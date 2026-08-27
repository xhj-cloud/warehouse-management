# 修复记录：更换 AI 模型为 Qwen3.8-27B · AI 输出增加流式传输（SSE）

**时间标志：** 20260820_023218
**部署版本：** commit（见 git）｜已同步双远程仓库 origin / nas
**涉及文件：** `config.py`、`ai_service.py`、`app.py`、`static/js/app.js`、`templates/index.html`
**回归测试：** `tests/` 143 passed, 10 skipped（含新增 7 条流式测试）

---

## 一、需求

1. 把 AI 模型换成 `lmstudio-community/qwen3.8-27b-q8_0.gguf/qwen3.8-27b-q8_0.gguf`
2. AI 对话输出增加流式传输（一边生成一边显示，不用干等全部生成完）

## 二、改了什么 / 怎么改的

### 1. 更换默认模型

- **`config.py`**：`LM_STUDIO_CONFIG['model']` 默认值由 `qwen3.6-35b-a3b`
  改为 `lmstudio-community/qwen3.8-27b-q8_0.gguf/qwen3.8-27b-q8_0.gguf`
  （仍可通过环境变量 `LM_STUDIO_MODEL` 覆盖）。
- **`.env.example`**：同步更新 `LM_STUDIO_MODEL` 示例值。
- LM Studio 端该模型已加载（`/api/ai/health` 可用模型列表中可见），本次部署后即用新模型。

### 2. 后端流式生成（`ai_service.py`）

新增两个方法：

- **`_stream_completion(messages)`（底层）**：以 `stream=True` 调用 LM Studio 的
  `/chat/completions`，逐行解析 SSE 的 `data:` 帧（含 `[DONE]` 结束标志），
  逐块 `yield` 内容增量。连接失败/超时/HTTP 错误仍抛 `AIServiceError`，与旧的 `_call` 一致。

- **`chat_stream(user_message)`（业务层）**：生成器，yield 两类事件：
  - `{'type':'token','content':...}` —— 回复正文增量片段，收到即推给前端
  - `{'type':'done','full':...,'reply':...,'actions':...}` —— 流结束，
    附带剥离指令后的正文 `reply` 与解析出的 `actions`（无指令为 `None`）

  **关键逻辑**：对话支持"AI 写指令改库存"（` ```action ` 块放回复末尾）。
  流式过程一旦发现 ` ```action`/` ```json` 围栏，就停止推送剩余内容（那是指令 JSON，不能当正文显示），
  只把围栏之前的正文推出去；等全部 token 结束后，用原有的 `parse_actions`
  从完整输出中统一剥离指令、解析出 actions。并用 `yielded` 游标防止围栏出现
  "已推送正文被重复推送一次" 的 bug。

### 3. 后端流式端点 + 指令执行复用（`app.py`）

- **抽取共享函数 `_execute_ai_actions(actions, reply)`**：把原来 `api_ai_chat`
  里那段约 170 行的"逐条执行 AI 指令"逻辑（入库/出库/调库/加供应商/加客户/
  智能导入/创建出库单+生成 Excel）原样提取成一个顶层函数，返回 `(log 列表, 处理后的 reply)`。
  `api_ai_chat` 与其共享同一实现，**保证流式与旧接口行为完全一致**，不重复、不分叉。

- **新增端点 `/api/ai/chat/stream`（POST）**：返回 `text/event-stream`。
  - 逐 token 推 `{"type":"token","content":...}`
  - 流结束（收到 `done`）后调用 `_execute_ai_actions` 执行指令；若有执行日志
    推一条 `{"type":"log","text":...}`；最后推 `{"type":"done","data":...}`（完整正文，
    已含执行结果/下载链接）。
  - 出错时推 `{"type":"error","error":...}`。
  - 沿用原有登录鉴权（`before_request` 白名单外的 `/api/*` 未登录返回 401
    `AUTH_REQUIRED`），浏览器携带 HttpOnly session cookie 即可访问。

- Flask 引入 `Response, stream_with_context`，并设置
  `Cache-Control: no-cache`、`X-Accel-Buffering: no` 以防反向代理缓冲破坏流式。

### 4. 前端流式渲染（`static/js/app.js` + `templates/index.html`）

- 新增 **`streamAIChat(message, onToken, onDone, onError)`**：用原生 `fetch` +
  `ReadableStream` 读取 SSE，按空行切帧、解析 `data: {...}`，逐事件回调。
  - 400/401 处理与超时兜底：会话过期仍跳登录页，断流兜底。
- **`sendAIChat()`** 改为调用 `streamAIChat`：
  - `onToken`：把增量拼进累计正文，用原有的 `renderChatReply`（转义防 XSS + 站内链接可点）
    增量渲染气泡，实现"打字机"流式显示。
  - `onDone`：用完整回复做最终渲染（含出库单下载链接、执行日志）。
  - `onError`：占位气泡显示错误。
- **`templates/index.html`**：AI 对话输入框 placeholder 提示"（流式输出）"。

## 三、逻辑说明

| 环节 | 逻辑 |
|------|------|
| 模型选择 | 配置默认值指向 `qwen3.8-27b-q8_0.gguf`；LM Studio 按该 id 加载 |
| 传输方式 | LM Studio `stream=True` SSE → 后端 `iter_lines` 逐块取增量 → 再以 SSE 转给浏览器 |
| 指令剥离时机 | 流式"正文可实时显示"，指令推迟到流末尾统一 `parse_actions` 批量解析执行 |
| 防重复推送 | 用 `yielded` 游标记录已推送位置，围栏出现时只推"尚未推过的正文" |
| 指令执行一致 | 新旧两接口共用 `_execute_ai_actions`，改一个两者同步生效 |
| 前端安全 | 流式渲染仍走 `renderChatReply` 转义 + 站内链接白名单，不自 XSS |

## 四、验证结果（服务器 100.101.108.100，端口 5050）

- 登录 `admin/admin123` 后：
  - `/api/ai/health` → `可用模型: lmstudio-community/qwen3.8-27b-q8_0.gguf/qwen3.8-27b-q8_0.gguf, ...` ✅
  - `/api/ai/chat/stream` 问"介绍一下你自己" → 逐条 `token` 事件后 `done` ✅（流式生效）
  - `/api/ai/chat/stream` 说"新增供应商：流式测试供应商..." → 产生 ` ```action ` → 执行 → `log` 事件
    → 供应商已写入 DB（验证后已清理测试数据） ✅（指令+流式联动）
  - `/api/ai/chat`（旧接口）→ 正常返回 JSON ✅（重构无回归）
- 上传的 5 个文件 MD5 与本地逐一校验一致。
- 服务 `systemctl is-active warehouse` → `active`。

## 五、注意事项

- 出厂默认模型为 `qwen3.8-27b`；如需临时换回旧模型，设置环境变量
  `LM_STUDIO_MODEL=qwen3.6-35b-a3b` 后重启服务即可，无需改代码。
- 浏览器中文显示正常；命令行 `curl` 若是 GBK 终端，UTF-8 中文会显示为乱码，属显示层现象，非数据问题。
- 流式请求超时放宽到 10 分钟，普通接口仍 2 分钟。
