# Code Review — Telegram Userbot Management System (opus-4.7)

Reviewed in two rounds against the materials in `review-pkg/`:

- **Round 1 — Backend** (`round1-backend.md`, ~95 files, FastAPI + Telethon + SQLAlchemy async + PostgreSQL + Redis)
- **Round 2 — Frontend & Deployment** (`round2-frontend.md`, React + TS + Vite + shadcn/ui, plus docker-compose / Makefile)

Severity legend: **Critical** (data loss / RCE / privilege escalation), **Major** (security weakness, reliability bug, or design hole that will bite under load), **Minor** (correctness / UX / DX), **Suggestion** (style, hygiene).

Findings reference file paths inside the project (the line numbers cited are the lines inside the reviewed source file, not the review-pkg offset). I read the files I considered most security- or reliability-critical in full; coverage for the long tail of CRUD endpoints is sample-based.

---

## Round 1 — Backend

### Critical

#### 1. **Plugin install runs untrusted code BEFORE signature verification** — `services/plugin_install_service.py:install_zip` (lines 9586–9650)

`install_zip()` calls `parse_zip()` first; `parse_zip()` writes the user-uploaded ZIP to disk and immediately calls `_load_manifest_from_path()`, which does:

```python
spec.loader.exec_module(mod)   # line 9521
```

Only **after** that succeeds does `install_zip()` invoke `verify_signature(zip_bytes, signature, settings.plugin_pubkey)`. So an unsigned or maliciously signed plugin **already executed arbitrary Python in the FastAPI backend process** by the time the signature check fires. The Ed25519 check therefore protects nothing on the install path — a malicious manifest can read `MASTER_KEY` / `JWT_SECRET` from `os.environ`, exfiltrate over network, write to other plugin dirs, monkey-patch in-memory objects, etc.

**Fix.** Verify signature on the raw bytes **before** `parse_zip()` executes any user code. If the public key is configured (`settings.plugin_pubkey` non-empty) and verification fails or signature is missing, refuse the upload with HTTP 403 and never extract. Loading `manifest.py` should also be moved into the worker subprocess, not the API process — the API has DB credentials in scope; a worker only has its own session.

Bonus issue: `_validate_zip_members` (line 9463) only blocks absolute paths and `..`. Python's `zipfile.extractall` will also extract symlinks if the host platform allows it (since Python 3.12 `zipfile` extracts file data only, but a packaged tarball-style symlink can still bite). Consider iterating members and rejecting anything where `external_attr >> 16` is `S_IFLNK`.

---

#### 2. **Plugin sandbox is not a sandbox** — `worker/plugins/sandbox.py` + `worker/plugins/loader.py`

`SandboxClient.__getattr__` (line 13317) is the only enforcement point, and the docstring is candid ("不能阻挡恶意插件"). But the rest of the system markets installed third-party plugins as if there were a meaningful boundary. Easy escapes within the worker process:

- `client.__class__.__getattribute__(client, "send_message")` — bypasses `__getattr__`.
- `client._real` is left accessible (line 13319: any leading-underscore attribute except a small allowlist falls through with `getattr(self._real, name)`).
- The plugin runs in the **same Python interpreter** as Telethon, asyncpg, the LLM key-handling code, and the Redis IPC channels. It can `import os`, `import sys`, read `/proc/self/environ`, write to `data/plugins/installed/<other-key>/`, redefine the global IPC channels by mutating module attributes, etc.
- MTProto raw API (`__call__`) is documented as not intercepted at all.

**Fix.** Either (a) drop the sandbox terminology and surface a hard "third-party plugins run with full Python privileges; install only signed code from sources you trust" warning in the UI on every install/enable, or (b) actually isolate plugins. (a) is realistic for V1; (b) requires a separate worker subprocess per plugin with its own restricted account proxy. Right now the UX implies safety where there is none, which is the dangerous combination.

At minimum, plug the trivial leaks: do not return `_real` on underscore attributes, intercept `__call__`, and forbid `__class__` lookups.

---

#### 3. **Timing oracle for username enumeration on `/api/auth/login`** — `api/auth.py:438–476`

```python
user = (...).scalar_one_or_none()
if not user or not auth_service.verify_password(req.password, user.password_hash):
    raise _err("AUTH_INVALID", ...)
```

