"""codex_image 插件 manifest。

配置模式：单配置对象（模式 B），无规则列表。
account_feature.config 字段：
  - access_token: str   Codex Access Token（通常在 .codex/auth.json 中获取）
  - command: str        触发命令名，默认 cximg，支持中文
  - model: str          主模型名称（如 gpt-5.4）
  - image_model: str    底层图片模型（如 gpt-image-2），auto 表示自动选择
  - message_template: str 最终 caption / 生成中状态消息模板
  - image_size / aspect_ratio / image_format: 图片尺寸、比例和输出格式
"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="codex_image",
    display_name="Codex 图片生成",
    version="1.1.1",
    author="TeleBoxOrg",
    description="通过 Codex API 调用 GPT 图片生成模型，支持纯文生图和参考图生成",
    experimental=True,
    permissions=["send_message", "edit_message", "read_chat", "send_file"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "properties": {
            "command": {
                "type": "string",
                "title": "触发指令名",
                "default": "cximg",
                "description": "在系统命令前缀后输入此指令触发图片生成，支持中文，如 画图",
            },
            "access_token": {
                "type": "string",
                "title": "Codex Access Token",
                "description": "从 .codex/auth.json 获取的 access token，用于鉴权 Codex API",
            },
            "model": {
                "type": "string",
                "title": "主模型",
                "default": "gpt-5.4",
                "enum": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "o3", "gpt-5", "gpt-5-nano", "gpt-5.2", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.5"],
                "description": "处理请求的主模型，支持 image_generation 工具",
            },
            "image_model": {
                "type": "string",
                "title": "底层图片模型",
                "default": "auto",
                "enum": ["auto", "gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"],
                "description": "实际生成图片的模型。auto 表示由 OpenAI 自动选择",
            },
            "max_wait_seconds": {
                "type": "integer",
                "title": "最大等待时间（秒）",
                "default": 600,
                "description": "图片生成最大等待时间，默认 600（10分钟）",
            },
            "status_interval_seconds": {
                "type": "integer",
                "title": "状态刷新间隔（秒）",
                "default": 20,
                "description": "长时间生成时编辑状态消息的间隔，默认 20 秒，建议 10 秒以上以减少风控",
            },
            "message_template": {
                "type": "string",
                "title": "消息模板",
                "default": (
                    "<b>🎨 Codex 图片生成</b>\n"
                    "<b>状态:</b> {status}\n"
                    "<b>提示词:</b> {prompt}\n"
                    "<b>主模型:</b> {model} · <b>图片模型:</b> {image_model}\n"
                    "<b>尺寸:</b> {image_size} · <b>比例:</b> {aspect_ratio} · <b>格式:</b> {image_format}\n"
                    "<b>耗时:</b> {elapsed}"
                    "{?revised_prompt}\n<b>修订提示词:</b> {revised_prompt}{/?}"
                ),
                "description": "支持 {status}/{prompt}/{elapsed}/{model}/{image_model}/{image_size}/{aspect_ratio}/{image_format}/{revised_prompt} 等占位符",
            },
            "image_size": {
                "type": "string",
                "title": "默认分辨率",
                "default": "1024x1024",
                "enum": ["auto", "1024x1024", "1536x1024", "1024x1536", "from_reference"],
                "description": "默认生成尺寸；from_reference 表示使用参考图尺寸（仅参考图生成时有效）；命令中可用 --size 临时覆盖",
            },
            "aspect_ratio": {
                "type": "string",
                "title": "默认画面比例",
                "default": "1:1",
                "enum": ["auto", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "from_reference"],
                "description": "默认构图比例；from_reference 表示使用参考图比例；命令中可用 --比例 或 --ratio 临时覆盖",
            },
            "image_format": {
                "type": "string",
                "title": "默认图片格式",
                "default": "png",
                "enum": ["png", "jpeg", "webp"],
                "description": "默认输出格式；命令中可用 --format 或 --格式 临时覆盖",
            },
            "delete_command_message": {
                "type": "boolean",
                "title": "完成后删除命令消息",
                "default": True,
                "description": "图片发送成功后删除原触发命令消息",
            },
            "show_revised_prompt": {
                "type": "boolean",
                "title": "显示模型修订提示词",
                "default": True,
                "description": "Codex 返回 revised_prompt 时是否显示在图片说明中",
            },
            "reasoning_effort": {
                "type": "string",
                "title": "推理强度",
                "default": "low",
                "enum": ["low", "medium", "high"],
                "description": "传给 Codex API 的 reasoning.effort",
            },
            "custom_instructions": {
                "type": "string",
                "title": "自定义系统指令",
                "default": "",
                "description": "留空使用默认指令；可要求风格、构图、安全边界等",
            },
        },
    },
)
