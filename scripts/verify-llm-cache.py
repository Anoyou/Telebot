#!/usr/bin/env python3
"""核对 TelePilot 经 Magpie 网关的会话缓存效果。

用法:
  python3 scripts/verify-llm-cache.py                    # 看全部可见范围
  python3 scripts/verify-llm-cache.py 2026-10-16T00:00   # 只看该时间之后的调用

环境变量:
  MAGPIE_BASE  默认 http://127.0.0.1:3425
  CALLER_NAME  默认 telepilot
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime

BASE = os.environ.get("MAGPIE_BASE", "http://127.0.0.1:3425")
CALLER = os.environ.get("CALLER_NAME", "telepilot")
API = f"{BASE}/v1/magpie/usage/requests"
MAX_ROWS = 4000
since_raw = sys.argv[1] if len(sys.argv) > 1 else ""


def get(url):
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.load(resp)


def parse_ts(value):
    """把 API 的 t / 命令行 since 解析成可比较的 datetime；解析不了返回 None。

    兼容 ISO-8601（含 Z 后缀与空格分隔符）与 epoch 秒；解析失败时由调用方
    决定保留该行，避免因格式差异静默丢数据。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


head = get(f"{API}?limit=1")
key = next((k for k in head.get("callerKeys", []) if k.get("callerKeyName") == CALLER), None)
if not key:
    print(f"没有找到 caller key「{CALLER}」的调用记录。")
    raise SystemExit(0)
kid = key.get("id") or key.get("callerKeyId")
if kid is None:
    # 两个字段都缺失时不能拿 callerKey=None 去查询——那只会得到空结果，
    # 打印“没有调用”误导排查。
    print(f"caller key「{CALLER}」缺少 id / callerKeyId 字段，无法按 key 过滤，请核对网关 API 结构。")
    raise SystemExit(1)
print(f"callerKey {CALLER} | 周期内 {key.get('calls')} 次 | cache_read {key.get('cache_read')} | errors {key.get('errors')}")

rows, offset = [], 0
truncated = False
while True:
    d = get(f"{API}?callerKey={kid}&limit=500&offset={offset}")
    page = d.get("rows", [])
    if not page:
        break
    rows.extend(page)
    offset += len(page)
    total = d.get("total")
    if isinstance(total, int) and offset >= total:
        break
    if len(rows) >= MAX_ROWS:
        truncated = True
        break

since_dt = parse_ts(since_raw)
if since_raw and since_dt is None:
    print(f"警告：since 参数「{since_raw}」无法解析为时间，将被忽略。")
    since_dt = None

seen, calls = set(), []
for r in rows:
    # 去重口径：同秒同会话同 token 计数的两次真实调用会被误合并——诊断脚本可接受，
    # 若需精确计数请改为带请求 id 的键。
    k = (r.get("t"), r.get("session"), r.get("model"), r.get("in"), r.get("out"), r.get("status"))
    if k in seen:
        continue
    seen.add(k)
    if since_dt is not None:
        row_dt = parse_ts(r.get("t"))
        # 行时间解析失败时保留该行，宁可多算也不静默过滤。
        if row_dt is not None and row_dt < since_dt:
            continue
    calls.append(r)
calls.sort(key=lambda r: str(r.get("t")))

label = f"（since {since_raw}）" if since_dt else "（全部可见范围）"
print(f"\n去重后调用: {len(calls)} 条 {label}")
if truncated:
    print(f"警告：已达 {MAX_ROWS} 条抓取上限，以下统计基于截断数据。")
if not calls:
    print("该范围内没有调用。先在服务器上触发几次对话，再跑本脚本。")
    raise SystemExit(0)

sess = defaultdict(list)
for r in calls:
    sess[r.get("session") or "(none)"].append(r)
multi = [s for s, rs in sess.items() if len(rs) >= 2]
print(f"会话总数: {len(sess)} | 含 >=2 次调用的会话: {len(multi)}")
print("参考基线：修复前 114 条调用散成 98 个会话，绝大多数会话只有 1 次调用。\n")
print("调用最多的会话（时间 | in | cache_read | status）:")
for s, rs in sorted(sess.items(), key=lambda kv: -len(kv[1]))[:5]:
    print(f"  {s[:8]}")
    for r in rs[:10]:
        print(f"    {str(r.get('t'))[:19]} | in {r.get('in') or 0:>6} | cr {r.get('cache_read') or 0:>6} | {r.get('status')}")
    if len(rs) > 10:
        print(f"    ... 其余 {len(rs) - 10} 条")

ti = sum(r.get("in") or 0 for r in calls)
tc = sum(r.get("cache_read") or 0 for r in calls)
tw = sum(r.get("cache_write") or 0 for r in calls)
hit_rows = sum(1 for r in calls if (r.get("cache_read") or 0) > 0)
print(f"\n含缓存读的调用: {hit_rows}/{len(calls)}")
print(f"input 合计 {ti} | cache_read 合计 {tc} | cache_write 合计 {tw}")
if ti + tc:
    print(f"缓存读占提示词比例: {tc / (ti + tc) * 100:.1f}%  （in 已不含缓存读，口径为 cache_read/(in+cache_read)）")

print("\n最近 12 条:")
for r in calls[-12:]:
    sid = (r.get("session") or "")[:8] or "-"
    model = str(r.get("model") or "")[:20]
    print(
        f"{str(r.get('t'))[:19]} | sess {sid} | in {r.get('in') or 0:>6} | cr {r.get('cache_read') or 0:>6} "
        f"| cw {r.get('cache_write') or 0:>4} | {model:20s} | {r.get('ep')} | {r.get('status')}"
    )
