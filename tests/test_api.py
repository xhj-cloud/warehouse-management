"""
Flask API 端点测试：使用 test_client + mock 模型层，不依赖真实数据库。
覆盖：页面渲染、CRUD、出入库、Excel 导入/导出、审计日志、AI 对话执行动作等。

注意：所有对共享对象（模型类方法 / app_mod.db）的替换必须走 monkeypatch，
避免状态泄漏到其他测试文件。
"""

import os
from io import BytesIO
from types import SimpleNamespace

import openpyxl
import pytest
import requests as requests_lib


def make_recorder(result=None):
    """生成一个记录调用参数的 mock 函数"""
    calls = []

    def rec(*args, **kwargs):
        calls.append({'args': args, 'kwargs': kwargs})
        return result

    rec.calls = calls
    return rec


def make_xlsx(rows):
    """rows: 第一行为表头，返回 BytesIO"""
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ==========================================
#  页面与静态资源
# ==========================================
class TestPages:
    def test_index_renders(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert '仓库管理系统' in resp.data.decode('utf-8')

    def test_static_css_served(self, client):
        resp = client.get('/static/css/style.css')
        assert resp.status_code == 200


# ==========================================
#  分类 API
# ==========================================
class TestCategories:
    def test_list(self, app_mod, client, monkeypatch):
        rec = make_recorder([{'id': 1, 'name': '电子产品'}])
        monkeypatch.setattr(app_mod.CategoryModel, 'get_all', rec)
        resp = client.get('/api/categories')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data'][0]['name'] == '电子产品'

    def test_create(self, app_mod, client, monkeypatch):
        rec = make_recorder(5)
        monkeypatch.setattr(app_mod.CategoryModel, 'create', rec)
        resp = client.post('/api/categories', json={'name': '新分类'})
        assert resp.status_code == 200
        assert resp.get_json()['id'] == 5
        assert rec.calls[0]['args'][0] == '新分类'

    def test_create_missing_name_returns_400(self, app_mod, client, monkeypatch):
        monkeypatch.setattr(app_mod.CategoryModel, 'create', make_recorder(1))
        resp = client.post('/api/categories', json={'description': '没有名字'})
        assert resp.status_code == 400

    def test_update_and_delete(self, app_mod, client, monkeypatch):
        upd = make_recorder(None)
        dele = make_recorder(None)
        monkeypatch.setattr(app_mod.CategoryModel, 'update', upd)
        monkeypatch.setattr(app_mod.CategoryModel, 'delete', dele)
        assert client.put('/api/categories/1', json={'name': '改名'}).status_code == 200
        assert upd.calls[0]['args'][:3] == (1, '改名', '')
        assert client.delete('/api/categories/1').status_code == 200
        assert dele.calls[0]['args'][0] == 1


# ==========================================
#  供应商 / 客户 API（含审计日志）
# ==========================================
class TestSuppliers:
    def test_create_records_audit(self, app_mod, client, fake_db, monkeypatch):
        create = make_recorder(8)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.SupplierModel, 'create', create)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/suppliers', json={'name': '华强五金', 'contact_person': '张经理'})
        assert resp.status_code == 200
        assert resp.get_json()['id'] == 8
        # create(name, contact, phone, email, address, notes)
        assert create.calls[0]['args'][:3] == ('华强五金', '张经理', '')

        action, table, record_id = audit.calls[0]['args'][:3]
        assert (action, table, record_id) == ('create', 'suppliers', 8)
        assert audit.calls[0]['kwargs']['operator'] == '管理员'

    def test_update_and_delete_record_audit(self, app_mod, client, fake_db, monkeypatch):
        get_by_id = make_recorder({'id': 1, 'name': '旧名'})
        upd = make_recorder(None)
        dele = make_recorder(None)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.SupplierModel, 'get_by_id', get_by_id)
        monkeypatch.setattr(app_mod.SupplierModel, 'update', upd)
        monkeypatch.setattr(app_mod.SupplierModel, 'delete', dele)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        assert client.put('/api/suppliers/1', json={'name': '新名'}).status_code == 200
        assert client.delete('/api/suppliers/1').status_code == 200
        actions = [c['args'][0] for c in audit.calls]
        assert actions == ['update', 'delete']

    def test_create_audit_log_in_same_transaction(self, app_mod, client, fake_db, monkeypatch):
        """回归：审计日志必须与主操作在同一事务内写入（同连接、一起提交/回滚）"""
        create = make_recorder(8)
        in_tx_at_audit = []

        def spy(action, table_name, record_id, old_data=None, new_data=None, operator='system'):
            in_tx_at_audit.append(fake_db.in_transaction)

        monkeypatch.setattr(app_mod.SupplierModel, 'create', create)
        monkeypatch.setattr(app_mod.AuditLog, 'log', spy)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/suppliers', json={'name': '华强五金'})
        assert resp.status_code == 200
        assert in_tx_at_audit == [True]

    def test_create_audit_failure_returns_error(self, app_mod, client, fake_db, monkeypatch):
        """回归：审计日志写失败时接口必须报错（事务回滚），不能静默成功"""
        create = make_recorder(8)

        def boom(*a, **k):
            raise RuntimeError('audit db down')

        monkeypatch.setattr(app_mod.SupplierModel, 'create', create)
        monkeypatch.setattr(app_mod.AuditLog, 'log', boom)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/suppliers', json={'name': '华强五金'})
        assert resp.status_code == 400
        assert 'audit db down' in resp.get_json()['error']


