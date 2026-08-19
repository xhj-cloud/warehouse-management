"""
ai_service.py 单元测试：action 指令解析、AI 输出 JSON 清洗、OCR 流程、健康检查。
LM Studio HTTP 层与数据库访问全部 mock，不依赖真实 AI 服务 / MySQL。

注意：对共享实例（ai_service）和模型类方法的替换必须走 monkeypatch，自动还原。
"""

import json
from types import SimpleNamespace

import pytest
import requests as requests_lib

import ai_service as ai_mod
import models as models_mod


@pytest.fixture()
def svc():
    return ai_mod.ai_service


# ==========================================
#  parse_actions：从回复中提取 ```action 指令块
# ==========================================
class TestParseActions:
    def test_extracts_action_block(self, svc):
        text = (
            '好的，已为您入库。\n'
            '```action\n'
            '[{"action": "stock_in", "sku": "A1", "quantity": 5}]\n'
            '```\n'
        )
        actions, clean = svc.parse_actions(text)
        assert actions == [{'action': 'stock_in', 'sku': 'A1', 'quantity': 5}]
        assert clean == '好的，已为您入库。'

    def test_json_fence_also_accepted(self, svc):
        text = '回复\n```json\n[{"action": "set_quantity", "sku": "B2", "quantity": 9}]\n```\n'
        actions, _ = svc.parse_actions(text)
        assert actions and actions[0]['action'] == 'set_quantity'

    def test_no_fence_returns_none(self, svc):
        text = '纯文字回复，没有任何指令。'
        actions, clean = svc.parse_actions(text)
        assert actions is None
        assert clean == text

    def test_invalid_json_inside_fence_returns_none(self, svc):
        text = '```action\n[{"action": "stock_in",}\n```'  # 尾逗号，无法解析
        actions, clean = svc.parse_actions(text)
        assert actions is None
        assert clean == text

    def test_multiple_actions(self, svc):
        text = (
            '```action\n'
            '[{"action": "add_supplier", "name": "S1"},\n'
            ' {"action": "add_customer", "name": "C1"}]\n'
            '```\n'
        )
        actions, _ = svc.parse_actions(text)
        assert [a['action'] for a in actions] == ['add_supplier', 'add_customer']


# ==========================================
#  _call：LM Studio HTTP 调用与错误降级
# ==========================================
class TestCall:
    def test_success_extracts_content(self, svc, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured['url'] = url
            captured['payload'] = json
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {'choices': [{'message': {'content': '分析结果'}}]},
            )

        monkeypatch.setattr(ai_mod.requests, 'post', fake_post)
        result = svc._call([{'role': 'user', 'content': 'hi'}])

        assert result == '分析结果'
        assert captured['url'].endswith('/chat/completions')
        assert captured['payload']['model'] == ai_mod.LM_STUDIO_CONFIG['model']
        assert captured['payload']['messages'][0]['content'] == 'hi'

    def test_timeout_raises_aiserviceerror(self, svc, monkeypatch):
        """回归 #14：超时抛 AIServiceError（旧实现返回字符串，上层误报"解析格式错误"）"""
        def boom(*a, **k):
            raise requests_lib.exceptions.Timeout('timed out')
        monkeypatch.setattr(ai_mod.requests, 'post', boom)
        with pytest.raises(ai_mod.AIServiceError, match='超时'):
            svc._call([{'role': 'user', 'content': 'x'}])

    def test_connection_error_raises_aiserviceerror(self, svc, monkeypatch):
        """回归 #14：连接失败抛 AIServiceError，错误信息直达用户而不是被 json 解析吞掉"""
        def boom(*a, **k):
            raise requests_lib.exceptions.ConnectionError('refused')
        monkeypatch.setattr(ai_mod.requests, 'post', boom)
        with pytest.raises(ai_mod.AIServiceError, match='无法连接'):
            svc._call([{'role': 'user', 'content': 'x'}])

    def test_aiserviceerror_is_runtime_error(self):
        """AIServiceError 必须是 RuntimeError 子类：入库识别端点按 RuntimeError 捕获并返回准确错误"""
        assert issubclass(ai_mod.AIServiceError, RuntimeError)


