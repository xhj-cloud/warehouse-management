# 修复记录：AI 输出乱码 + 分析超时的根因修复（编码 + 流式分析）

**时间标志：** 20260820_024711
**基于提交：** 4c03c67（上一轮"换模型 + 流式对话"）
**涉及文件：** `config.py`、`ai_service.py`、`app.py`、`static/js/app.js`
**回归测试：** `tests/` 145 passed, 10 skipped

---

## 一、现象（用户反馈）

1. 点"低库存预警"等 **分析按钮时报 `AI 分析服务超时`**。
2. **AI 输出全是乱码**，例如 `å½å ä¸ªåæè§¦åéåºå­é¢è¦`（其实是"当前 9 个商品触发低库存预警"）。

## 二、根因

换了 Qwen3.8-27B 后暴露出两个真实 bug：

| 问题 | 根因 |
|------|------|
| **乱码** | LM Studio 的**流式**响应 `Content-Type` 是 `text/event-stream`（**没有 `charset=utf-8`**）。Python `requests` 见此缺省 charset 会把 `resp.encoding` 默认成 `ISO-8859-1`；`iter_lines(decode_unicode=True)` 于是把每个 UTF-8 中文字节按 Latin-1 解出，变成 `å½å` 这类乱码。非流式的 `/chat/completions` 带 `charset=utf-8` 所以正常，之前没暴露。 |
| **超时** | Qwen3.8 是**思考型模型**：先生成 `reasoning_content`（内心推理）再出正文，完整一句低库存分析实测要 **~90–150s**。旧 `timeout=60` 太紧，必然误报超时；且 `/api/ai/analyze` 是非流式的，用户要干等整段生成完才显示。 |

## 三、怎么改的

### 1. 修复流式乱码（`ai_service.py` → `_stream_completion`）
把 `iter_lines(decode_unicode=True)` 改为 `iter_lines(decode_unicode=False)` 读取**原始字节**，
再进行 `raw_line.decode('utf-8', errors='replace')`。因为一个 SSE 帧就是完整一行、不会跨行拆散多字节字符，
逐行 UTF-8 解码即正确。这样流式与流式分/流式对话的中文都正常了。

### 2. 修复分析超时（两个方向一起）
**(a) 分析改流式**（治本、体验最好）：
- `ai_service.py`：把原 `analyze_inventory` 的 prompt 构建抽成 `_build_analyze_messages(query_type)`（非流式/流式共用）；
  新增 **`analyze_stream(query_type)`** 生成器，逐段 `yield {'type':'token','content':...}`，末尾 `{'type':'done','data':完整文本}`。
- `app.py`：新增 **`/api/ai/analyze/stream`**（SSE），逐 token 推送；错误推 `{'type':'error'}`，沿用登录鉴权。
- `static/js/app.js`：抽出通用 `streamSSE(path, onToken, onDone, onError, init)`（`fetch`+`ReadableStream` 解析 SSE）；
  `streamAIChat` 与 `runAIAnalysis` 都复用它。**四个分析预设按钮（综合分析/低库存/补货/趋势）改走流式分析**，
  边生成边显示，不再干等。

**(b) 放宽超时**（兜底非流式 `_call`，如智能导入/入库单识别）：
- `config.py`：`timeout` 60 → **180**，注释说明 Qwen3.8 是思考型模型、完整分析可达 80–150s。

### 3. 前端分析页
`runAIAnalysis` 用 `streamSSE` 实时渲染；中途中止/出错显示错误。渲染仍走 `renderChatReply`（转义防 XSS + 站内链接白名单）。

## 四、逻辑说明

| 环节 | 逻辑 |
|------|------|
| 流式编码 | 强制按字节读 + 逐行 `utf-8` 解码，绕开 requests 对缺 charset 的 SSE 误判为 ISO-8859-1 |
| 分析为何慢 | 思考型模型先 reasoning 后正文；非流式一次要等完整 ~150s |
| 为何流式能治 | `stream=True` 时每帧只有少量增量，前端立刻显示推理出的文字，不再触发整请求 60s 超时 |
| 共享 prompt | `analyze_inventory` 与 `analyze_stream` 共用 `_build_analyze_messages`，提示词一致 |
| 复用前端流式 | 通用 `streamSSE` 供"AI 对话"与"AI 分析"共用，逻辑不重复 |

## 五、验证（服务器 100.101.108.100:5050，账号 admin/admin123）

- **流式对话** `POST /api/ai/chat/stream {"message":"一句话说你好"}`：
  返回 `data: {"type":"token","content":"你好！"}`… 中文正常，**乱码已消除** ✅
- **流式分析** `GET /api/ai/analyze/stream?type=low_stock`：
  200，**耗时 152.5s（全程流式未超时）**，457 个 token 事件，末尾 `done`，输出规范中文
  「以下分析基于你提供的当前库存…补货数量…」，**不再提示超时** ✅
- 上传文件 MD5 与本地逐一一致；`systemctl status warehouse` → `active` ✅
- 本地 `pytest` → **145 passed, 10 skipped**（新增 analyze_stream 单元/API 用例） ✅

## 六、注意

- 分析一次可能要 1~3 分钟是**正常**的（思考型模型推理耗时）。流式让它"边想边显示"，
  首屏很快出来，用户不用干等。
- 若某次仍旧报超时：可再调大 `config.py` 的 `timeout`；或确认 LM Studio 是否在做推理 token 溢出。
