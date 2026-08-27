# 修复记录：AI 输出长度截断排查 + 移除硬截断 + 对话关思考

- **时间**：2026-08-27 15:15
- **提交**：`304d1b3` ai: 库存分析移除 600 字硬截断（完整输出模型全文）；AI 对话与分析统一关闭思考（enable_thinking=false + /no_think）
- **触发**：用户反馈 AI 输出有"长度截断"，要求 ① 库存分析不要硬截断 ② 库存分析与 AI 对话都关闭思考

## 一、截断原因排查结论

| # | 位置 | 性质 | 说明 |
|---|---|---|---|
| 1 | `config.py` `max_analyze_chars`（默认 600，env `ANALYZE_MAX_CHARS`） | **硬截断（主因）** | `analyze_stream` 正文累计到 600 字即停止读流、断开 LM Studio 连接，超出部分全部丢弃 |
| 2 | `ai_service.py` 分析 system prompt | 软约束 | prompt 要求"正文总长必须严格控制在 600 字以内" |
| 3 | AI 对话链路 | 潜在截断 | `max_tokens=16384` 为"思考+正文"共享预算，对话未关思考，推理写长会吃光预算中途停生成，且代码不检查 `finish_reason`，截断被当成正常结束 |
| 4 | 前端 `app.js:809` | 展示层（保留） | 思考进度区只显示末尾 24 字，避免刷屏，不影响数据 |

服务器 systemd 单元未设 `ANALYZE_MAX_CHARS`，生效值即默认 600。

## 二、本次改动（3 个文件）

1. **`config.py`**：删除 `max_analyze_chars` 配置项（`ANALYZE_MAX_CHARS` 环境变量同时失效）。
2. **`ai_service.py`**：
   - `analyze_stream` 移除硬截断逻辑，模型输出多长就完整推送多长；
   - 分析 system prompt 删除"600 字以内"硬性字数句，保留"要点/表格、聚焦干货"的风格引导；
   - 关思考开关 `ANALYZE_EXTRA_PAYLOAD` 改名 `NO_THINK_EXTRA_PAYLOAD`，**AI 对话（`chat_stream` 流式 + `chat` 非流式）也统一带上** `enable_thinking=false` 请求体开关 + `/no_think` 词法开关（双保险）；
3. **`tests/test_ai_service.py`**：
   - `test_hard_cap_limits_output` → `test_long_output_not_truncated`（1200 字长输出必须完整推送）；
   - 新增 `test_chat_disables_thinking`（对话必须带关思考开关 + `/no_think`）；
   - chat 相关 fake_stream 签名补 `extra_payload` 参数。

## 三、验证（实测）

**本地**：`pytest tests/ -q` → **151 passed, 10 skipped**（基线 150，+1 为新增对话关思考测试）。

**服务器**（srv-108，`/home/xhj/桌面/warehouse-management`，3 文件 MD5 与本地一致，重启后 active）：

| 场景 | 结果 |
|---|---|
| 登录 admin | success:true |
| `GET /api/ai/analyze/stream?type=general` | **正文 1516 字**（远超旧 600 上限，完整输出无截断），done 事件 1592 字，**thinking 事件 0 条**，耗时 ~27s，无 error |
| `POST /api/ai/chat/stream`（"现在仓库里有多少种商品？"） | 直接回答"仓库里共有 12 种商品。"（13 字），**thinking 事件 0 条**，耗时 ~20s，无 error |

## 四、注意事项

- 分析结果现在**可能较长**（模型写多长出多长），耗时随内容增长；如需控制长度只能靠 prompt 风格引导，不再有硬上限。
- 对话/分析关思考后，前端"AI 正在思考中"的推理进度展示基本不会再出现（模型直接作答）。
- 遗留（本次未动）：`_stream_completion` 仍不检查 `finish_reason`，若 `max_tokens=16384` 被耗尽，截断的回复仍会被当成完整回复；llama.cpp 侧运行时上下文长度（`-c` 参数）未确认，若小于"输入+16384"也会被上下文限制截断。