class TestCustomers:
    def test_create_records_audit(self, app_mod, client, fake_db, monkeypatch):
        create = make_recorder(4)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.CustomerModel, 'create', create)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/customers', json={'name': '中建公司'})
        assert resp.status_code == 200
        assert resp.get_json()['id'] == 4
        action, table, record_id = audit.calls[0]['args'][:3]
        assert (action, table, record_id) == ('create', 'customers', 4)


# ==========================================
#  商品 API
# ==========================================
class TestProducts:
    def test_detail_not_found_404(self, app_mod, client, monkeypatch):
        monkeypatch.setattr(app_mod.ProductModel, 'get_by_id', make_recorder(None))
        resp = client.get('/api/products/999')
        assert resp.status_code == 404
        assert '不存在' in resp.get_json()['error']

    def test_create_with_initial_stock(self, app_mod, client, fake_db, monkeypatch):
        create = make_recorder(9)
        stock_in = make_recorder(None)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.ProductModel, 'create', create)
        monkeypatch.setattr(app_mod.InventoryModel, 'stock_in', stock_in)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/products', json={
            'name': '螺丝 M6', 'sku': 'S-1', 'quantity': 10,
            'unit_price': 2.5, 'location': 'A-01', 'min_stock': 5,
        })
        assert resp.status_code == 200
        assert resp.get_json()['id'] == 9

        # 初始库存走 stock_in（生成入库流水）
        si = stock_in.calls[0]
        assert si['args'][0] == 9 and si['kwargs']['quantity'] == 10
        assert si['kwargs']['unit_price'] == 2.5

        # 库位/阈值单独 UPDATE inventory
        hits = fake_db.assert_executed('UPDATE inventory SET location')
        assert hits[0][1][:4] == ('A-01', 5, 9999, 9)

    def test_create_missing_sku_returns_400(self, app_mod, client, fake_db, monkeypatch):
        create = make_recorder(1)
        monkeypatch.setattr(app_mod.ProductModel, 'create', create)
        monkeypatch.setattr(app_mod, 'db', fake_db)
        resp = client.post('/api/products', json={'name': '没有SKU'})
        assert resp.status_code == 400

    def test_create_without_quantity_still_sets_location(self, app_mod, client, fake_db, monkeypatch):
        """回归测试：不填初始库存时，库位/最低/最高库存仍应写入（不丢失）"""
        create = make_recorder(9)
        stock_in = make_recorder(None)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.ProductModel, 'create', create)
        monkeypatch.setattr(app_mod.InventoryModel, 'stock_in', stock_in)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/products', json={
            'name': '打印纸 A4', 'sku': 'P-1', 'location': 'B-02', 'min_stock': 20,
        })
        assert resp.status_code == 200
        # 未提供数量 → 不应调用 stock_in
        assert stock_in.calls == []
        # 但库位/阈值照常写入
        hits = fake_db.assert_executed('UPDATE inventory SET location')
        assert hits[0][1] == ('B-02', 20, 9999, 9)

    def test_create_negative_quantity_rejected_before_write(self, app_mod, client, fake_db, monkeypatch):
        """回归：负数量创建商品必须直接 400，且任何数据都不落库（此前商品已先落库）"""
        create = make_recorder(9)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.ProductModel, 'create', create)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/products', json={
            'name': '螺丝 M6', 'sku': 'S-1', 'quantity': -5,
        })
        assert resp.status_code == 400
        assert '不能为负数' in resp.get_json()['error']
        # 校验发生在任何写入之前：商品未创建、无审计日志
        assert create.calls == []
        assert audit.calls == []

    def test_update_negative_quantity_rejected_before_write(self, app_mod, client, fake_db, monkeypatch):
        """回归：负数量更新商品必须直接 400，且任何数据都不落库（此前字段已先更新）"""
        upd = make_recorder(None)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.ProductModel, 'get_by_id', make_recorder({'id': 1}))
        monkeypatch.setattr(app_mod.ProductModel, 'update', upd)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.put('/api/products/1', json={
            'name': '螺丝 M6', 'sku': 'S-1', 'quantity': -5,
        })
        assert resp.status_code == 400
        assert upd.calls == []
        assert audit.calls == []