# ==========================================
#  smart_import：自然语言 → JSON（含输出清洗）
# ==========================================
class TestSmartImport:
    def _mock_db_context(self, monkeypatch):
        """smart_import 会先查现有商品/供应商构建上下文，统一 mock 掉"""
        monkeypatch.setattr(models_mod.InventoryModel, 'get_all', lambda: [])
        monkeypatch.setattr(models_mod.SupplierModel, 'get_all', lambda: [])

    def test_parses_fenced_json_with_trailing_comma(self, svc, monkeypatch):
        self._mock_db_context(monkeypatch)
        raw = (
            '```json\n'
            '[{"name": "A4纸", "sku": "PAP-A4", "quantity": 30,},\n'
            ' {"name": "电容", "sku": "CAP-1", "quantity": 100,}]\n'
            '```\n'
        )
        monkeypatch.setattr(svc, '_call', lambda messages: raw)

        items = svc.smart_import('今天买了30包A4纸和100个电容')
        assert len(items) == 2
        assert items[0]['name'] == 'A4纸' and items[0]['quantity'] == 30

    def test_prompt_includes_existing_products_and_suppliers(self, svc, monkeypatch):
        captured = {}

        def fake_call(messages):
            captured['user_msg'] = messages[1]['content']
            return '[{"name": "A4纸", "sku": "PAP-A4", "quantity": 30}]'

        monkeypatch.setattr(svc, '_call', fake_call)
        monkeypatch.setattr(models_mod.InventoryModel, 'get_all', lambda: [
            {'product_name': '螺丝 M6', 'sku': 'SCR-1', 'category_name': '机械零件'}])
        monkeypatch.setattr(models_mod.SupplierModel, 'get_all', lambda: [
            {'id': 1, 'name': '华强五金'}])

        svc.smart_import('再买30包A4纸')

        # 上下文应包含现有商品 SKU 与供应商名，便于 AI 复用
        assert 'SCR-1' in captured['user_msg']
        assert '华强五金' in captured['user_msg']

    def test_garbage_output_raises_parse_error(self, svc, monkeypatch):
        self._mock_db_context(monkeypatch)
        monkeypatch.setattr(svc, '_call', lambda messages: '抱歉，我无法解析这段内容。')
        with pytest.raises(ValueError, match='格式错误'):
            svc.smart_import('随便说点什么')


# ==========================================
#  recognize_inbound_image：OCR + AI 解析入库单
# ==========================================
class TestRecognizeInboundImage:
    def test_success_with_fenced_output(self, svc, monkeypatch):
        monkeypatch.setattr(svc, 'ocr_image', lambda b64: '商品: 螺丝 M6\n数量: 100\n单价: 0.05')
        monkeypatch.setattr(svc, '_call', lambda messages: (
            '[{"name": "螺丝 M6", "quantity": 100, "unit_price": 0.05,},]'
        ))

        items = svc.recognize_inbound_image('base64data')
        assert len(items) == 1
        assert items[0]['name'] == '螺丝 M6'
        assert items[0]['quantity'] == 100

    def test_empty_ocr_text_raises(self, svc, monkeypatch):
        monkeypatch.setattr(svc, 'ocr_image', lambda b64: '')
        with pytest.raises(RuntimeError, match='未识别到有效文字'):
            svc.recognize_inbound_image('base64data')

    def test_ocr_failure_mentions_tesseract(self, svc, monkeypatch):
        def broken(b64):
            raise Exception('tesseract not found')
        monkeypatch.setattr(svc, 'ocr_image', broken)
        with pytest.raises(RuntimeError) as ei:
            svc.recognize_inbound_image('base64data')
        assert 'tesseract' in str(ei.value)

    def test_prompt_contains_ocr_text(self, svc, monkeypatch):
        captured = {}

        def fake_call(messages):
            captured['user_msg'] = messages[1]['content']
            return '[{"name": "X", "quantity": 1}]'

        monkeypatch.setattr(svc, 'ocr_image', lambda b64: 'OCR识别出的文字内容')
        monkeypatch.setattr(svc, '_call', fake_call)
        svc.recognize_inbound_image('base64data')
        assert 'OCR识别出的文字内容' in captured['user_msg']


