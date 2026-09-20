# 来源与许可说明

## ChatGPT2API

- 上游：<https://github.com/basketikun/chatgpt2api>
- 输入：用户提供的 `chatgpt2api-v1.1.7-full-repo.zip`，版本文件为 `1.1.7`。
- ZIP SHA-256：`b437d374f808b1f5913cb976ba3ceB4AF5A5B8B1A32C1AF0684C5061AA50D1A1`（大小写不影响摘要）。
- 移植范围：网页图片请求、参考图上传、会话准备、SSE、文件下载，以及配套网页请求处理函数，位于 `studio/upstream/`。
- 未引入账号自动注册、邮箱接码、CPA/sub2api、账号池、Git 存储或云备份。
- 上游 MIT 文本原样保留于 `licenses/chatgpt2api-MIT.txt`。上游 README 另有学习研究及用途声明，使用时需一并了解并遵守适用的平台条款。
- 本项目对适配器作了本地化改造：依赖注入、可配置网页模型、不记录敏感数据、禁止从 API 输入读取任意本地文件、连接关闭和安全错误处理。

## ai-ecommerce-detail-generator

- 用户提供入口：<https://github.com/CTctikki/ai-ecommerce-detail-generator>
- GitHub 实际重定向到仓库所有者的私有仓库：<https://github.com/ctzlyj/ai-ecommerce-detail-generator>。
- 本次参考提交：`c00dc693f8dbf2021bfa36d62050447df5bda587`。
- 按仓库所有者的本次请求，仅独立移植自定义生图相关规则：
  - `src/server/workbench/generation-plan.ts` 的计数、队列、逐图参考分配。
  - `src/shared/custom-canvas.ts` 的尺寸归一、冲突检查与画布提示词。
  - `src/server/images/derivative-prompt.ts` 的商品保真修改要求。
- 新工作台 UI 和 Python 本地任务服务为本项目独立实现。未复制整个私有项目、业务后端、收费逻辑、ERP 权限、用户历史或部署配置。
- 原私有仓库未声明通用开源许可证。本次公开交付不应被理解为把该私有仓库的其余部分开源，也不新增对其余部分的许可。

本项目未替所有者选择额外的整体开源许可证；公开可见不等于对所有代码授予任意再许可权。第三方已有许可证各自适用。
