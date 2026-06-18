# with_http

最小 HTTP facade 示例，展示第三方插件如何声明并使用受控 HTTP 能力。

## 重点

- `plugin.json` 是安装阶段静态元数据。
- `manifest.py` 是运行阶段真实 Manifest。
- `permissions` 必须包含 `external_http`。
- `allowed_hosts` 必须列出允许访问的域名。
- 运行时只通过 `ctx.http` 访问外部 HTTP，不直接创建 `httpx.AsyncClient`。

## 使用

安装到 `plugins/installed/with_http/` 后启用插件，发送：

```text
,http_status
```

插件会请求 `https://api.github.com/zen` 并把状态码与响应片段编辑回命令消息。CI 只会导入 manifest 和实例化插件类，不会执行该命令或访问网络。
