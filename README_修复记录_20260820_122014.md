# 修复记录：AI 库存分析输出限长 + 前端模型显示与实际调用一致

- **时间**：2026-08-20 12:20
- **提交**：见 git log（本文件随本次提交）
- **涉及文件**：`config.py`、`ai_service.py`、`app.py`、`static/js/app.js`、`templates/index.html`、`tests/test_ai_service.py`、`tests/test_api.py`

## 一、问题描述

1. **AI 库存分析输出太长**：换 Qwen3.8-27B（思考型模型）后，一次完整分析正文可达
   3000+ 字、耗时 2~4 分钟。用户要求限制分析的输出长度。
2. **前端模型显示与实际调用不一致**：侧边栏写死 `AI 模型: qwen3.6-35b-a3b`，但后端实际
   调用的是 config 里配置的 Qwen3.8-27B（`lmstudio-community/qwen3.8-27b-q8_0.gguf/...`），
   显示与实际不符。

## 二、根因分析

1. **输出太长**：思考型模型在"给足 max_tokens 预算"后正文容易长篇大论；此前只在 system
   prompt 里软性要求"简短"，没有硬性长度约束，也没有兜底截断。
2. **模型显示错误**：`templates/index.html` 侧边栏把模型名硬编码成旧值 `qwen3.6-35b-a3b`，
   与后端真实调用（`config.LM_STUDIO_CONFIG['model']`）脱节；换模型后前端没跟着改。

## 三、修复方案

### 1. 限制分析输出长度（双保险：prompt 约束 + 硬截断）

- `config.py`：新增 `LM_STUDIO_CONFIG['max_analyze_chars']`，默认 **600** 字，可用环境变量
  `ANALYZE_MAX_CHARS` 覆盖。
- `ai_service._build_analyze_messages()`：system prompt 增加硬约束——"正文总长必须严格控制在
  {max_analyze_chars} 字以内，用要点或表格精炼表达，只列最重要的商品和建议数量"；restock 的
  user prompt 由"详细分析"改为"精炼建议（要点式）"。
- `ai_service.analyze_stream()`：正文累计达到上限即**停止读取流并直接返回 done**（break 会关闭
  `_stream_completion` 生成器，底层 requests 连接随之断开，LM Studio 侧生成也提前终止）。
  即使模型不听话写超长，前端收到的正文也不会超过上限。

### 2. 分析提速：关闭思考型模型的内心推理（附带优化）

限制长度后若仍花几分钟"思考"再给几百字结论体验很差，因此对**仅库存分析场景**加了两级关思考开关
（对话/导入等场景行为不变）：

- prompt 末尾追加 Qwen3 系列词法开关 `/no_think`；
- 请求体追加模板级 `chat_template_kwargs: {"enable_thinking": false}`（LM Studio 实测接受且生效）。

为此 `_call()` / `_stream_completion()` 增加可选参数 `extra_payload`，分析路径统一传入模块常量
`ANALYZE_EXTRA_PAYLOAD`。

### 3. 前端模型显示改为动态取后端实际调用值

- `app.py` `/api/ai/health`：响应新增 `model` 字段 = `ai_service.model`（即 `_call`/
  `_stream_completion` 真正发给 LM Studio 的 model，来自 config）。该值与 LM Studio 是否在线无关。
- `templates/index.html`：侧边栏硬编码模型名改为 `<span id="ai-model-name">加载中…</span>`。
- `static/js/app.js` `checkAIHealth()`：拿到响应后把 `model` 取最后一段、去掉 `.gguf` 后缀显示短名
  （如 `qwen3.8-27b-q8_0`）；请求失败时显示"未知"。

## 四、验证结果（服务器 100.101.108.100:5050，admin/admin123）

| 项目 | 修复前 | 修复后 |
|------|--------|--------|
| 分析正文长度 | ~3743 字 | **≤600**（实测 553/580 字，要点+表格） |
| 分析耗时 | ~195s（4600+ thinking） | **~88-101s**（thinking 降到 ~1600-2000） |
| 侧边栏模型显示 | qwen3.6-35b-a3b（写死，错误） | **qwen3.8-27b-q8_0**（=实际调用短名） |
| `/api/ai/health` model 字段 | 无 | `lmstudio-community/qwen3.8-27b-q8_0.gguf/qwen3.8-27b-q8_0.gguf` |
| AI 对话（回归） | — | 10s 返回"你好！"，中文正常无乱码 |

> 说明：分析仍保留几十秒耗时是思考型模型 + 大上下文所致；前端已有"🤖 AI 正在思考中…"实时进度，
> 不会像卡死。若需更快可继续调小 `ANALYZE_MAX_CHARS` 或在 LM Studio 侧换更小模型。

- 本地测试：`pytest tests/ -q` → **150 passed, 10 skipped**（新增硬截断、短输出不受影响、
  分析关思考开关透传等用例）。
- 服务器部署：改动文件 MD5 与本地一致，`systemctl restart warehouse.service` 后 `active`。

## 五、回滚方式

如需恢复旧行为：删除 `config.py` 的 `max_analyze_chars`（或设很大）、去掉 `_build_analyze_messages`
里的 `/no_think` 与 `ANALYZE_EXTRA_PAYLOAD` 传参即可；前端模型显示可改回写死文本。
