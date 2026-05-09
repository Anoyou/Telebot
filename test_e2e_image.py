#!/usr/bin/env python3
"""一键端到端测试：从 grok-bridge 抓取当前页面图片 → 发送到 Telegram。

用法:
  1. 在 Safari 前台打开一个有 Grok 图片的对话
  2. grok-bridge 以 --shared-tab 模式运行
  3. python3 test_e2e_image.py --chat me
"""
import argparse, asyncio, base64, json, sys, os, urllib.request

GROK_URL = "http://127.0.0.1:19998"

def fetch_snapshot():
    with urllib.request.urlopen(f"{GROK_URL}/snapshot", timeout=15) as r:
        return json.loads(r.read())

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat", required=True)
    parser.add_argument("--grok-url", default=GROK_URL)
    args = parser.parse_args()

    # 1. 从 grok-bridge 获取图片
    print("📸 从 grok-bridge /snapshot 获取图片...")
    snap = fetch_snapshot()
    images = snap.get("images", [])
    if not images:
        print("❌ 当前页面没有图片。请确保 Safari 前台是有图片的 Grok 对话。")
        sys.exit(1)

    print(f"  找到 {len(images)} 张图片")
    img_bytes_list = []
    for i, img in enumerate(images):
        d = img.get("data", "")
        if d and ";base64," in d:
            b64 = d.split(";base64,", 1)[1]
            raw = base64.b64decode(b64)
            img_bytes_list.append(raw)
            print(f"  图片{i+1}: ✅ base64 解码 {len(raw):,} bytes")
        else:
            print(f"  图片{i+1}: ❌ 无 base64 数据")

    if not img_bytes_list:
        print("❌ 没有可用的 base64 图片数据")
        sys.exit(1)

    # 2. 通过 Telethon 发送
    print("\n🔌 连接 Telegram...")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
    from app.settings import settings
    from app.crypto import decrypt_bytes, decrypt_str
    from app.db.models.account import Account, ACCOUNT_STATUS_ACTIVE
    from app.worker.tg_client import build_proxy_tuple
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(settings.database_url_sync)
    with Session(engine) as s:
        acct = s.query(Account).filter(Account.status == ACCOUNT_STATUS_ACTIVE).first()
        if not acct:
            acct = s.query(Account).first()
        if not acct:
            print("❌ 没有找到账号"); sys.exit(1)
        print(f"  账号: {acct.display_name or acct.phone}")
        session_str = decrypt_bytes(acct.session_enc).decode()
        api_id = int(decrypt_str(acct.api_id_enc))
        api_hash = decrypt_str(acct.api_hash_enc)

    proxy = None
    try: proxy = build_proxy_tuple(None)
    except: pass

    client = TelegramClient(StringSession(session_str), api_id, api_hash, proxy=proxy, request_retries=3)
    await client.connect()
    me = await client.get_me()
    print(f"  已连接: {me.first_name}")

    # 3. 发送（用 BytesIO 包装 + .name 属性，让 Telethon 识别为图片而非无名文件）
    print(f"\n📤 发送 {len(img_bytes_list)} 张图片到 {args.chat}...")
    import io
    for i, img_bytes in enumerate(img_bytes_list[:3]):
        buf = io.BytesIO(img_bytes)
        # 从 data URI 中推断扩展名
        ext = ".jpg"
        if i < len(images):
            d = images[i].get("data", "")
            if "image/png" in d[:30]:
                ext = ".png"
            elif "image/webp" in d[:30]:
                ext = ".webp"
        buf.name = f"grok_image{ext}"
        caption = f"🧸 grok-bridge → Telegram 端到端测试 (图{i+1})" if i == 0 else ""
        try:
            msg = await client.send_file(args.chat, buf, caption=caption)
            print(f"  ✅ 图{i+1} 发送成功 (msg_id={msg.id}, name={buf.name})")
        except Exception as e:
            print(f"  ❌ 图{i+1} 发送失败: {e}")

    await client.disconnect()
    print("\n🎉 端到端测试完成！")

if __name__ == "__main__":
    asyncio.run(main())