# ==========================================
#  库存 / 出入库 API
# ==========================================
class TestInventory:
    def test_low_stock_passes_threshold(self, app_mod, client, monkeypatch):
        rec = make_recorder([])
        monkeypatch.setattr(app_mod.InventoryModel, 'get_low_stock', rec)
        resp = client.get('/api/inventory/low-stock?threshold=25')
        assert resp.status_code == 200
        assert rec.calls[0]['args'] == (25,)

    def test_stock_in_updates_product_price_and_supplier(self, app_mod, client, fake_db, monkeypatch):
        stock_in = make_recorder(None)
        audit = make_recorder(None)
        monkeypatch.setattr(app_mod.InventoryModel, 'stock_in', stock_in)
        monkeypatch.setattr(app_mod.AuditLog, 'log', audit)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/inventory/stock-in', json={
            'product_id': 7, 'quantity': 10, 'unit_price': 2.5, 'supplier_id': 3,
        })
        assert resp.status_code == 200
        assert resp.get_json()['message'] == '入库成功'

        # 最近进价与供应商同步回写 products 表
        price_hits = fake_db.assert_executed('UPDATE products SET unit_price')
        assert price_hits[0][1] == (2.5, 7)
        sup_hits = fake_db.assert_executed('UPDATE products SET supplier_id')
        assert sup_hits[0][1] == (3, 7)

        action, table, record_id = audit.calls[0]['args'][:3]
        assert (action, table, record_id) == ('stock_in', 'inventory', 7)

    def test_stock_out_insufficient_returns_400(self, app_mod, client, fake_db, monkeypatch):
        def boom(*a, **k):
            raise ValueError('库存不足！当前库存: 3, 需要出库: 10')
        monkeypatch.setattr(app_mod.InventoryModel, 'stock_out', boom)
        monkeypatch.setattr(app_mod, 'db', fake_db)

        resp = client.post('/api/inventory/stock-out', json={
            'product_id': 7, 'quantity': 10,
        })
        assert resp.status_code == 400
        assert '库存不足' in resp.get_json()['error']


# ==========================================
#  DECIMAL 金额序列化
# ==========================================
class TestDecimalSerialization:
    def test_decimal_serialized_as_json_number(self, app_mod, client, monkeypatch):
        """回归：DECIMAL 金额字段经 CustomJSONProvider 输出为 JSON 数字（不再在 DB 层转 float）"""
        from decimal import Decimal
        rows = [{'id': 1, 'name': '螺丝', 'sku': 'S-1',
                 'unit_price': Decimal('2.50'), 'sale_price': Decimal('3.75')}]
        monkeypatch.setattr(app_mod.ProductModel, 'get_all', make_recorder(rows))

        resp = client.get('/api/products')
        assert resp.status_code == 200
        data = resp.get_json()['data'][0]
        assert data['unit_price'] == 2.5
        assert data['sale_price'] == 3.75


