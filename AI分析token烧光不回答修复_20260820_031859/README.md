# 修复记录：AI 库存分析"用不了"——思考型模型烧光 token、只推理不给答案

**时间标志：** 20260820_031859
**基于提交：** 3c2dfca（上一轮：乱码 + 分析超时）
**涉及文件：** `config.py`、`ai_service.py`、`app.py`、`static/js/app.js`
**回归测试：** `tests/` 147 passed, 10 skipped

---

## 一、现象

换了 Qwen3.8-27B（思考型模型）后，点"AI 库存分析"按钮：
- 点了之后长时间无反应，最后要么"超时"、要么只显示一个空结果；
- 实际上模型一直在做**内心推理**，但没产出真正的分析正文。

## 二、根因

Qwen3.8 是 thinking 型模型，会先在 `reasoning_content` 里做很长一段内心推理，再产出正式的 `content`。
排查发现：
1. **老配置 `max_tokens=4096` 太小**，模型的推理（reasoning）几段就把 4096 吃光，`finish_reason=length`，
   `content` 为空 → 分析没有答案。
2. 旧非流式逻辑在**推理期间不返回任何中间结果**，前端一直"转圈"像卡死。
3. 实测：未约束时一段分析 149s 内产出 **4095 条 thinking、0 条 content**（全烧在推理上）。

## 三、怎么改的

| 改动 | 逻辑 |
|------|------|
| **`config.py`** `max_tokens` 4096 → **16384** | 给"推理 + 正文"都留足预算，避免推理吃光导致正文为空 |
| **`ai_service.py`** 各 system prompt 加"简短思考、直接作答" | 约束模型少推理、聚焦结论，实测能把纯推理从 ~150s 压到 ~20s 内开始出正文 |
  - `_build_analyze_messages`：分析类
  - `chat` / `chat_stream`：对话类
| **`app.py`** `/api/ai/analyze/stream` 同时转发 **thinking 事件** | 思考型模型推理期间也能实时推送，前端看到"正在思考…"进度，不再像卡死 |
| **`static/js/app.js`** `streamSSE` 支持 `onThinking`；`runAIAnalysis` 显示思考进度 | 推理到达时显示"🤖 AI 正在思考中…"，正式正文到达后切换为正文流式渲染 |

## 四、逻辑说明

- `_stream_completion` 把 `reasoning_content` 作为 `('thinking', …)`、`content` 作为 `('token', …)` 分别产出；
  前端两类分开处理：thinking 仅作为进度展示，token 才是最终答案。
- `analyze_stream` / `chat_stream` 把 thinking 原样透传、token 累积为 `full` 供 `done` 使用。
- 通过"提高 token 预算 + 提示词约束少推理"，从源头避免"只思考不出答案"。

## 五、验证（服务器 100.101.108.100:5050，登录 admin/admin123）

- `/api/ai/analyze/stream?type=low_stock`：200，**产出完整中文分析**（2413 条 token 事件、done 正文 3743 字，
  含低库存补货优先级表格），同时 3935 条 thinking 事件用于"思考中"实时进度，无 error ✅
- 修复前后对照：修复前 149s 只有 thinking、正文为空；修复后能正常给出补货建议 ✅
- `/api/ai/chat/stream`：`一句话说你好` → `你好！`，中文正常（无乱码） ✅
- 上传 `config.py / ai_service.py / app.py / static/js/app.js`，MD5 与本地一致；服务 `active` ✅
- 本地 `pytest` → **147 passed, 10 skipped** ✅

## 六、注意

- 完整库存分析一次要 **1~4 分钟** 是思考型模型的正常耗时；现在推理与正文都实时流式显示，
  用户能看到"正在思考…"和逐字正文，不再误以为卡死。
- 若仍嫌慢，可进一步在分析 system prompt 里收紧字数（如"结论控制在 N 字以内"）或降低分析覆盖面。
