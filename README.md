# 画间 · ChatGPT 图片工作台

把自定义生图工作台与 ChatGPT 网页生图适配器组合成一个**本地 Web 程序**。无需部署服务器，图片、参考图、任务与登录配置保存在自己的电脑。

> 这是非官方网页接口适配，不是 OpenAI 官方 API。网页协议、账号可用性、额度和实际图像模型可能变化。只有在有权使用的账号与素材范围内操作，并遵守平台条款及上游用途声明。

## 能做什么

- 自由提示词生成：同一提示词生成 1–10 个独立任务。
- 提示词队列：每行一个任务，空行忽略；超过 10 行明确报错，不截断。
- 多参考图编辑：拖拽、选择文件或在上传区粘贴图片。
- 逐图修改：每张来源图单独处理，可附加最多 3 张通用参考图，避免商品相互混入。
- 继续修改：选中已有结果，保留来源图并添加商品保真规则。
- 12 种比例 / 自动画布，自定义厘米画布和目标像素提示；完整预览实际发送的提示词。
- 结果实时落盘、历史恢复、原尺寸预览、单图下载和整批 ZIP（包含任务清单）。
- 本地 OpenAI 风格图片 API：文生图、图片编辑、模型列表及进度流。
- 幂等提交、逐项失败隔离、取消尚未开始的任务；重启后标记中断，不自动重放未知请求。

普通自由生图不添加角色、风格或负面提示词。指定比例、自定义画布、继续修改是明确展示的例外。网页不提供可靠的 seed 或精确像素控制，本程序没有假装支持这些能力，也不会事后拉伸或裁切图片来冒充成功。

## Windows 开始使用

1. 安装 **Python 3.12+** 和 **Node.js 22+**，安装时把 Python 加入 PATH。
2. 下载仓库 ZIP 并解压到固定目录。
3. 双击 `Start.cmd`。首次运行会安装依赖并构建页面，需要联网；以后复用本地环境。
4. 浏览器打开 `http://127.0.0.1:8765`。
5. 点击右上角「配置 ChatGPT」，按说明打开**自己的** ChatGPT 会话信息页，全选复制整个 JSON 并粘贴到「网页登录凭证」，程序自动提取，无需自己查找字段；需要代理时一起填写，保存后检查连接。
6. 输入提示词，按需上传参考图，点击「开始生成」。

连接设置中有会话信息页链接，支持整段 JSON（包括多行格式）或单独的 `accessToken` / `Bearer` 凭证。程序只提取并加密保存所需凭证，不保存 JSON 中的姓名、邮箱等个人资料；格式有误时保留原连接设置。整个 JSON 也包含敏感登录信息，不要发给他人、粘贴到问题反馈、截图、Git 或日志。程序不读取其他应用的凭证，不自动注册账号，不接管工作浏览器。

停止服务：双击 `Stop.cmd`。建议先等任务完成；强制停止中的任务在下次启动后标记为中断，请先到 ChatGPT 网页核对结果。

PowerShell 自选端口、只启动后台而不打开浏览器：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Port 8765 -NoBrowser
```

更新代码后，执行 `npm ci` 和 `npm run build` 重新构建页面，再重启服务。不要覆盖或删除 `data` 目录。

## macOS / Linux 或开发运行

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
npm ci
npm run build
.venv/bin/python run.py
```

Windows 可把 `.venv/bin/python` 换成 `.venv/Scripts/python.exe`。开发前端可另开终端执行 `npm run dev`，后端仍需运行。这里只监听回环地址，不提供公网或局域网部署模式。

## 关于 gpt-image-2.5

默认对外别名是 `gpt-image-2.5`，兼容 `gpt-image-2` 与 `gpt-image2.5`。**这是适配器命名，不是实际模型版本的证明。**

网页请求目前沿用源项目的 `model=gpt-5-3` 和 `system_hints=["picture_v2"]`。可以在连接设置中调整网页 model 参数。对外 API 名称、网页会话模型和底层出图模型是不同层次；不能靠改名确认升级。检查连接仅验证网页账号访问，不验证生图能力或图像模型版本。