# ==========================================
#  交易记录 / 审计日志 API
# ==========================================
class TestTransactionsAndAudit:
    def test_transactions_respects_limit(self, app_mod, client, monkeypatch):
        rec = make_recorder([])
        monkeypatch.setattr(app_mod.TransactionModel, 'get_all', rec)
        resp = client.get('/api/transactions?limit=5')
        assert resp.status_code == 200
        assert rec.calls[0]['args'] == (5,)

    def test_audit_log_filters_by_table(self, app_mod, client, monkeypatch):
        by_table = make_recorder([{'id': 1}])
        recent = make_recorder([])
        monkeypatch.setattr(app_mod.AuditLog, 'get_by_table', by_table)
        monkeypatch.setattr(app_mod.AuditLog, 'get_recent', recent)

        resp = client.get('/api/audit-log?table=suppliers&limit=5')
        assert resp.status_code == 200
        assert by_table.calls[0]['args'] == ('suppliers', 5)
        assert recent.calls == []

    def test_audit_log_default_recent(self, app_mod, client, monkeypatch):
        recent = make_recorder([])
        monkeypatch.setattr(app_mod.AuditLog, 'get_recent', recent)
        resp = client.get('/api/audit-log')
        assert resp.status_code == 200
        assert recent.calls[0]['args'] == (200,)


# ==========================================
#  Excel 上传导入 API
# ==========================================
class TestUpload:
    def _patch_import_models(self, app_mod, fake_db, monkeypatch):
        """把导入流程涉及的模型层替换为 mock（全部走 monkeypatch，自动还原）"""
        monkeypatch.setattr(app_mod.CategoryModel, 'get_all', make_recorder([]))
        monkeypatch.setattr(app_mod.ProductModel, 'get_by_sku', make_recorder(None))
        monkeypatch.setattr(app_mod.ProductModel, 'create', make_recorder(1))
        monkeypatch.setattr(app_mod.ExcelUploadModel, 'create', make_recorder(7))
        self.update_status = make_recorder(None)
        monkeypatch.setattr(app_mod.ExcelUploadModel, 'update_status', self.update_status)
        monkeypatch.setattr(app_mod, 'db', fake_db)

    def test_rejects_bad_extension(self, client):
        buf = BytesIO(b'MZ...')
        resp = client.post('/api/upload', data={
            'file': (buf, 'evil.exe'), 'mode': 'replace'},
            content_type='multipart/form-data')
        assert resp.status_code == 400
        assert '仅支持' in resp.get_json()['error']

    def test_xlsx_import_creates_product_and_transaction(self, app_mod, client, fake_db, monkeypatch):
        self._patch_import_models(app_mod, fake_db, monkeypatch)
        buf = make_xlsx([['商品名称', '数量'], ['螺丝 M6', 50]])

        resp = client.post('/api/upload', data={
            'file': (buf, 'test.xlsx'), 'mode': 'replace'},
            content_type='multipart/form-data')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['rows_imported'] == 1

        # 上传状态最终标记为 success（第一次调用是 processing）
        final_status = self.update_status.calls[-1]
        assert final_status['args'][1] == 'success'
        assert final_status['args'][2] == 1

        # 生成入库流水，批次号关联上传记录
        hits = fake_db.assert_executed('INSERT INTO transactions')
        assert 'Excel-7' in str(hits[0][1])

    def test_xlsx_import_skips_rows_without_name(self, app_mod, client, fake_db, monkeypatch):
        """回归测试：空单元格被 pandas 读成 NaN，str() 后变成字符串 "nan"，无名称的行必须被跳过"""
        self._patch_import_models(app_mod, fake_db, monkeypatch)
        buf = make_xlsx([['商品名称', '数量'], ['', 50], [None, 30]])

        resp = client.post('/api/upload', data={
            'file': (buf, 'empty.xlsx'), 'mode': 'replace'},
            content_type='multipart/form-data')
        assert resp.status_code == 200
        # 期望行为：无名称的行应被跳过
        assert resp.get_json()['data']['rows_imported'] == 0

    def test_xlsx_import_negative_quantity_not_written(self, app_mod, client, fake_db, monkeypatch):
        """回归测试：数量为负的行不应生成出入库流水"""
        self._patch_import_models(app_mod, fake_db, monkeypatch)
        buf = make_xlsx([['商品名称', '数量'], ['螺丝 M6', -5]])

        resp = client.post('/api/upload', data={
            'file': (buf, 'neg.xlsx'), 'mode': 'replace'},
            content_type='multipart/form-data')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['data']['errors']           # 有行级错误提示
        assert any('负' in e for e in body['data']['errors'])
        assert not fake_db.find_executed('INSERT INTO transactions')  # 不写负库存流水

    def test_xlsx_import_duplicate_sku_in_file_skipped(self, app_mod, client, fake_db, monkeypatch):
        """回归测试：文件内重复 SKU 只导入首条，避免后者静默覆盖前者"""
        self._patch_import_models(app_mod, fake_db, monkeypatch)
        buf = make_xlsx([['商品名称', 'SKU', '数量'], ['螺丝 A', 'S1', 10], ['螺丝 B', 'S1', 20]])

        resp = client.post('/api/upload', data={
            'file': (buf, 'dup.xlsx'), 'mode': 'replace'},
            content_type='multipart/form-data')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['data']['rows_imported'] == 1
        assert any('重复' in e for e in body['data']['errors'])
        fake_db.assert_executed('INSERT INTO transactions', count=1)