When `user is None`, `verify_password` is short-circuited, so the request returns in microseconds. When the user exists, Argon2 verification adds ~100ms. The attacker now has a precise oracle to enumerate valid usernames even though the error code is uniform. This entirely defeats the comment on line 457 ("用户不存在或密码错都返回相同错误，避免账号枚举").

**Fix.** Always run `_hasher.verify` against a sentinel hash when the user is missing, or precompute a static dummy Argon2 hash at module import and burn the same CPU on the negative path. Also consider rejecting the request only after a constant-time delay.

This is single-tenant ("one super-admin"), so the attack value is small — but the rate limiter alone (30/min) is not constant-time and will not save you against a determined attacker who can wait.

---

#### 4. **Background log/event consumers can lose data on DB outage** — `worker/supervisor.py:_consume_runtime_log` and `_consume_ratelimit_event`

The pattern is `BLPOP → LRANGE 0..BATCH-1 → LTRIM len(more)..-1 → db.add_all + commit`. Items are removed from the Redis list **before** commit. If the DB is down or the SQL fails:

```python
except Exception as e:
    log.exception("...")
    await asyncio.sleep(1)
```

The trimmed items are gone forever. For audit-relevant rows (rate-limit events, runtime errors) this is a silent data loss path that's invisible in logs except a generic exception line.

**Fix.** Use `LMOVE`/`BLMOVE` to a sibling "in-flight" list, only `LREM` after successful commit; on retry, scan the in-flight list and re-push. Or migrate to actual Redis Streams (`XREAD GROUP`/`XACK`) which is what the variable name `RUNTIME_LOG_STREAM` was already pretending. The current implementation is a list, not a stream, which is a confusing naming mismatch.

Also: the two consumers are word-for-word duplicates with one type swapped — extract a generic helper.

---

### Major

#### 5. **`MASTER_KEY` rotation has no story** — `crypto.py` + `settings.py:10348`

Single Fernet key, no `MultiFernet` wrapping, no key version field on encrypted columns. If the operator ever needs to rotate (suspected leak, employee turnover), they have to: stop service, re-encrypt every `session_enc` / `api_id_enc` / `api_hash_enc` / `password_enc` / `totp_secret_enc` / `api_key_enc` row, restart. There is no migration tool committed.

**Fix.** Replace `Fernet(...)` with `MultiFernet([Fernet(current), Fernet(previous), ...])`, parse a `MASTER_KEYS` (plural, comma-separated) env var. `MultiFernet.rotate(token)` lets you re-encrypt lazily on read. Document the rotation procedure in `agent-plans/`.

#### 6. **JWT has no revocation, no `jti`, no logout-everywhere** — `services/auth_service.py` + `api/auth.py:558–589`

`change-password` clears the cookie on the calling client only. Other already-issued tokens stay valid until their 12-hour `exp`. There is no token denylist or `password_changed_at` claim. Anyone who has a stolen cookie keeps full access until `iat + 12h`.

**Fix.** Embed `pwd_v` (an integer bumped on password change) in the JWT, check it in `decode_jwt_token` against the user row. Or store a signed-in `jti` in DB / Redis with a revocation set.

Also missing on the JWT itself: `iss`, `aud`, `kid`. HS256 with a single secret in `settings.jwt_secret` means a leaked env file = full account takeover with no rotation lever. Consider asymmetric (RS256/EdDSA) so the verifying secret can be split or compromised more gracefully.

#### 7. **Login `_PENDING` brute-force window is 30 min × no per-token attempt cap** — `services/login_service.py`

`confirm_code` re-uses the same Telethon client across attempts with no counter. Telegram's own rate limiting will eventually trigger `FloodWaitError`, but a 5-digit numeric code is small enough that a determined attacker can run thousands of attempts before TG cuts them off — particularly if `proxy_id` is set. There's also no per-IP cap on `/login/code`, only on `/login` (the username/password endpoint).

**Fix.** Track `attempts` on the `_PendingLogin` dataclass and `_cleanup` after, say, 5 failures; apply the same Redis rate-limit middleware used for password login to the three login wizard endpoints.

#### 8. **No CSRF protection at all** — `api/auth.py` cookie config + `main.py` middleware stack