# ==========================================
#  health_check：LM Studio 可用性检查
# ==========================================
class TestHealthCheck:
    def test_ok_lists_models(self, svc, monkeypatch):
        fake_resp = SimpleNamespace(status_code=200, json=lambda: {'data': [{'id': 'm1'}]})
        monkeypatch.setattr(ai_mod.requests, 'get', lambda *a, **k: fake_resp)
        ok, msg = svc.health_check()
        assert ok is True and 'm1' in msg

    def test_bad_status_code(self, svc, monkeypatch):
        fake_resp = SimpleNamespace(status_code=503, json=lambda: {})
        monkeypatch.setattr(ai_mod.requests, 'get', lambda *a, **k: fake_resp)
        ok, msg = svc.health_check()
        assert ok is False and '异常状态码' in msg

    def test_connection_error(self, svc, monkeypatch):
        def boom(*a, **k):
            raise requests_lib.exceptions.ConnectionError('refused')
        monkeypatch.setattr(ai_mod.requests, 'get', boom)
        ok, msg = svc.health_check()
        assert ok is False and '无法连接' in msg


# ==========================================
#  chat_stream：流式生成（SSE token / done）
# ==========================================
class TestChatStream:
    def _make_stream_svc(self, svc, monkeypatch, chunks):
        """用给定 chunk 序列构造可流式调度 + 空上下文的 svc"""
        monkeypatch.setattr(models_mod.InventoryModel, 'get_all', lambda: [])
        monkeypatch.setattr(models_mod.StatsModel, 'get_dashboard', lambda: {
            'total_products': 1, 'total_categories': 1, 'total_quantity': 10, 'low_stock_count': 0})
        monkeypatch.setattr(models_mod.InventoryModel, 'get_low_stock', lambda: [])
        monkeypatch.setattr(models_mod.TransactionModel, 'get_all', lambda limit=30: [])
        monkeypatch.setattr(models_mod.SupplierModel, 'get_all', lambda: [])
        monkeypatch.setattr(models_mod.CustomerModel, 'get_all', lambda: [])

        def fake_stream(self, messages):
            for c in chunks:
                # 现在 _stream_completion 产出 (kind, text) 二元的（thinking/token）
                yield ('token', c)

        monkeypatch.setattr(ai_mod.AIService, '_stream_completion', fake_stream)

    def test_streams_plain_reply_without_action(self, svc, monkeypatch):
        self._make_stream_svc(svc, monkeypatch, ['你好', '，', '我是助手'])
        events = [e for e in svc.chat_stream('hi')]
        tokens = ''.join(e['content'] for e in events if e['type'] == 'token')
        done = [e for e in events if e['type'] == 'done'][0]
        assert tokens == '你好，我是助手'
        assert done['reply'] == '你好，我是助手'
        assert done['actions'] is None

    def test_forward_thinking_events(self, svc, monkeypatch):
        # 混合 thinking + token：thinking 单独解包为 thinking 事件，不混入正文
        def fake_stream(self, messages):
            yield ('thinking', '思考中')
        monkeypatch.setattr(ai_mod.AIService, '_stream_completion', fake_stream)
        # build_inventory_context 需要 DB：mock 掉
        monkeypatch.setattr(ai_mod.InventoryModel, 'get_all', lambda: [])
        monkeypatch.setattr(ai_mod.StatsModel, 'get_dashboard', lambda: {
            'total_products': 1, 'total_categories': 1, 'total_quantity': 10, 'low_stock_count': 0})
        monkeypatch.setattr(ai_mod.InventoryModel, 'get_low_stock', lambda: [])
        monkeypatch.setattr(ai_mod.TransactionModel, 'get_all', lambda limit=30: [])
        monkeypatch.setattr(ai_mod.SupplierModel, 'get_all', lambda: [])
        monkeypatch.setattr(ai_mod.CustomerModel, 'get_all', lambda: [])
        # 只 yield thinking，不 yield token → 正文应为空
        events = [e for e in svc.chat_stream('hi')]
        th = [e['content'] for e in events if e['type'] == 'thinking']
        assert th and '思考中' in th[0]
        done = [e for e in events if e['type'] == 'done'][0]
        assert done['reply'] == ''   # 没有任何 token → 正文为空

    def test_streams_reply_and_splits_at_action_fence(self, svc, monkeypatch):
        chunks = ['已为您入库。\n', '```action\n',
                  '[{"action": "stock_in", "sku": "A1", "quantity": 5}]\n', '```']
        self._make_stream_svc(svc, monkeypatch, chunks)
        events = [e for e in svc.chat_stream('入库5个A1')]
        done = [e for e in events if e['type'] == 'done'][0]
        # 正文不含指令，action 被剥离
        assert done['reply'].strip() == '已为您入库。'
        assert done['actions'] == [{'action': 'stock_in', 'sku': 'A1', 'quantity': 5}]
        # 流式 token 只推正文，不推指令
        tokens = ''.join(e['content'] for e in events if e['type'] == 'token')
        assert 'stock_in' not in tokens

    def test_reply_is_not_yielded_twice_when_fence_late(self, svc, monkeypatch):
        """回归：围栏出现在多 chunk 之后时，不得把已推送的正文重复推送"""
        chunks = ['第一段', '第二段', '```action\n[{"action":"stock_out","sku":"B","quantity":1}]\n```']
        self._make_stream_svc(svc, monkeypatch, chunks)
        events = [e for e in svc.chat_stream('x')]
        tokens = ''.join(e['content'] for e in events if e['type'] == 'token')
        assert tokens == '第一段第二段'          # 无重复
        done = [e for e in events if e['type'] == 'done'][0]
        assert done['reply'] == '第一段第二段'    # 剥离指令后正文一致


