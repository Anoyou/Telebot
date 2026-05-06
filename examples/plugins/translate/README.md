# translate（PagerMaid 移植示例）

## 源出处

本样例参考的是 PagerMaid 插件：
- https://github.com/TeamPGM/PagerMaid_Plugins/blob/master/translate/main.py

参考的是“功能意图”（回复消息后翻译），不是源码拷贝。

## 移植决策

- 从 Pyrogram 事件处理改为 Telebot 插件命令机制：`commands = {"fy": ...}`
- 命令语义保持一致：
  - `,fy <lang>` 翻译被回复文本到目标语言
  - `,fy auto` 自动识别语言后翻译
- LLM 调用不走第三方 SDK，直接复用 Telebot 现有链路：
  - `worker.command.get_command_context()` 获取 provider 池
  - `services.llm_client.build_client()` 构造客户端
- provider 选择策略：
  - 优先选带 `translate` tag 的 provider
  - 找不到则退回第一个可用 provider

## 目录结构

- `__init__.py`：导出 `PLUGIN_CLASS` 和 `MANIFEST`
- `manifest.py`：权限与元数据
- `plugin.py`：`fy_handler` 与 `TranslatePlugin`

## 权限说明

manifest 里声明了：
- `read_chat`：读取被回复消息文本
- `edit_message`：把命令消息编辑成翻译结果

## 安装（示例）

```bash
cp -R examples/plugins/translate data/plugins/installed/
# 然后重启 worker 或触发 reload
```

## 使用

1. 回复一条文本消息
2. 发送 `,fy zh` 或 `,fy auto`