Cookie is `HttpOnly`, `SameSite=Lax`, `Secure` optional. Lax provides decent protection for simple state-changing requests but **does not block top-level GET-induced state changes** (e.g., `<img src="https://api/api/auth/logout">`) or in-browser `fetch` from popups. The app uses POST/PATCH/DELETE for mutations (good), so Lax + the JSON content-type requirement covers most cases — but `change-password` and `totp/disable` should probably require an explicit CSRF token or a custom header that the browser would not auto-attach (`X-Requested-With: telebot-ui`).

**Fix.** Add a small middleware that requires a custom header on all non-GET routes, or implement double-submit token. Cookie-based auth without CSRF protection has bitten too many shops.

#### 9. **`trust_forwarded_for=False` with a proxy in front silently shares one rate-limit bucket across all clients** — `api/auth.py:_client_ip` + `settings.py:10359`

Default is "do not trust XFF; use `request.client.host`." When deployed behind nginx (per `docker-compose.yml`), `request.client.host` becomes nginx's IP. So all clients hash to the same `auth_rl:ip:<nginx-ip>` bucket. The 30/minute cap then becomes a global cap, easily abused for denial-of-service. The default does not match the intended deployment.

**Fix.** Either default `trust_forwarded_for=True` and document that it requires the deploy to set `proxy_protocol`/`real_ip_module` correctly, or detect the situation at startup (warn if `cors_origins` looks like a hostname but `trust_forwarded_for=False`). At minimum, document the mismatch loudly in `.env.example` and the docker-compose comments.

#### 10. **`pending_totp` cookie carries plaintext base32 secret** — `api/auth.py:512–530`

The TOTP secret is set as a 5-minute `HttpOnly` `SameSite=Lax` cookie. Even though it never reaches JS, it is now persisted on disk in the user's browser cookie store, transmitted over the wire on every request to the same origin during those 5 minutes (cookie is sent on **every** request, not just `/totp/verify`), and survives a tab close. If `cookie_secure=False` (the default for local HTTP), it crosses the wire in cleartext.

**Fix.** Keep pending TOTP secrets in a per-user Redis key with 5-minute TTL, indexed by `user.id`. Or in an in-memory dict keyed by user ID with a TTL sweeper (the same `cleanup_expired_loop` pattern). Don't ship secrets through cookies even briefly.

#### 11. **`worker/runtime.py` runs DB writes from worker that the docs say should be log-only** — `runtime.py:14637–14653`

The module docstring claims "所有 DB 写操作由主进程统一处理（消费 Redis stream）；worker 只读 DB"。But `run_worker` writes back `tg_user_id` / `tg_username` directly via `db.commit()` after `client.get_me()`. This is a bug *or* the docstring is wrong; either way, the contract is violated.

**Fix.** Pick one. If workers are allowed to write specific fields, document the allowlist; otherwise convert this to a "publish profile_update event → main process consumer writes."

#### 12. **`_listen_global` and `_listen_cmd` on supervisor have no graceful unsubscribe failure mode** — `worker/supervisor.py:_listen_global`

`pubsub.listen()` is an infinite async iterator. When the surrounding task is cancelled, the `finally` calls `unsubscribe` and `close` — but if the Redis connection has dropped, both can hang or raise. There's no timeout, so `stop_all_workers()` may block on shutdown.

**Fix.** Wrap unsubscribe/close in `asyncio.wait_for(..., timeout=2)` and swallow any final error.

#### 13. **`alembic upgrade head` at startup races multi-replica deployments** — `main.py:5179`

The default is `auto_migrate_on_startup=True`. The README/docker-compose says `--workers 1`, but operators running two web replicas (HA) will run two migrations concurrently. PostgreSQL's `alembic_version` table has a single row, so the second one will either error or — worse — try to apply both and double-write.

**Fix.** Either grab a Postgres advisory lock around the alembic upgrade (`SELECT pg_advisory_lock(<int>)`) so only one replica wins, or default to `False` for production-like envs. The current docstring mentions multi-instance, but the default still bites.

#### 14. **CORS uses `allow_methods=["*"], allow_headers=["*"]` with credentials** — `main.py:5219–5225`

Combined with a configured allowlist (`cors_origin_list`), this is mostly OK. But `allow_headers=["*"]` actually disables the preflight whitelist for custom headers, weakening any future CSRF defense. Pin the headers you actually use.

#### 15. **`redis_client.get_pool()` race** — `redis_client.py:5361–5369`

