"""worker 端的媒体抽取工具：图片 / 贴纸 / 相册 / 转发媒体引用过期 重试。

把这些"从 telethon Message 取出可识别字节"的细节集中放在这里，``command.py``
只调高层 ``collect_image_sources()`` / ``download_image_bytes()`` 即可。

为什么单独成模块：
- ``_run_ai`` 已经够长；图片识别支线再加 album/document/sticker 多个分支会糊成
  一团。抽出来后单元测试也好写——不必构造完整 event 树。
- telethon 的 Message 对象在测试里用 ``AsyncMock`` 模拟即可；这里所有函数
  都只读 message 的属性，不直接打 TG 网络。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# 单图体积上限——OpenAI / Anthropic 实测稳吃 ≤4MB；多图情况下整组求和也按这个
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# 一次最多送几张图给视觉模型；防 token 烧爆 + 防 album 灌爆。
# OpenAI vision 实操 ≤10 张；这里偏保守。
MAX_IMAGES_PER_REQUEST = 6

# 允许送给 vision 模型的图片 mime（image-as-document 模式时按这个白名单挑）
_ALLOWED_IMAGE_MIMES = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
)


def _doc_mime(msg: Any) -> str:
    """从 ``msg.document`` / ``msg.file`` 上取 mime_type；没拿到返回空串。"""
    doc = getattr(msg, "document", None)
    if doc is not None:
        m = getattr(doc, "mime_type", None) or ""
        if isinstance(m, str) and m:
            return m.lower()
    f = getattr(msg, "file", None)
    if f is not None:
        m = getattr(f, "mime_type", None) or ""
        if isinstance(m, str) and m:
            return m.lower()
    return ""


def _has_url_entity(msg: Any) -> bool:
    """检查消息是否包含 URL 类型的 entity（表示用户发了链接）。

    用于区分「用户主动上传的图」与「Telegram 从 URL 自动内联的预览图」。
    典型场景：用户发送 ```,ai https://example.com/photo.jpg```，
    Telegram 下载图片并以 MessageMediaPhoto 形式内联展示——
    但这不是用户主动上传的图，不应触发 vision 路由。
    """
    entities = getattr(msg, "entities", None) or []
    for e in entities:
        # Telethon TL 类型名：MessageEntityUrl / MessageEntityTextUrl
        e_type_name = type(e).__name__
        if e_type_name in ("MessageEntityUrl", "MessageEntityTextUrl"):
            return True
    return False


def message_has_image(msg: Any) -> bool:
    """判断一条 telethon Message 是否含**可识别的静态图**。

    覆盖：
    - ``photo``                    压缩图（最常见）
    - ``document`` mime=image/*    "按文件发送"的未压缩图
    - ``sticker`` mime=image/webp  静态贴纸（动态贴纸 .tgs / video webm 不算——vision 模型读不了）

    不算图的：voice / video / video_note / animation(gif) / 动态贴纸 / poll 等。
    不算图的：URL 网页预览缩略图（telethon msg.photo 会返回 web_preview.photo，
              但这是 Telegram 自动生成的链接预览，不是用户主动发送的图片）。
    不算图的：Telegram 从 URL 自动内联的图片（如直接发送 .jpg/.png 链接时，
              Telegram 会下载图片并以 MessageMediaPhoto 形式展示，但这不是用户
              主动上传的图，不应触发 vision 路由）。
    """
    if msg is None:
        return False
    media_type_name = type(getattr(msg, "media", None)).__name__

    # 排除 URL 预览缩略图：telethon 的 msg.photo 会包含 web_preview.photo，
    # 但网页预览图不算用户主动发送的图，应跳过。
    # 覆盖两种场景：
    #   1. MessageMediaWebPage — 标准 URL 预览（原有逻辑）
    #   2. MessageMediaPhoto + URL entity — Telegram 从 URL 自动内联的图片
    #      （如用户发送 ,ai https://xxx.jpg 时 Telegram 下载图片并以 Photo 形式展示）
    is_web_preview = media_type_name == "MessageMediaWebPage"
    if is_web_preview:
        return False

    if getattr(msg, "photo", None) is not None:
        # 如果消息中有 URL 类型的 entity，且 media 是 MessageMediaPhoto，
        # 很可能是 Telegram 自动从 URL 解析出的预览图，不算用户主动发送的图片。
        # 典型场景：用户发送 ,ai https://example.com/photo.jpg，
        # Telegram 自动下载图片并内联展示为 MessageMediaPhoto。
        if media_type_name == "MessageMediaPhoto" and _has_url_entity(msg):
            log.info(
                "[media] skipping MessageMediaPhoto with URL entity — "
                "likely auto-inlined preview, not user-uploaded image"
            )
            return False
        return True
    mime = _doc_mime(msg)
    # sticker 与 image-document 共用 document 字段；sticker 字段额外是 DocumentAttributeSticker
    is_sticker = getattr(msg, "sticker", None) is not None
    if is_sticker:
        # 静态贴纸（webp 静图）才送给 vision；动画贴纸（tgs lottie / webm 视频）跳过
        return mime in ("image/webp",)
    if mime in _ALLOWED_IMAGE_MIMES:
        return True
    return False


def message_has_audio(msg: Any) -> bool:
    """是否含可转写的音频（语音留言 / 音频文件）。"""
    if msg is None:
        return False
    if getattr(msg, "voice", None) is not None:
        return True
    if getattr(msg, "audio", None) is not None:
        return True
    mime = _doc_mime(msg)
    if mime.startswith("audio/"):
        return True
    return False


async def collect_image_sources(
    client: Any,
    replied: Any,
    self_msg: Any,
) -> list[Any]:
    """聚合所有"可识别图"的源 Message，按发送时序返回。

    顺序约定：
    1. 被回复消息（含其所在相册的全部图）
    2. 命令消息自己（含其所在相册的全部图）

    去重逻辑：
    - 按 ``msg.id`` 去重（同一相册可能被同时命中两次）
    - 限 ``MAX_IMAGES_PER_REQUEST`` 张

    ``client`` 用于 album 拉伴随消息——单元测试里给个 AsyncMock 即可，不出网。
    传 None 则跳过 album 扩展（仅返回自身那一条）。
    """
    out: list[Any] = []
    seen_ids: set[int] = set()

    async def _add_with_album(msg: Any) -> None:
        if msg is None:
            return
        # 主消息本身
        if message_has_image(msg):
            mid = getattr(msg, "id", None)
            if mid is None or mid not in seen_ids:
                if mid is not None:
                    seen_ids.add(mid)
                out.append(msg)
        # 相册：grouped_id 非空时拉同组 sibling
        gid = getattr(msg, "grouped_id", None)
        if gid is None or client is None:
            return
        try:
            siblings = await _fetch_album_siblings(client, msg)
        except Exception as e:  # noqa: BLE001
            # 拉相册失败不阻塞主流程——至少主图能识别
            log.warning("[media] fetch album siblings failed: %s: %s", type(e).__name__, e)
            return
        for s in siblings:
            if not message_has_image(s):
                continue
            sid = getattr(s, "id", None)
            if sid is None or sid in seen_ids:
                continue
            seen_ids.add(sid)
            out.append(s)
            if len(out) >= MAX_IMAGES_PER_REQUEST:
                return

    await _add_with_album(replied)
    if len(out) < MAX_IMAGES_PER_REQUEST:
        await _add_with_album(self_msg)

    if len(out) > MAX_IMAGES_PER_REQUEST:
        out = out[:MAX_IMAGES_PER_REQUEST]
    return out


async def _fetch_album_siblings(client: Any, msg: Any) -> list[Any]:
    """给定相册某一条，返回同组的其它消息（不含 ``msg`` 自己）。

    Telethon 没有一等公民 album API，惯用法是在该消息附近的窗口里
    （前后各 ~10 条）按 ``grouped_id`` 过滤。窗口太大浪费 API；太小可能漏。
    实测一组相册最多 10 张，所以前后各拉 10 条足够。
    """
    chat_id = getattr(msg, "chat_id", None)
    msg_id = getattr(msg, "id", None)
    gid = getattr(msg, "grouped_id", None)
    if chat_id is None or msg_id is None or gid is None:
        return []
    out: list[Any] = []
    # iter_messages(min_id=..., max_id=..., reverse=True) 取闭区间内的消息
    try:
        msgs = await client.get_messages(
            chat_id, limit=20, min_id=max(0, msg_id - 10), max_id=msg_id + 10
        )
    except Exception:
        return []
    for m in msgs or []:
        if getattr(m, "id", None) == msg_id:
            continue
        if getattr(m, "grouped_id", None) == gid:
            out.append(m)
    return out


async def download_image_bytes(client: Any, msg: Any) -> bytes:
    """从一条带图 Message 下载字节；遇 ``FileReferenceExpiredError`` 重试一次。

    转发的旧消息常见 file_reference 过期——telethon 的官方建议是"重新 fetch
    一次原始 message 拿新 reference 再下载"。这里实现这条重试。

    抛 ``ValueError`` 表示用户层面的失败（消息撤回 / 体积超限）；
    抛其它异常由调用方捕获并显示。
    """
    data = await _download_with_retry(client, msg)
    if not data:
        raise ValueError("图片下载结果为空（消息可能已撤回）")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片体积超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB（实际 {len(data) // 1024} KB），请压缩后再试"
        )
    return data


async def download_audio_bytes(client: Any, msg: Any) -> bytes:
    """同 ``download_image_bytes``，但对音频：上限可以放宽（25MB 是 Whisper 限制）。"""
    data = await _download_with_retry(client, msg)
    if not data:
        raise ValueError("音频下载结果为空（消息可能已撤回）")
    # Whisper 单文件上限 25MB；超了 STT 端点会拒
    _MAX_AUDIO_BYTES = 25 * 1024 * 1024
    if len(data) > _MAX_AUDIO_BYTES:
        raise ValueError(
            f"音频体积超过 {_MAX_AUDIO_BYTES // (1024 * 1024)} MB；请截短后再试"
        )
    return data


async def _download_with_retry(client: Any, msg: Any) -> bytes:
    """``msg.download_media(file=bytes)`` 包装，遇 file_reference 过期会重新拉一次原 message。"""
    try:
        return await msg.download_media(file=bytes)
    except Exception as exc:  # noqa: BLE001
        # 仅对 FileReferenceExpired 类异常重试；其它直接抛
        name = type(exc).__name__
        if "FileReference" not in name and "ReferenceExpired" not in name:
            raise
        log.warning("[media] file reference expired, refetching: %s", name)
        chat_id = getattr(msg, "chat_id", None)
        msg_id = getattr(msg, "id", None)
        if client is None or chat_id is None or msg_id is None:
            raise
        try:
            fresh = await client.get_messages(chat_id, ids=msg_id)
        except Exception:
            raise exc from None
        if fresh is None:
            raise exc from None
        return await fresh.download_media(file=bytes)


__all__ = [
    "MAX_IMAGES_PER_REQUEST",
    "MAX_IMAGE_BYTES",
    "collect_image_sources",
    "download_audio_bytes",
    "download_image_bytes",
    "message_has_audio",
    "message_has_image",
]
