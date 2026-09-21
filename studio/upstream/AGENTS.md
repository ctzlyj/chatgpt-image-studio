# 网页图片适配维护

- 排查失败先核对同一任务的网页会话与工具输出；不要仅凭通用报错归因于提示词，也不要为核对结果重复提交生图。
- `server_ste_metadata` 的 `tool_invoked=false` / `turn_use_case=text` 不能独立证明无图片。回归覆盖旧版 `async_task_type=image_gen` 和新版 `image_gen_title` 工具输出，排除用户参考图、无关工具与错误消息。
- ChatGPT 的 `/backend-api/estuary/content` 需要原站鉴权。仅对精确 HTTPS 原站、默认端口和该路径使用登录会话；外部 URL 保持无凭证会话，禁止自动跟随重定向。
- 修改图片链路先运行 `tests/test_provider.py`，再运行完整测试。离线通过不能替代真实文件取回；恢复历史任务时保留任务身份和已确认的结果，不把新生成冒充旧结果。
