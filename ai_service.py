"""
仓库管理系统 - LM Studio AI 分析服务
通过 OpenAI 兼容 API 调用本地部署的 Qwen3.8-27B 模型（支持流式 SSE 输出）
"""

import json
import decimal
import requests
from datetime import datetime, date
from config import LM_STUDIO_CONFIG
from models import InventoryModel, StatsModel, TransactionModel, SupplierModel, CustomerModel


class DecimalEncoder(json.JSONEncoder):
    """处理 Decimal / datetime 等类型的 JSON 编码器"""
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        return super().default(obj)


class AIServiceError(RuntimeError):
    """AI 服务调用失败（连接不上/超时/HTTP 错误）。

    与「解析格式错误」区分开：旧实现把这类故障吞成普通字符串返回，
    上层 json.loads 失败后误报"解析格式错误"，LM Studio 挂了也查不出来。
    """


class AIService:
    """AI 库存分析服务"""

    def __init__(self):
        self.base_url = LM_STUDIO_CONFIG['base_url']
        self.model = LM_STUDIO_CONFIG['model']
        self.temperature = LM_STUDIO_CONFIG['temperature']
        self.max_tokens = LM_STUDIO_CONFIG['max_tokens']
        self.timeout = LM_STUDIO_CONFIG['timeout']

    def _call(self, messages):
        """调用 LM Studio API。连接失败/超时抛 AIServiceError，不再吞成字符串误导上层。"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']
        except requests.exceptions.Timeout:
            raise AIServiceError("AI 分析服务超时，请稍后重试。") from None
        except requests.exceptions.ConnectionError:
            raise AIServiceError(
                f"无法连接到 LM Studio 服务，请确认服务是否已启动 ({self.base_url})。") from None
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else '未知'
            raise AIServiceError(f"LM Studio 返回 HTTP {code} 错误，请检查模型状态。") from None
        except Exception as e:
            raise AIServiceError(f"AI 分析出错: {str(e)}") from None

    def _stream_completion(self, messages):
        """低层流式调用：逐块 yield 内容增量字符串。

        连接失败/超时/HTTP 错误抛 AIServiceError（与 _call 一致，便于上层统一处理）。
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'stream': True,   # 关键：启用 SSE 流式
        }
        try:
            with requests.post(url, json=payload, timeout=self.timeout, stream=True) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    # 只处理 "data: ..." 前缀的行
                    if raw_line.startswith('data:'):
                        data_str = raw_line[len('data:'):].strip()
                    else:
                        continue
                    if data_str == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get('choices') or []
                    if not choices:
                        continue
                    delta = choices[0].get('delta', {}) or {}
                    content = delta.get('content')
                    if content:
                        yield content
        except requests.exceptions.Timeout:
            raise AIServiceError("AI 分析服务超时，请稍后重试。") from None
        except requests.exceptions.ConnectionError:
            raise AIServiceError(
                f"无法连接到 LM Studio 服务，请确认服务是否已启动 ({self.base_url})。") from None
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else '未知'
            raise AIServiceError(f"LM Studio 返回 HTTP {code} 错误，请检查模型状态。") from None
        except GeneratorExit:
            # 客户端断开：正常结束生成器，不全抛 AIServiceError
            return
        except Exception as e:
            raise AIServiceError(f"AI 分析出错: {str(e)}") from None

    def chat_stream(self, user_message):
        """流式对话：逐段 yield 回复正文，可在流式展示的同时把 action 指令推迟到 stream 末尾解析。

        生成器产出两种事件字典：
            {'type': 'token', 'content': '...'}  回复正文的增量片段（收到即推给前端）
            {'type': 'done',  'full': '...', 'reply': '...', 'actions': [...]}
                全部 token 结束；reply=正文(去掉 action 块)，actions=解析出的指令(无则 None)

        reply 以外出现的 '```action'/'```json' 代码块需在最后用 parse_actions 从完整输出中剥离，
        因此流式过程中遇到 action 围栏后停止继续推送，把剩余部分积攒下来最后统一解析。
        """
        context = self.build_inventory_context()
        system_prompt = f"""你是一个专业的仓库库存管理助手，可以管理库存、供应商和客户。当前数据：
{context}

操作指令格式（放在回复末尾）：
```action
[
  {{"action":"stock_in","sku":"SKU编码","quantity":数量,"unit":"单位","category":"分类名","supplier":"供应商名"}},
  {{"action":"stock_out","sku":"SKU编码","quantity":数量}},
  {{"action":"set_quantity","sku":"SKU编码","quantity":数量}},
  {{"action":"smart_import","text":"采购描述"}},
  {{"action":"add_supplier","name":"供应商名","contact":"联系人","phone":"电话"}},
  {{"action":"add_customer","name":"客户名","contact":"联系人","phone":"电话"}},
  {{"action":"create_order","customer":"客户名","operator":"经办人","keeper":"库管","warehouse":"仓库编号",
    "items":[{{"sku":"SKU","quantity":数量,"price":单价}}]}}
]
```

规则：
- 入库/买了/进货 → stock_in，必须带上 unit、category、supplier
- 出库/用了/消耗 → stock_out
- 改成/调整为 → set_quantity
- 新增供应商/客户 → add_supplier / add_customer
- 创建出库单 → create_order，需要客户、经办人、商品清单(SKU+数量+单价)
- 纯提问不要输出 action
- 先自然回复，再放指令"""
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message},
        ]
        full = ''
        yielded = 0          # full 中已经推送给前端的长度（避免围栏触发时重复推送）
        in_action = False
        for delta in self._stream_completion(messages):
            full += delta
            if not in_action:
                # 一旦出现 action 围栏，说明正文到此为止，后面全是指令 JSON
                if '```action' in full or '```json' in full:
                    in_action = True
                    cut = min(p for p in (
                        full.find('```action'),
                        full.find('```json'),
                    ) if p != -1)
                    if cut > yielded:   # 只推围栏前尚未推过的部分
                        yield {'type': 'token', 'content': full[yielded:cut]}
                        yielded = cut
                else:
                    yield {'type': 'token', 'content': delta}
                    yielded = len(full)
        # 停止流式后统一用 parse_actions 从完整输出剥离 action 块
        actions, reply = self.parse_actions(full)
        if not actions:
            reply = full
        yield {'type': 'done', 'full': full, 'reply': reply, 'actions': actions}

    def build_inventory_context(self):
        """构建库存上下文数据"""
        # 获取库存概览
        stats = StatsModel.get_dashboard()

        # 获取低库存商品
        low_stock = InventoryModel.get_low_stock()

        # 获取全部库存
        all_inventory = InventoryModel.get_all()

        # 获取最近交易
        recent_txns = TransactionModel.get_all(limit=30)

        context = {
            '概览': {
                '商品总数': stats['total_products'],
                '分类数量': stats['total_categories'],
                '库存总量': stats['total_quantity'],
                '低库存预警数': stats['low_stock_count'],
            },
            '低库存商品': [
                {
                    '名称': item['product_name'],
                    'SKU': item['sku'],
                    '库存': item['quantity'],
                    '最低库存': item['min_stock'],
                    '分类': item['category_name'],
                    '位置': item['location'],
                }
                for item in low_stock
            ],
            '库存概况': [
                {
                    '名称': item['product_name'],
                    'SKU': item['sku'],
                    '数量': item['quantity'],
                    '分类': item['category_name'],
                    '位置': item['location'],
                }
                for item in all_inventory[:50]
            ],
            '最近交易': [
                {
                    '商品': item['product_name'],
                    '类型': '入库' if item['type'] == 'in' else '出库',
                    '数量': item['quantity'],
                    '时间': str(item['created_at']),
                }
                for item in recent_txns[:20]
            ],
            '供应商列表': [
                {'ID': s['id'], '名称': s['name']}
                for s in SupplierModel.get_all()
            ],
            '客户列表': [
                {'ID': c['id'], '名称': c['name']}
                for c in CustomerModel.get_all()
            ],
        }
        return json.dumps(context, ensure_ascii=False, indent=2, cls=DecimalEncoder)

    def analyze_inventory(self, query_type='general'):
        """
        综合库存分析

        query_type:
            - 'general': 综合分析
            - 'low_stock': 低库存预警分析
            - 'restock': 补货建议
            - 'trend': 趋势分析
        """
        context = self.build_inventory_context()

        prompts = {
            'general': f"""你是一个专业的仓库库存管理分析助手。请根据以下仓库数据进行分析，给出专业建议。

库存数据：
{context}

请从以下几个方面进行分析：
1. 整体库存状况评估
2. 低库存商品风险提示
3. 库存结构优化建议
4. 需要立即处理的紧急事项

请用中文输出，分析要具体、可操作。如果数据量较少，请如实说明。""",

            'low_stock': f"""你是一个专业的仓库库存管理分析助手。请重点关注以下库存数据中的低库存风险。

库存数据：
{context}

请分析：
1. 哪些商品存在立即断货风险
2. 建议的补货优先级排序
3. 每类低库存商品的建议补货数量
4. 预防缺货的管理建议

请用中文输出具体建议。""",

            'restock': f"""你是一个专业的仓库库存管理分析助手。请根据以下库存数据给出补货计划建议。

库存数据：
{context}

请提供：
1. 需要立即补货的商品清单及建议数量
2. 近期可能需要补货的商品预警
3. 补货时间窗口建议
4. 库存周转优化策略

请用中文输出详细分析。""",

            'trend': f"""你是一个专业的仓库库存管理分析助手。请根据以下库存和交易数据，分析库存变化趋势。

库存数据：
{context}

请分析：
1. 近期出入库的主要趋势
2. 哪些商品库存变化较快
3. 是否存在库存积压风险
4. 未来1-2周的库存变化预测

请用中文输出分析结果。""",
        }

        prompt = prompts.get(query_type, prompts['general'])
        messages = [
            {'role': 'system', 'content': '你是一个专业的仓库库存管理分析助手，擅长数据分析和管理建议。请始终用中文回答。'},
            {'role': 'user', 'content': prompt},
        ]
        return self._call(messages)

    def chat(self, user_message):
        """
        自由对话模式，支持直接修改库存。
        AI 返回 JSON 指令时自动执行，否则正常回复。
        """
        context = self.build_inventory_context()
        system_prompt = f"""你是一个专业的仓库库存管理助手，可以管理库存、供应商和客户。当前数据：
{context}

操作指令格式（放在回复末尾）：
```action
[
  {{"action":"stock_in","sku":"SKU编码","quantity":数量,"unit":"单位","category":"分类名","supplier":"供应商名"}},
  {{"action":"stock_out","sku":"SKU编码","quantity":数量}},
  {{"action":"set_quantity","sku":"SKU编码","quantity":数量}},
  {{"action":"smart_import","text":"采购描述"}},
  {{"action":"add_supplier","name":"供应商名","contact":"联系人","phone":"电话"}},
  {{"action":"add_customer","name":"客户名","contact":"联系人","phone":"电话"}},
  {{"action":"create_order","customer":"客户名","operator":"经办人","keeper":"库管","warehouse":"仓库编号",
    "items":[{{"sku":"SKU","quantity":数量,"price":单价}}]}}
]
```

规则：
- 入库/买了/进货 → stock_in，必须带上 unit、category、supplier
- 出库/用了/消耗 → stock_out
- 改成/调整为 → set_quantity
- 新增供应商/客户 → add_supplier / add_customer
- 创建出库单 → create_order，需要客户、经办人、商品清单(SKU+数量+单价)
- 纯提问不要输出 action
- 先自然回复，再放指令"""
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message},
        ]
        return self._call(messages)

    def ocr_image(self, image_base64):
        """OCR 识别图片文字"""
        import base64
        import io
        from PIL import Image
        import pytesseract

        img_bytes = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(img_bytes))
        # 转灰度提高识别率
        img = img.convert('L')
        text = pytesseract.image_to_string(img, lang='chi_sim+eng')
        return text.strip()

    def recognize_inbound_image(self, image_base64):
        """OCR + AI 识别入库单：先 OCR 提取文字，再由 AI 解析为结构化数据"""
        # 1. OCR 提取文字
        try:
            ocr_text = self.ocr_image(image_base64)
        except Exception as e:
            raise RuntimeError(f"OCR 识别失败: {str(e)}。请确认已安装 tesseract-ocr 及中文语言包")

        if not ocr_text or len(ocr_text) < 5:
            raise RuntimeError("OCR 未识别到有效文字，请上传更清晰的图片")

        # 2. AI 解析文字
        prompt = f"""你是一个仓库入库单解析助手。请从以下 OCR 识别文字中提取商品信息。

OCR 识别文字：
{ocr_text}

请解析为 JSON 数组，每个商品一个对象：
[
  {{"name": "商品名称", "sku": "SKU或型号", "quantity": 数量, "unit": "单位", "unit_price": 单价, "supplier": "供应商名称", "specification": "规格"}}
]

规则：
- 数量、单价必须是数字
- 单位根据上下文推断（个/盒/箱/包/卷/台/只/件）
- 供应商填入发货方/销售方名称
- 忽略表头、页码、总价等非商品行
- 只输出 JSON 数组，不要其他内容"""

        messages = [
            {'role': 'system', 'content': '你是一个精准的 JSON 数据解析器。只输出 JSON。'},
            {'role': 'user', 'content': prompt},
        ]
        raw = self._call(messages)

        # 清洗输出
        import re as _re
        raw = raw.strip()
        raw = _re.sub(r'^```(?:json)?\s*', '', raw)
        raw = _re.sub(r'\s*```\s*$', '', raw)
        arr = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if arr: raw = arr.group(0)
        raw = _re.sub(r',\s*]', ']', raw)
        raw = _re.sub(r',\s*}', '}', raw)
        return json.loads(raw)

    def _call_vision(self, messages):
        """调用 LM Studio Vision API（保留兼容）"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': 0.3,
            'max_tokens': 4096,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout * 2)
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content']
        except requests.exceptions.Timeout:
            raise RuntimeError("AI 视觉识别超时")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"无法连接 LM Studio ({self.base_url})")
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"模型不支持视觉: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"AI 视觉识别失败: {str(e)}")

    def parse_actions(self, text):
        """从 AI 回复中提取 action 指令"""
        import re
        # 宽松匹配：```action 或 ```json 都行
        match = re.search(r'```(?:action|json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if not match:
            return None, text
        clean_text = text[:match.start()].strip()
        try:
            actions = json.loads(match.group(1))
            return actions, clean_text
        except json.JSONDecodeError:
            return None, text

    def smart_import(self, user_text):
        """
        智能导入：用户用自然语言描述采购/入库信息，AI 解析为结构化数据
        """
        # 获取现有商品和供应商列表
        all_inv = InventoryModel.get_all()
        existing_products = [
            {'名称': item['product_name'], 'SKU': item['sku'], '分类': item['category_name']}
            for item in all_inv
        ]
        existing_suppliers = [
            {'ID': s['id'], '名称': s['name']} for s in SupplierModel.get_all()
        ]
        existing_json = json.dumps({
            '现有商品': existing_products,
            '现有供应商': existing_suppliers,
        }, ensure_ascii=False, cls=DecimalEncoder)

        prompt = f"""你是一个仓库数据录入助手。请将用户描述的商品采购信息解析为 JSON 数组。

