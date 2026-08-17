"""
models.py 单元测试：库存增减逻辑、超卖检查、审计日志序列化等。
全部使用 FakeDB，不依赖真实数据库。
"""

import decimal
from datetime import date, datetime

import pytest

import models as models_mod
from tests.fake_db import FakeDB


# ==========================================
#  _clean_value：查询结果类型清洗
# ==========================================
class TestCleanValue:
    def test_decimal_to_float(self):
        assert models_mod._clean_value(decimal.Decimal('1.5')) == 1.5

    def test_datetime_to_isoformat(self):
        dt = datetime(2026, 7, 13, 10, 30, 0)
        assert models_mod._clean_value(dt) == '2026-07-13T10:30:00'

    def test_date_to_isoformat(self):
        assert models_mod._clean_value(date(2026, 7, 13)) == '2026-07-13'

    def test_bytes_decoded(self):
        assert models_mod._clean_value(b'\xe4\xb8\xad\xe6\x96\x87') == '中文'

    def test_nested_dict_and_list(self):
        data = {'a': [decimal.Decimal('2'), date(2026, 1, 1)], 'b': b'x'}
        assert models_mod._clean_value(data) == {'a': [2.0, '2026-01-01'], 'b': 'x'}

    def test_plain_values_unchanged(self):
        for v in (42, 3.14, 'text', None, True):
            assert models_mod._clean_value(v) == v


# ==========================================
#  InventoryModel.stock_in / stock_out
# ==========================================
class TestStockIn:
    def test_stock_in_creates_inventory_row_when_missing(self, monkeypatch):
        db = FakeDB()
        db.update_affected = 0   # 原子自增未命中（无库存行）→ 走补建分支
        db.one_side_effect = lambda sql, params=None: None
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.InventoryModel.stock_in(7, 10, batch_no='B1', operator='张三', unit_price=2.5)

        # 1) 原子自增未命中  2) 补建库存行  3) 写入库流水
        assert 'UPDATE inventory SET quantity = quantity +' in db.executed[0][0]
        assert 'INSERT INTO inventory' in db.executed[1][0]
        txn_sql, txn_params = db.executed[2]
        assert 'INSERT INTO transactions' in txn_sql
        # params: product_id, quantity, unit_price, supplier_id, before, after, ...
        assert txn_params[:3] == (7, 10, 2.5)
        assert txn_params[4:6] == (0, 10)

    def test_stock_in_accumulates_on_existing_inventory(self, monkeypatch):
        db = FakeDB()
        # 模拟自增后回读到 3 + 5 = 8
        db.one_side_effect = lambda sql, params=None: {'quantity': 8}
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.InventoryModel.stock_in(7, 5)

        # 已有库存时不应再 INSERT inventory 行
        assert not any('INSERT INTO inventory' in s for s, _ in db.executed)
        update_sql, update_params = db.executed[0]
        assert 'UPDATE inventory SET quantity = quantity +' in update_sql
        assert update_params == (5, 7)
        txn_sql, txn_params = db.executed[1]
        assert 'INSERT INTO transactions' in txn_sql
        assert txn_params[4:6] == (3, 8)  # before=3, after=8

    def test_stock_in_rejects_non_positive_quantity(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        with pytest.raises(ValueError, match='大于 0'):
            models_mod.InventoryModel.stock_in(7, -5)
        assert db.executed == []


class TestStockOut:
    def test_stock_out_insufficient_raises_value_error(self, monkeypatch):
        db = FakeDB()
        db.update_affected = 0   # 条件扣减未命中 → 库存不足
        db.one_side_effect = lambda sql, params=None: {'quantity': 3}
        monkeypatch.setattr(models_mod, 'db', db)

        with pytest.raises(ValueError, match='库存不足'):
            models_mod.InventoryModel.stock_out(7, 10)

        # 校验失败时不应有任何事务写入
        assert not any('INSERT INTO transactions' in s for s, _ in db.executed)

    def test_stock_out_missing_inventory_raises(self, monkeypatch):
        db = FakeDB()
        db.update_affected = 0
        db.one_side_effect = lambda sql, params=None: None
        monkeypatch.setattr(models_mod, 'db', db)

        with pytest.raises(ValueError, match='库存记录不存在'):
            models_mod.InventoryModel.stock_out(7, 1)
        assert not any('INSERT INTO transactions' in s for s, _ in db.executed)

    def test_stock_out_success_records_before_after(self, monkeypatch):
        db = FakeDB()
        # 模拟扣减后回读 10 - 4 = 6
        db.one_side_effect = lambda sql, params=None: {'quantity': 6}
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.InventoryModel.stock_out(7, 4, customer_id=2, unit_price=9.9)

        update_sql, update_params = db.executed[0]
        assert 'UPDATE inventory SET quantity = quantity -' in update_sql
        assert update_params == (4, 7, 4)   # qty, product_id, 扣减条件 qty
        txn_sql, txn_params = db.executed[1]
        assert 'INSERT INTO transactions' in txn_sql
        # params: product_id, quantity, unit_price, before, after, ... , customer_id
        assert txn_params[:5] == (7, 4, 9.9, 10, 6)
        assert txn_params[-1] == 2

    def test_stock_out_rejects_non_positive_quantity(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        with pytest.raises(ValueError, match='大于 0'):
            models_mod.InventoryModel.stock_out(7, 0)
        assert db.executed == []


# ==========================================
#  ProductModel.create：自动初始化库存行
# ==========================================
class TestProductCreate:
    def test_create_returns_id_and_initializes_inventory(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        prod_id = models_mod.ProductModel.create('螺丝 M6', 'SCR-M6', unit_price=0.5)

        assert prod_id == 1  # FakeDB 自增 id
        insert_sqls = [s for s, _ in db.executed]
        assert any('INSERT INTO products' in s for s in insert_sqls)
        assert any('INSERT IGNORE INTO inventory' in s for s in insert_sqls)


# ==========================================
#  AuditLog：JSON 序列化（中文不转义）
# ==========================================
class TestAuditLog:
    def test_log_serializes_data_with_ensure_ascii_false(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.AuditLog.log('create', 'suppliers', 7,
                                new_data={'名称': '螺丝'}, operator='张三')

        sql, params = db.executed[0]
        assert 'INSERT INTO audit_log' in sql
        action, table, record_id, old_data, new_data, operator = params
        assert (action, table, record_id) == ('create', 'suppliers', 7)
        assert old_data is None
        # ensure_ascii=False：中文应原样保留而非 \uXXXX
        assert '"名称"' in new_data and '螺丝' in new_data
        assert operator == '张三'

    def test_log_without_data_stores_none(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.AuditLog.log('delete', 'products', 3, old_data={'name': 'x'})

        _, params = db.executed[0]
        assert params[3] is not None and params[4] is None


# ==========================================
#  TransactionModel：统计查询参数传递
# ==========================================
class TestTransactionStats:
    def test_get_stats_passes_days(self, monkeypatch):
        db = FakeDB()
        captured = {}

        def capture(sql, params=None):
            captured['sql'] = sql
            captured['params'] = params
            return [{'date': '2026-07-13', 'total_in': 5}]

        db.query_side_effect = capture
        monkeypatch.setattr(models_mod, 'db', db)

        result = models_mod.TransactionModel.get_stats(days=7)

        assert captured['params'] == (7,)
        assert 'INTERVAL' in captured['sql']
        assert result[0]['total_in'] == 5