## 本地图片 API

在连接设置的「模型映射与本地 API」中复制**本地 API 密钥**。它与网页登录凭证不同，切勿互换。

| 接口 | 功能 |
| --- | --- |
| `GET /v1/models` | 查询本地可用别名；`actual_model_verified=false` 明确表示版本未经校验 |
| `POST /v1/images/generations` | JSON 文生图 |
| `POST /v1/images/edits` | multipart 图片编辑，支持 `image` 或 `image[]` |

基础地址为 `http://127.0.0.1:8765/v1`。通过 `Authorization: Bearer <本地API密钥>` 鉴权。

文生图请求结构（不含任何真实凭证）：

```json
{
  "model": "gpt-image-2.5",
  "prompt": "一只陶瓷马克杯，暖白背景，自然光",
  "n": 1,
  "response_format": "b64_json",
  "stream": false
}
```

- 图片 API 的 `n` 为 1–4，工作台批次最多 10 项。
- `response_format` 支持 `b64_json` 或 `url`。URL 指向本地文件，下载时仍需本地 API Bearer 鉴权；工作台浏览器使用自身会话。
- `size` 可传 `auto`、`1024x1024`、`3:4`，仅构成提示词要求，不承诺输出精确像素。
- 可带 `Idempotency-Key` 请求头。重试时保留同一值、同一内容，避免重复生图；同编号内容变化会拒绝。
- `stream=true` 返回 SSE：自定义进度事件、最终图片结果、`[DONE]`；不是官方部分图像增量协议。
- 客户端断线不取消已接收任务。返回中的 `batch_id` 可在工作台对应历史核对。部分失败时 API 返回错误并报告已完成数，成功图片仍保留在工作台。
- 不包含通用文本聊天、自动注册、账号池、额度规避或云端存储。

## 数据与安全

- `data/connection.enc`：网页登录、代理与本地 API 密钥。Windows 使用当前系统用户的 DPAPI 加密；其他系统使用权限受限的本地 Fernet 密钥。不要复制此目录给别人，也不要公开它。
- `data/studio.db`：本地 SQLite 任务、提示词及参考图关系。
- `data/images/`：参考图与结果 PNG，保存时去除元数据；不上传任何第三方对象存储。
- 点击生成时，提示词与参考图会发送至 ChatGPT 执行生成；“本地”指程序运行与结果存储位置，不代表离线 AI 推理。
- API/图片有鉴权，工作台限制 Host/Origin，拒绝跨站浏览器请求。服务不监听外部网络地址。
- 网页请求的完整响应、Token、Cookie 和设备标识不记入日志；错误仅提供安全的状态说明。
- 图片文件传输使用独立的无登录会话，不携带 ChatGPT 登录 Token、Cookie 或账号设备标识；仅接受 HTTPS 图片地址，不自动跟随重定向。
- 删除历史只删记录，保留图片文件，避免破坏其他任务引用。本版本不自动清理图片。
- 可用性取决于 ChatGPT 网页。登录、验证码或权限问题必须由账号持有人正常完成，程序不绕过授权。

## 验证

```sh
.venv/bin/python -m pytest tests -q
npm run build
npx playwright install chromium
npm run test:e2e
.venv/bin/python scripts/check_public_source.py
```

浏览器测试启动独立的本地临时服务并注入离线图片回执，不使用真实账号，不消耗额度。适配器测试覆盖上传 → prepare → conversation SSE → 文件查询 → 图片下载的请求链，但**离线测试通过不等于当前网页登录生图已验收**。

## 来源

工作台任务规划、提示词透传、自定义画布及继续修改规则来自 `ai-ecommerce-detail-generator` 自定义生图模块的独立移植；网页适配器来自用户提供的 ChatGPT2API v1.1.7 源码。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [架构说明](docs/ARCHITECTURE.md)。未复制原业务系统、历史数据、授权服务、云配置或整个私有仓库。