参考数据：
{existing_json}

用户说：
{user_text}

请解析为以下 JSON 格式，只输出 JSON，不要其他内容：
[
  {{
    "name": "商品名称",
    "sku": "SKU编码（自动生成）",
    "category": "分类名称",
    "supplier": "供应商名称",
    "unit": "单位（个/盒/包/箱/卷/根/套）",
    "quantity": 数量（整数）,
    "price": 参考单价（如有，填入数字）,
    "notes": "备注"
  }}
]

规则：
1. 已有商品 SKU 复用，供应商优先匹配已有列表
2. 用户提到"从XX买的/XX公司的"→ supplier 填那个名称
3. 没提到供应商可不填
4. 只输出 JSON，不要其他文字"""

        messages = [
            {'role': 'system', 'content': '你是一个精准的 JSON 数据解析器。只输出 JSON，不输出任何其他内容。'},
            {'role': 'user', 'content': prompt},
        ]
        raw = self._call(messages)

        # 清洗 AI 输出
        import re as _re
        raw = raw.strip()
        # 去掉所有 markdown 代码块包裹
        raw = _re.sub(r'^```(?:json)?\s*', '', raw)
        raw = _re.sub(r'\s*```\s*$', '', raw)
        # 尝试提取 JSON 数组
        arr_match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if arr_match:
            raw = arr_match.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 宽松解析：修复常见问题
        raw = _re.sub(r',\s*]', ']', raw)  # 去掉尾逗号
        raw = _re.sub(r',\s*}', '}', raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("AI 解析结果格式错误，请重新描述或换一种说法")

    def health_check(self):
        """检查 LM Studio 服务是否可用"""
        try:
            url = f"{self.base_url}/models"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get('id', '') for m in data.get('data', [])]
                return True, f"AI 服务正常，可用模型: {', '.join(models) if models else '未知'}"
            return False, f"AI 服务返回异常状态码: {resp.status_code}"
        except requests.exceptions.ConnectionError:
            return False, "无法连接到 LM Studio，请确认 http://100.101.108.100:1234 已启动"
        except Exception as e:
            return False, f"AI 服务检查失败: {str(e)}"


ai_service = AIService()