# ==========================================
#  Excel 导出 API（出库单 / 库存清单）
# ==========================================
class TestExport:
    def test_order_export_generates_xlsx_with_total(self, client):
        resp = client.post('/api/order/export', json={
            'customer': '中建公司', 'operator': '张三',
            'items': [
                {'name': '螺丝 M6', 'sku': 'S1', 'quantity': 2, 'sale_price': 0.5},
                {'name': '打印纸 A4', 'sku': 'P1', 'quantity': 3, 'sale_price': 15},
            ],
        })
        assert resp.status_code == 200
        assert 'spreadsheetml' in resp.content_type
        # xlsx 是 zip 格式，魔数 PK
        assert resp.data[:2] == b'PK'

        wb = openpyxl.load_workbook(BytesIO(resp.data))
        ws = wb.active
        total_val = None
        for row in ws.iter_rows():
            for cell in row:
                if cell.value == '合计':
                    total_val = ws.cell(row=cell.row, column=7).value
        assert total_val is not None
        assert total_val == pytest.approx(2 * 0.5 + 3 * 15)

    def test_export_inventory_returns_xlsx(self, client):
        resp = client.post('/api/order/export-inventory', json={
            'items': [{'product_name': '螺丝 M6', 'quantity': 10}],
        })
        assert resp.status_code == 200
        assert resp.data[:2] == b'PK'