```python
def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis_async.ConnectionPool.from_url(...)
    return _pool
```

Two concurrent `get_redis()` callers on cold start each create a pool, then the second's pool wins; the first leaks. Asyncio is single-threaded so this is small, but `async def` boundaries can interleave around `from_url` if it ever awaits in the future.

**Fix.** Move pool creation into FastAPI startup lifespan, or use `asyncio.Lock` around the create.

#### 16. **`account_service.delete_account` re-decrypts session for log_out without disconnecting on failure** — line 6445

`_logout_best_effort` builds a TelegramClient, presumably calls `client.connect()` then `client.log_out()`. If `connect()` raises (network down), the client is left half-built and never `disconnect`-ed. Build it once, wrap in `try/finally` with `disconnect`. (Hidden behind the snippet I read; double-check the implementation.)

#### 17. **Avatar endpoint trusts caller-controlled path indirectly** — `api/accounts.py:get_avatar`

`account_service.ensure_avatar` builds the path from `aid` (numeric) only — that's safe. But `FileResponse(str(path), media_type="image/jpeg", ...)` always reports `image/jpeg` even if the actual file is a PNG. Telethon's `download_profile_photo` returns whatever TG had. Browsers will mostly cope, but mismatched `Content-Type` can defeat caching/CSP/etc. — minor.

### Minor

#### 18. **`Fernet(settings.master_key.encode())` is lazy and only fails at first use** — `crypto.py:_get_fernet`

If the operator typos the key, the API stays up, then the first login attempt blows up with a 500. Validate the key at startup (call `_get_fernet()` from `lifespan`) so the deploy fails fast instead of "looking healthy" until the first user tries to log in.

#### 19. **`audit.py:write` does not commit but takes `db`** — `services/audit.py:6626`

The contract is "caller commits." Every API endpoint correctly calls `await db.commit()` after — but a few audit calls follow `await db.commit()` then `await db.commit()` again (e.g., `auth.py:469–470` is fine, but `plugins.py:1908–1910` does `await db.commit(); await audit.write(...); await db.commit()` which results in audit row sometimes living in a separate transaction depending on autoflush behaviour). Pick a convention and stick to it. Re-call `commit` with no pending writes is a no-op so behaviour is correct, but the duplicate hurts readability.

#### 20. **Argon2 default parameters are 2024-conservative; not configurable** — `auth_service.py:6667`

`PasswordHasher()` with stock parameters is fine for now, but with no env knob you can't dial it up if hardware moves. Add `settings.argon2_memory_kb` etc. for future-proofing.

#### 21. **`_local_rl_check` uses `time.time()` (wall clock)** — `auth.py:347–365`

A backwards clock jump (NTP slew, DST? unlikely for monotonic, but containers do skew) would corrupt the bucket window. Use `time.monotonic()`.

#### 22. **`login_service.start_login` keeps `api_hash` in plaintext memory until finalize**

Documented (line 9050-ish), and unavoidable for the Telethon flow. No fix needed; flagged so a reader knows it's intentional.

#### 23. **Worker `_dispatch` snapshot iteration** — `loader.py:_dispatch` line 12682

`for fkey, inst in list(state.instances.items())` — copy is correct. But `state.contexts.get(fkey)` is fetched fresh each iteration; if `reload_account_config` removes a key mid-dispatch, `ctx` is `None` and the message is silently dropped. Logging a debug line would help diagnose mysterious drops.

#### 24. **`update_account` doesn't trigger worker reload after `proxy_id` / `template_id` change** — `services/account_service.py:update_account`

A user changes the proxy and the running worker keeps using the old one until restart. Either (a) pause+resume the worker, (b) document that proxy/template changes need a restart, or (c) add a new IPC message `CMD_RELOAD_NETWORK`.

#### 25. **`web_user` schema implies single user, but no `is_active`/`is_admin`** — `db/models/user.py`

There's no soft-delete, no role; "single super-admin" is an unwritten constraint enforced only by `register` refusing when count > 0. If anyone manually inserts a row in DB, the second user gets full admin rights. Add a `role` column or at least audit insertions.

#### 26. **`generate_master_key()` lives in `crypto.py` next to runtime code** — `crypto.py:4053`

Move CLI-only helpers to a `scripts/keygen.py`. Otherwise Fernet's `generate_key` is ergonomically next to the encryption code, tempting someone to call it from a request handler.