# ==========================================
#  analyze_stream：流式分析（token / done）
# ==========================================
class TestAnalyzeStream:
    def _patch(self, monkeypatch, chunks, svc):
        monkeypatch.setattr(ai_mod.InventoryModel, 'get_all', lambda: [])
        monkeypatch.setattr(ai_mod.StatsModel, 'get_dashboard', lambda: {
            'total_products': 1, 'total_categories': 1, 'total_quantity': 10, 'low_stock_count': 0})
        monkeypatch.setattr(ai_mod.InventoryModel, 'get_low_stock', lambda: [])
        monkeypatch.setattr(ai_mod.TransactionModel, 'get_all', lambda limit=30: [])
        monkeypatch.setattr(ai_mod.SupplierModel, 'get_all', lambda: [])
        monkeypatch.setattr(ai_mod.CustomerModel, 'get_all', lambda: [])

        def fake_stream(self, messages):
            for c in chunks:
                # _stream_completion 产出 (kind, text) 二元组
                yield ('token', c)
        monkeypatch.setattr(ai_mod.AIService, '_stream_completion', fake_stream)

    def test_streams_tokens_and_done(self, svc, monkeypatch):
        self._patch(monkeypatch, ['第一', '段低库存分析', '结果'], svc)
        events = [e for e in svc.analyze_stream('low_stock')]
        tokens = ''.join(e['content'] for e in events if e['type'] == 'token')
        done = [e for e in events if e['type'] == 'done'][0]
        assert tokens == '第一段低库存分析结果'
        assert done['data'] == '第一段低库存分析结果'

    def test_forward_analyze_thinking(self, svc, monkeypatch):
        def fake_stream(self, messages):
            yield ('thinking', '先思考')
            yield ('token', '结论')
        monkeypatch.setattr(ai_mod.AIService, '_stream_completion', fake_stream)
        monkeypatch.setattr(ai_mod.InventoryModel, 'get_all', lambda: [])
        monkeypatch.setattr(ai_mod.StatsModel, 'get_dashboard', lambda: {
            'total_products': 1, 'total_categories': 1, 'total_quantity': 10, 'low_stock_count': 0})
        monkeypatch.setattr(ai_mod.InventoryModel, 'get_low_stock', lambda: [])
        monkeypatch.setattr(ai_mod.TransactionModel, 'get_all', lambda limit=30: [])
        monkeypatch.setattr(ai_mod.SupplierModel, 'get_all', lambda: [])
        monkeypatch.setattr(ai_mod.CustomerModel, 'get_all', lambda: [])
        events = [e for e in svc.analyze_stream('low_stock')]
        th = [e['content'] for e in events if e['type'] == 'thinking']
        tk = ''.join(e['content'] for e in events if e['type'] == 'token')
        done = [e for e in events if e['type'] == 'done'][0]
        assert th == ['先思考']
        assert tk == '结论'
        assert done['data'] == '结论'