# ==========================================
#  AI API（mock LM Studio HTTP 层）
# ==========================================
class TestAI:
    def test_health_ok(self, app_mod, client, monkeypatch):
        import ai_service as ai_mod
        fake_resp = SimpleNamespace(status_code=200, json=lambda: {'data': [{'id': 'qwen3.6-35b-a3b'}]})
        monkeypatch.setattr(ai_mod.requests, 'get', lambda *a, **k: fake_resp)

        resp = client.get('/api/ai/health')
        body = resp.get_json()
        assert body['success'] is True
        assert 'qwen3.6-35b-a3b' in body['message']

    def test_health_down(self, app_mod, client, monkeypatch):
        import ai_service as ai_mod

        def boom(*a, **k):
            raise requests_lib.exceptions.ConnectionError('refused')
        monkeypatch.setattr(ai_mod.requests, 'get', boom)

        resp = client.get('/api/ai/health')
        body = resp.get_json()
        assert body['success'] is False
        assert '无法连接' in body['message']

    def test_chat_add_supplier_action(self, app_mod, client, monkeypatch):
        reply_text = (
            '好的，已为您添加供应商。\n'
            '```action\n'
            '[{"action": "add_supplier", "name": "华为", "contact": "张经理", "phone": "13800000000"}]\n'
            '```\n'
        )
        monkeypatch.setattr(app_mod.ai_service, 'chat', lambda msg: reply_text)

        create = make_recorder(9)
        monkeypatch.setattr(app_mod.SupplierModel, 'create', create)

        resp = client.post('/api/ai/chat', json={'message': '新增供应商华为，联系人张经理'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['actions'] == ['新增供应商: 华为']
        assert '已执行' in body['data']
        assert create.calls[0]['args'][0] == '华为'

    def test_chat_stock_in_auto_category(self, app_mod, client, monkeypatch):
        reply_text = (
            '好的，已入库。\n'
            '```action\n'
            '[{"action": "stock_in", "sku": "NEW-1", "quantity": 5, "name": "电阻 10K"}]\n'
            '```\n'
        )
        monkeypatch.setattr(app_mod.ai_service, 'chat', lambda msg: reply_text)

        cat_create = make_recorder(11)
        prod_create = make_recorder(5)
        stock_in = make_recorder(None)
        monkeypatch.setattr(app_mod.ProductModel, 'get_by_sku', make_recorder(None))
        monkeypatch.setattr(app_mod.CategoryModel, 'get_all', make_recorder([]))
        monkeypatch.setattr(app_mod.CategoryModel, 'create', cat_create)
        monkeypatch.setattr(app_mod.ProductModel, 'create', prod_create)
        monkeypatch.setattr(app_mod.InventoryModel, 'stock_in', stock_in)

        resp = client.post('/api/ai/chat', json={'message': '入库 5 个电阻 10K'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert any('入库' in a for a in body['actions'])

        # "电阻" 命中关键词 → 自动归类到「电子产品」
        assert cat_create.calls[0]['args'][0] == '电子产品'
        # 新商品创建后执行入库
        assert prod_create.calls[0]['args'][:2] == ('电阻 10K', 'NEW-1')
        assert stock_in.calls[0]['args'][0] == 5

    def test_chat_create_order_writes_excel(self, app_mod, client, tmp_path, fake_db, monkeypatch):
        reply_text = (
            '出库单已创建。\n'
            '```action\n'
            '[{"action": "create_order", "customer": "中建公司", "operator": "张三",\n'
            '  "warehouse": "WH-A01",\n'
            '  "items": [{"sku": "SCR-1", "quantity": 2, "price": 0.5}]}]\n'
            '```\n'
        )
        monkeypatch.setattr(app_mod.ai_service, 'chat', lambda msg: reply_text)

        cust_create = make_recorder(3)
        stock_out = make_recorder(None)
        monkeypatch.setattr(app_mod.CustomerModel, 'get_all', make_recorder([]))
        monkeypatch.setattr(app_mod.CustomerModel, 'create', cust_create)
        monkeypatch.setattr(app_mod.ProductModel, 'get_by_sku', make_recorder(
            {'id': 7, 'name': '螺丝 M6', 'specification': '不锈钢'}))
        monkeypatch.setattr(app_mod.InventoryModel, 'stock_out', stock_out)
        # 出库单预检需要查询库存：mock db 返回充足库存
        monkeypatch.setattr(app_mod, 'db', fake_db)
        fake_db.one_side_effect = lambda sql, params=None: {'quantity': 10}

        # 出库单 Excel 写入临时目录，避免污染真实 uploads/
        monkeypatch.setattr(app_mod, 'UPLOAD_FOLDER', str(tmp_path))

        resp = client.post('/api/ai/chat', json={'message': '创建出库单给中建公司'})
        assert resp.status_code == 200
        body = resp.get_json()
        # 出库执行：客户 id=3、单价 0.5
        so = stock_out.calls[0]
        assert so['args'][0] == 7 and so['args'][1] == 2
        assert so['kwargs']['customer_id'] == 3
        assert so['kwargs']['unit_price'] == 0.5

        # Excel 文件已生成，回复带下载链接
        files = os.listdir(tmp_path)
        assert any(f.startswith('出库单_中建公司') and f.endswith('.xlsx') for f in files)
        assert '/uploads/' in body['data']