#### 27. **`llm_client.py` still embeds the API key in error strings** — `_safe_error_message` is called but the function logic isn't visible in the dump

The intent is good; verify by code inspection that the regex/scrub strips Bearer tokens, `sk-...`, `x-api-key` headers, and any base URL parameters that contain credentials. If it relies on the literal `api_key` string match, a malformed proxy URL like `https://user:secret@proxy/...` will leak through.

### Suggestion

#### 28. Settings schema: add `Field(min_length=44)` for `master_key` to fail at parse time, not at first decrypt.

#### 29. The duplicated `_consume_runtime_log` / `_consume_ratelimit_event` should become a single generic `_drain_redis_list_to_db(stream_name, model_factory)`.

#### 30. The "fail-degraded" fallback for the rate limiter in `auth.py:368–406` mixes `try / except / log.warning` with a fresh `_local_rl_check` for *each* key, which means: on Redis fail, the limit applies to the *first* key and then the *second* key independently in local buckets. The behaviour is correct but the control-flow is hard to read; extract a helper.

#### 31. `cleanup_expired_loop` does not explicitly handle the case where `_PENDING` grows unbounded between sweeps under attack (each `start_login` allocates a Telethon client and a 30-minute slot). Add an upper-bound (`if len(_PENDING) >= MAX: refuse`).

#### 32. Missing tests inferred from review: there are none for `parse_zip` symlink/zip-bomb/large-file behaviours, none for JWT edge cases (`alg=none` regression, expired tokens), none for `verify_signature` with non-Ed25519 keys.

#### 33. `worker/supervisor.py` exponential backoff uses an array `[5, 10, 20, 60, 300]` (line 15102) but `min(h.fail_count - 1, len(_BACKOFF) - 1)` — when `fail_count == len(_BACKOFF)+1`, the comparison `h.fail_count > len(_BACKOFF)` (line 15302) flags as dead. So the largest backoff (300s) is used exactly once before deadlock. Either lengthen the array or change the predicate to `>= len * 2` so each tier gets repeated.

#### 34. `tg_client.py:build_client` calls `decrypt_str(account.api_id_enc)` and `decrypt_str(account.api_hash_enc)` — if either field was encrypted with a previous master key, the worker dies on connect. Wrap these and emit a clearer error so operators understand they need to re-bind.

---

## Round 2 — Frontend & Deployment

The frontend is well-organized: shadcn/ui primitives, React Query for state, axios with interceptor for 401, sonner for toasts, react-router for routing. PWA via vite-plugin-pwa. Findings below are sparser than the backend because the security-critical surface area is much smaller — the backend does the work; the frontend reflects it.

### Critical

(None.) The frontend correctly delegates auth to HttpOnly cookies, doesn't store tokens in localStorage, and reads `auth_token` only via the implicit cookie. No XSS sinks I can find with `dangerouslySetInnerHTML`.

### Major

#### F1. **`api.ts` interceptor uses `location.href = "/login"` — drops React Query cache, breaks SPA navigation, and races multiple in-flight requests** — `lib/api.ts:3794–3803`

```js
if (status === 401 && !location.pathname.startsWith("/login")) {
  location.href = "/login";
}
return Promise.reject(err);
```

Three issues:
1. Hard navigation throws away in-memory state; if the user typed something into a form, it's gone with no warning.
2. If 5 queries on a page race, all 5 see 401, all 5 set `location.href`. Browsers usually coalesce, but this can cause weird interleaved logging.
3. `location.pathname.startsWith("/login")` won't help if you ever introduce a `/login-help` route — fragile prefix check.

**Fix.** Use `react-router`'s navigate via a small auth-aware wrapper: invalidate `auth/me` query, redirect once via `navigate("/login", { replace: true })`. Use a module-level boolean `redirecting` to coalesce concurrent 401s.

#### F2. **PWA service worker may serve stale `/api/...` responses on flaky network** — `pwa.ts` + `vite.config` (not in dump, inferred)

vite-plugin-pwa's default `generateSW` strategy will precache same-origin assets and (often) network-first or stale-while-revalidate API responses. If the service worker is configured with default routing, mutating endpoints (POST /api/auth/login, etc.) could be cached or replayed. The dump doesn't show `vite.config.ts`, so I cannot verify.

