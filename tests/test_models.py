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
    def test_decimal_kept_as_is(self):
        """回归：DECIMAL 保持 Decimal 精确类型（不再转 float），JSON 序列化由 CustomJSONProvider 负责"""
        d = decimal.Decimal('1.5')
        assert models_mod._clean_value(d) is d

    def test_datetime_to_isoformat(self):
        dt = datetime(2026, 7, 13, 10, 30, 0)
        assert models_mod._clean_value(dt) == '2026-07-13T10:30:00'

    def test_date_to_isoformat(self):
        assert models_mod._clean_value(date(2026, 7, 13)) == '2026-07-13'

    def test_bytes_decoded(self):
        assert models_mod._clean_value(b'\xe4\xb8\xad\xe6\x96\x87') == '中文'

    def test_nested_dict_and_list(self):
        data = {'a': [decimal.Decimal('2'), date(2026, 1, 1)], 'b': b'x'}
        result = models_mod._clean_value(data)
        assert isinstance(result['a'][0], decimal.Decimal) and result['a'][0] == decimal.Decimal('2')
        assert result['a'][1] == '2026-01-01'
        assert result['b'] == 'x'

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


# ==========================================
#  低库存条件：列表页与仪表盘必须同口径
# ==========================================
class TestLowStockCondition:
    def test_get_low_stock_uses_per_product_threshold_with_fallback(self, monkeypatch):
        """回归：优先商品自己的 min_stock，未设置时回退全局阈值（不再有 OR 双条件误报）"""
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.InventoryModel.get_low_stock()

        sql, params = db.queries[0]
        assert 'IF(i.min_stock > 0, i.min_stock' in sql
        assert 'i.min_stock OR' not in sql   # 旧的双条件已移除
        assert params == (models_mod.DEFAULT_LOW_STOCK_THRESHOLD,)

    def test_get_low_stock_custom_threshold(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.InventoryModel.get_low_stock(25)

        sql, params = db.queries[0]
        assert params == (25,)

    def test_dashboard_low_stock_matches_list_condition(self, monkeypatch):
        """回归：仪表盘低库存计数与列表页同一条件，否则两处数字对不上"""
        db = FakeDB()
        db.one_side_effect = lambda sql, params=None: {'cnt': 0}
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.StatsModel.get_dashboard()

        hits = db.find_queried('COUNT(*) AS cnt FROM inventory')
        assert len(hits) == 1
        sql, params = hits[0]
        assert 'IF(min_stock > 0, min_stock' in sql
        assert params == (models_mod.DEFAULT_LOW_STOCK_THRESHOLD,)


# ==========================================
#  DECIMAL 精度：DB 层不再转 float
# ==========================================
class TestDecimalPrecision:
    def test_database_config_has_no_float_conversion(self):
        """回归：DECIMAL 列保持 Decimal 类型，连接配置里不再有全局 float 转换"""
        d = models_mod.Database()
        assert 'conv' not in d.config


# ==========================================
#  ProductModel.update：价格缺省不清零（导入路径回归）
# ==========================================
class TestProductUpdatePrices:
    def test_update_without_prices_omits_price_columns(self, monkeypatch):
        """回归：不传价格时 SQL 不得包含 unit_price/sale_price，避免重新导入把价格清零"""
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.ProductModel.update(9, '螺丝 M6', 'S1')

        sql, params = db.executed[0]
        assert 'UPDATE products SET' in sql
        assert 'unit_price' not in sql and 'sale_price' not in sql
        # 参数顺序：name, sku, category_id, supplier_id, unit, specification, description, prod_id
        assert params == ('螺丝 M6', 'S1', None, None, '个', '', '', 9)

    def test_update_with_explicit_prices_includes_them(self, monkeypatch):
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.ProductModel.update(9, '螺丝 M6', 'S1', unit_price=5.5, sale_price=9.9)

        sql, params = db.executed[0]
        assert 'unit_price=%s' in sql and 'sale_price=%s' in sql
        assert 5.5 in params and 9.9 in params
        assert params[-1] == 9


# ==========================================
#  ProductModel.delete：连带清理库存行（孤儿库存回归）
# ==========================================
class TestProductDeleteCleansInventory:
    def test_delete_removes_product_and_inventory_row(self, monkeypatch):
        """回归：删商品必须同删 inventory 行，否则仪表盘总数量永久虚增"""
        db = FakeDB()
        monkeypatch.setattr(models_mod, 'db', db)

        models_mod.ProductModel.delete(7)

        assert len(db.executed) == 2
        prod_sql, prod_params = db.executed[0]
        inv_sql, inv_params = db.executed[1]
        assert 'DELETE FROM products' in prod_sql and prod_params == (7,)
        assert 'DELETE FROM inventory WHERE product_id=%s' in inv_sql and inv_params == (7,)


# ==========================================
#  SupplierModel / CustomerModel.get_by_name（导入去重）
# ==========================================
class TestGetByName:
    def test_supplier_get_by_name(self, monkeypatch):
        db = FakeDB()
        db.one_side_effect = lambda sql, params=None: {'id': 3, 'name': '华为'}
        monkeypatch.setattr(models_mod, 'db', db)

        row = models_mod.SupplierModel.get_by_name('华为')

        assert row == {'id': 3, 'name': '华为'}
        sql, params = db.queries[0]
        assert 'FROM suppliers WHERE name=%s' in sql and params == ('华为',)

    def test_customer_get_by_name(self, monkeypatch):
        db = FakeDB()
        db.one_side_effect = lambda sql, params=None: None
        monkeypatch.setattr(models_mod, 'db', db)

        row = models_mod.CustomerModel.get_by_name('中建')

        assert row is None
        sql, params = db.queries[0]
        assert 'FROM customers WHERE name=%s' in sql and params == ('中建',)
