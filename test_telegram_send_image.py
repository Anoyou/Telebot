#!/usr/bin/env python3
"""Telegram 发图测试 —— 模拟 command.py 的 base64 → send_file 流程。

这个脚本从 telebot 的数据库中读取账号信息，创建 Telethon 客户端，
下载一张公开测试图片，然后发送到指定聊天。

用法:
  1. 确保 telebot 后端正在运行（需要数据库和 .env）
  2. python3 test_telegram_send_image.py --chat <chat_id_or_username>
     例如: python3 test_telegram_send_image.py --chat me
     例如: python3 test_telegram_send_image.py --chat @someone

可选:
  --account-id   指定账号 ID（默认取第一个在线账号）
  --image-url    自定义测试图片 URL
  --base64       直接从 base64 data URI 发送（不走 HTTP 下载）
"""

import argparse
import asyncio
import base64
import io
import sys
import os

# 添加 telebot 后端到 Python 路径
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "backend")
sys.path.insert(0, BACKEND_DIR)


async def main():
    parser = argparse.ArgumentParser(description="Telegram 发图测试")
    parser.add_argument("--chat", required=True, help="目标聊天 ID 或用户名 (如 'me' 或 '@someone')")
    parser.add_argument("--account-id", type=int, help="账号 ID（默认取第一个在线账号）")
    parser.add_argument("--image-url", default="https://httpbin.org/image/jpeg", help="测试图片 URL")
    parser.add_argument("--base64", dest="b64_data", help="直接使用 base64 data URI（跳过下载）")
    args = parser.parse_args()

    # ── 1. 从 telebot 获取账号信息 ──
    print("📋 从 telebot 数据库读取账号信息...")

    try:
        from app.settings import settings
        from app.crypto import decrypt_bytes, decrypt_str
        from app.db.models.account import Account, ACCOUNT_STATUS_ACTIVE
        from app.worker.tg_client import build_client
    except ImportError as e:
        print(f"❌ 无法导入 telebot 模块: {e}")
        print("   请确保在 telebot 项目目录下运行，或 telebot 后端已安装")
        sys.exit(1)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    # 使用同步引擎查询（避免复杂的 async session 设置）
    db_url = settings.database_url_sync
    engine = create_engine(db_url)

    with Session(engine) as session:
        if args.account_id:
            account = session.get(Account, args.account_id)
        else:
            # 取第一个在线账号
            account = session.query(Account).filter(
                Account.status == ACCOUNT_STATUS_ACTIVE
            ).first()

        if not account:
            # 如果没有在线账号，取任意一个
            account = session.query(Account).first()

        if not account:
            print("❌ 数据库中没有找到任何账号")
            print("   请先在 telebot 前端添加账号")
            sys.exit(1)

        print(f"  找到账号: {account.display_name or account.phone} (ID: {account.id})")

        # 解密 session
        session_str = decrypt_bytes(account.session_enc).decode()
        api_id = int(decrypt_str(account.api_id_enc))
        api_hash = decrypt_str(account.api_hash_enc)

    # ── 2. 创建 Telethon 客户端 ──
    print("🔌 创建 Telethon 客户端...")
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    # 获取代理配置
    proxy = None
    try:
        from app.worker.tg_client import build_proxy_tuple
        proxy = build_proxy_tuple(None)
    except Exception:
        pass

    client = TelegramClient(
        StringSession(session_str),
        api_id,
        api_hash,
        proxy=proxy,
        request_retries=3,
        connection_retries=5,
    )

    await client.connect()
    if not await client.is_user_authorized():
        print("❌ 账号未授权（session 可能已失效）")
        await client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    print(f"  已连接: {me.first_name} (@{me.username or 'N/A'})")

    # ── 3. 获取测试图片 ──
    print("🖼️  准备测试图片...")

    if args.b64_data:
        # 直接使用 base64 data URI（模拟 grok-bridge 返回的格式）
        print("  使用提供的 base64 data URI")
        if ";base64," in args.b64_data:
            b64_part = args.b64_data.split(";base64,", 1)[1]
        else:
            b64_part = args.b64_data
        img_bytes = base64.b64decode(b64_part)
        print(f"  base64 解码: {len(img_bytes):,} bytes")
    else:
        # 下载公开测试图片
        print(f"  下载测试图片: {args.image_url}")
        import httpx

        # 处理代理
        img_proxy = None
        for ek in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            ev = os.environ.get(ek)
            if ev:
                img_proxy = ev
                break

        dl_kwargs = {"timeout": httpx.Timeout(30.0, connect=10.0)}
        if img_proxy:
            dl_kwargs["trust_env"] = False
            dl_kwargs["mounts"] = {"all://": httpx.AsyncHTTPTransport(proxy=img_proxy)}
        else:
            dl_kwargs["trust_env"] = False

        async with httpx.AsyncClient(**dl_kwargs) as dl_cli:
            resp = await dl_cli.get(args.image_url)
            if resp.status_code != 200:
                print(f"❌ 下载失败: HTTP {resp.status_code}")
                await client.disconnect()
                sys.exit(1)
            img_bytes = resp.content
        print(f"  下载完成: {len(img_bytes):,} bytes")

    # ── 4. 模拟 command.py 的 base64 解码流程 ──
    # 将图片转为 base64 data URI 再解码回来，模拟完整管线
    print("\n🔄 模拟 base64 → bytes 解码流程（与 command.py 一致）...")

    # 检测 MIME
    mime = "image/jpeg"
    if img_bytes[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
        mime = "image/webp"

    # 模拟 grok-bridge 返回的 data URI 格式
    data_uri = f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"

    # 模拟 command.py 的解码逻辑
    if data_uri.startswith("data:") and ";base64," in data_uri:
        b64_part = data_uri.split(";base64,", 1)[1]
        decoded_bytes = base64.b64decode(b64_part)
        if len(decoded_bytes) > 100:
            print(f"  ✅ base64 解码成功: {len(decoded_bytes):,} bytes")
            img_bytes = decoded_bytes  # 使用解码后的数据
        else:
            print(f"  ❌ 解码后数据太小: {len(decoded_bytes)} bytes")
            await client.disconnect()
            sys.exit(1)
    else:
        print("  ❌ data URI 格式异常")
        await client.disconnect()
        sys.exit(1)

    # ── 5. 通过 Telethon 发送图片 ──
    print(f"\n📤 发送图片到 {args.chat}...")

    try:
        # 用 BytesIO 包装 + .name 属性，让 Telethon 识别为图片而非无名文件
        import io as _io
        buf = _io.BytesIO(img_bytes)
        buf.name = f"test_image{'.png' if 'png' in mime else '.jpg'}"
        result = await client.send_file(
            args.chat,
            buf,
            caption="🧪 Telegram 发图测试 — 如果你看到这张图，说明 base64 → send_file 流程正常！",
        )
        print(f"  ✅ 发送成功！消息 ID: {result.id}")
    except Exception as e:
        print(f"  ❌ 发送失败: {type(e).__name__}: {e}")
        await client.disconnect()
        sys.exit(1)

    # ── 6. 清理 ──
    await client.disconnect()
    print("\n🎉 测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