**Fix.** Audit `vite.config.ts` for `workbox.runtimeCaching` and ensure all `/api/*` is `NetworkOnly`. Also clear the SW on logout via `registration.unregister()` or it will keep indexing the path of the previously logged-in user.

#### F3. **`RequireAuth.tsx` shows a generic redirect screen on error but doesn't trigger redirect itself** — line 3006–3012

It relies entirely on the axios interceptor to do the navigation. If the interceptor logic ever changes (e.g., the `/login` prefix check fails), the user lands on a "正在跳转到登录…" page that never moves. Add an explicit `<Navigate to="/login" />` here as a belt-and-suspenders.

#### F4. **`fetchMe` is called in both `RequireAuth` and `UserAccount`** with separate query keys (both `["auth", "me"]` — same key, OK) but at minimum 2× round trips on Login → first page load. Consider making `RequireAuth` `select`-able so children read from the same query.

#### F5. **`docker-compose.yml` has no resource limits, no read-only filesystem, no user namespacing** — lines 28–106

No `mem_limit`, no `cpus`, no `read_only: true`, no `cap_drop`, no `user: 1000:1000`. The `web` container runs as root by default (FastAPI image inheritance unknown, not in dump). For a system whose threat model includes "untrusted plugins running arbitrary Python," this is the perfect storm:
- Plugin escapes Python sandbox (#2 above).
- Now has root in container.
- Container has access to /var/run/docker.sock? (depends on host config, but I can't tell from compose.)
- `sessions:/app/sessions` volume → plugin can read all session bytes (already encrypted with master key, which is in env var of the same container).

**Fix.** Add `user: nobody`, `read_only: true` (with explicit `tmpfs` for working dirs), `cap_drop: [ALL]`, drop `host.docker.internal` access unless `TG_DEFAULT_PROXY` actually requires it, and document a pinned digest for `postgres:16-alpine` and `redis:7-alpine` (currently mutable tags).

#### F6. **No `Content-Security-Policy`, `X-Frame-Options`, `Strict-Transport-Security` headers** — frontend Dockerfile/nginx.conf not in dump

The dump shows that nginx is the static host but does not include the actual `nginx.conf`. Confirm that the prod nginx config sets at least:
- `Content-Security-Policy` (script-src 'self'; etc.)
- `Strict-Transport-Security` when behind HTTPS
- `X-Frame-Options: DENY`
- `Referrer-Policy: same-origin`

Without these, the cookie-based auth is a clickjacking target.

### Minor

#### F7. **`Login.tsx` "Show password" toggle leaves password visible in the input field's `type=text` until manual toggle** — line 8290–8327

Good comments about iOS IME, but if the user submits successfully and goes back, the password is still in plaintext on the rendered DOM until they toggle. Auto-revert to `password` after `onSuccess` of the mutation (or after 30s) — minor UX hygiene.

#### F8. **`Wizard.tsx` keeps `loginToken` in `useState`, lost on refresh** — line 6088

Documented inline ("仅放组件 state，刷新即丢失"). For a multi-step flow this is OK because the user has to start over, which matches the backend `_PENDING` model. But the wizard does not show a clear "session expired, restart" message — just a backend error. Add an explicit `LOGIN_TOKEN_EXPIRED` → "Login session expired, restart" UX.

#### F9. **`UserAccount.tsx:handleChange` validates client-side only** — lines 12483–12500

`if (newPwd.length < 8)` — backend should also enforce. If the backend has no minimum (I didn't see one in `auth.py:change_password` or `register`), then any tooling that bypasses the UI can set a 1-char password. Verify and add server-side validation.

#### F10. **Restart-worker UX is sleep-based** — `Detail.tsx:restartWorkerMut` line 4585

```js
await pauseAccount(aid);
await new Promise((r) => setTimeout(r, 1000));
await resumeAccount(aid);
```

Hardcoded 1-second sleep is a smell. The backend pause is async (publishes to Redis) so the worker may not have actually paused in 1s. Consider polling `account.status` until it transitions, or surface a "still pausing…" state. Same for the 5-second hard-coded refetch on line 4596.

#### F11. **`main.tsx` QueryClient `retry: 1`** — line 4027

Means every error (including legitimate 4xx like 401, 404, 422) triggers a retry. Already overridden in `RequireAuth` to `retry: false`, but leak elsewhere. Better default: `retry: (count, err) => err?.response?.status >= 500 && count < 1`.

#### F12. **`pwa.ts` toast on offline-ready uses `duration: Infinity` for "new version" toast** — line 12676

If the user dismisses, they get no further nudge until the next page load that detects an SW update. OK as documented, but the "Infinity" toast persisting across route changes is jarring; pin it to a header bar instead.

#### F13. **No error boundary anywhere in the React tree** — `App.tsx` / `main.tsx`

A render error in any leaf takes the whole app to the white-screen fallback. Add an `<ErrorBoundary>` around `<AppShell>` with a "reload page" button.

#### F14. **`api/accounts.ts:avatarUrl` builds a relative URL by string concat** — line 403–406

```js
const base = (api.defaults.baseURL || "").replace(/\/$/, "");
return `${base}/api/accounts/${aid}/avatar`;
```

If `VITE_API_BASE` is `http://localhost:8000/`, the result is `http://localhost:8000/api/...`. Fine. But `<img src>` won't send the auth cookie cross-origin without `crossOrigin="use-credentials"` on the img tag. If frontend and backend ever live on different origins, avatars break. Document the constraint or proxy via the SPA host (which the docker-compose does, OK).

#### F15. **`Logs.tsx` polls every 5s by default** — line 8424 + queries below (not shown in detail)

For a system that may have many accounts, 5s polling is fine but no upper bound on log row count or tail truncation is visible. Confirm backend caps result count; otherwise an "info-spammy" worker can produce a 50MB JSON response.

### Suggestion

#### F16. The `api/types.ts` is enormous (~750 lines) with many manual interfaces. The `Makefile` already has `codegen: pnpm codegen` (line 274) → pushing toward OpenAPI-generated types would eliminate drift.

#### F17. `pages/Settings/LLMProviders.tsx` has 1000+ lines. Split form vs table vs row dialogs into smaller files; right now the file blocks IDE intellisense.

#### F18. Frontend has no tests committed to the dump (no `*.test.ts(x)` shown). Add at least Vitest smoke tests for the auth flow.

#### F19. `docker-compose.yml` mounts `sessions:/app/sessions` as a named volume but the path is now likely `./data/avatars` and `./data/plugins/installed` per `settings.py`. Verify the volume covers all the persistent state, including the avatars dir and the installed-plugin dir; otherwise plugins disappear on container rebuild.

#### F20. The `Makefile` `nuke` target is destructive and has no `--yes` confirmation. Add at least an `@read -p "are you sure? "` gate.

---

## Cross-cutting observations

- **Documentation density is excellent.** Almost every module has a top-level docstring explaining design choices, trade-offs, and even Pythonic gotchas (e.g., why `spawn` not `fork`). This is unusual and welcome.
- **The contract between API process and worker process is fuzzy in places.** Workers should be log-only readers of DB; in practice they write small fields back. Tighten the boundary.
- **Threat model needs to be written down.** The plugin sandbox naming, the master-key-is-everything dependency, the single-super-admin assumption, the multi-replica caveats — all of these are scattered across module docstrings. A single `SECURITY.md` with the explicit threat model (and what is *not* defended) would prevent future contributors from over-trusting "the sandbox."
- **Reliability code is mostly defensive but a few critical paths short-circuit.** The Redis fallback in auth rate-limiting is good. The Redis-list consumer in supervisor is the opposite — it deletes data eagerly and hopes the DB write goes through.

---

## Top-priority fix list (recommended order)

1. Move signature verification **before** plugin manifest execution (Critical #1).
2. Constant-time username/password verification in `/login` (Critical #3).
3. Add `LMOVE`-based in-flight semantics to the runtime-log / rate-limit-event consumers (Critical #4).
4. Rewrite the plugin "sandbox" doc to match reality, OR build real isolation (Critical #2).
5. Add JWT revocation on password change (`pwd_v` claim) and add `iss`/`aud` (Major #6).
6. CSRF custom-header check on all mutating routes (Major #8).
7. Multi-Fernet support and a documented rotation procedure (Major #5).
8. `pending_totp` cookie → server-side ephemeral store (Major #10).
9. `auto_migrate_on_startup` Postgres advisory lock (Major #13).
10. Container hardening: `cap_drop`, non-root user, pinned image digests, CSP/HSTS headers in nginx (Major #F5, #F6).

---

*End of review.*
