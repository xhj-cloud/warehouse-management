"""
真实 MySQL 集成测试。

- 自动创建独立测试库 warehouse_test_<pid>，执行 init_db.sql（含迁移语句），
  结束后 DROP，**不触碰任何业务数据**。
- MySQL 不可达时整个模块自动跳过（本地无数据库也能跑其余单测）。
- 可通过环境变量指向服务器数据库，例如：
    DB_HOST=100.101.108.100 pytest tests/test_integration.py -v
  （需要该账号具备 CREATE/DROP DATABASE 权限）
"""

import os
import time
from io import BytesIO

import openpyxl
import pymysql
import pytest

from config import MYSQL_CONFIG


def _probe_db():
    try:
        conn = pymysql.connect(
            host=MYSQL_CONFIG['host'], port=MYSQL_CONFIG['port'],
            user=MYSQL_CONFIG['user'], password=MYSQL_CONFIG['password'],
            connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


DB_AVAILABLE = _probe_db()

pytestmark = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason='MySQL 不可达（可用 DB_HOST 等环境变量指向服务器），跳过集成测试',
)


@pytest.fixture(scope='session')
def test_db():
    """创建独立测试库并执行 init_db.sql；会话结束后删除。"""
    import models as models_mod

    base = {k: MYSQL_CONFIG[k] for k in ('host', 'port', 'user', 'password')}
    name = f"warehouse_test_{os.getpid()}"
    try:
        conn = pymysql.connect(**base, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` DEFAULT CHARACTER SET utf8mb4")
            sql_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'init_db.sql')
            with open(sql_path, encoding='utf-8') as f:
                script = f.read().replace('warehouse_db', name)
            for chunk in script.split(';'):
                stmt = '\n'.join(
                    line for line in chunk.splitlines() if not line.strip().startswith('--')
                ).strip()
                if stmt:
                    cur.execute(stmt)
        conn.commit()
        conn.close()
    except Exception as e:
        pytest.skip(f'无法创建测试库（权限不足或连接失败）: {e}')

    # 让 models.db / app 内的 db 指向测试库（同一 Database 实例，改 config 即可）
    old_db = models_mod.db.config.get('database')
    models_mod.db.config['database'] = name
    yield name
    try:
        models_mod.db.config['database'] = old_db
        conn = pymysql.connect(**base, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        conn.commit()
        conn.close()
    except Exception:
        pass


def make_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ==========================================
#  建库脚本本身
# ==========================================
class TestSchema:
    def test_all_tables_exist(self, test_db):
        import models as m
        rows = m.db.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
            (test_db,),
        )
        tables = {r['table_name'] for r in rows}
        expected = {'categories', 'products', 'inventory', 'transactions',
                    'excel_uploads', 'suppliers', 'customers', 'audit_log'}
        missing = expected - tables
        assert not missing, f'缺少表: {missing}'

    def test_sample_data_loaded(self, test_db):
        import models as m
        assert m.db.query_one("SELECT COUNT(*) AS c FROM products")['c'] >= 5
        assert m.db.query_one("SELECT COUNT(*) AS c FROM categories")['c'] >= 5


# ==========================================
#  分类：CRUD + 唯一约束
# ==========================================
class TestCategoriesIntegration:
    def test_crud_and_unique_constraint(self, test_db):
        import models as m
        cid = m.CategoryModel.create('集成测试分类')
        assert cid > 0

        with pytest.raises(pymysql.err.IntegrityError):
            m.CategoryModel.create('集成测试分类')  # name UNIQUE

        m.CategoryModel.update(cid, '集成测试分类-改')
        assert m.CategoryModel.get_by_id(cid)['name'] == '集成测试分类-改'

        m.CategoryModel.delete(cid)
        assert m.CategoryModel.get_by_id(cid) is None


# ==========================================
#  库存核心流程：入库/出库/批次价格
# ==========================================
class TestInventoryFlow:
    def test_product_create_initializes_inventory(self, test_db):
        import models as m
        pid = m.ProductModel.create('集成测试商品P', 'IT-P-001')
        inv = m.db.query_one("SELECT * FROM inventory WHERE product_id=%s", (pid,))
        assert inv is not None and inv['quantity'] == 0

    def test_stock_in_out_flow_and_batch_prices(self, test_db):
        import models as m
        pid = m.ProductModel.create('集成测试商品Q', 'IT-Q-001', unit_price=10)

        m.InventoryModel.stock_in(pid, 5, batch_no='B1', operator='t', unit_price=10)
        time.sleep(1.1)  # TIMESTAMP 秒级精度，保证 created_at 排序确定
        m.InventoryModel.stock_in(pid, 3, batch_no='B2', operator='t', unit_price=12)

        inv = m.db.query_one("SELECT quantity FROM inventory WHERE product_id=%s", (pid,))
        assert inv['quantity'] == 8

        # 流水 before/after 链完整（get_by_product 按时间倒序）
        txns = m.TransactionModel.get_by_product(pid)
        assert [t['batch_no'] for t in txns] == ['B2', 'B1']
        b1, b2 = txns[1], txns[0]
        assert (b1['before_qty'], b1['after_qty']) == (0, 5)
        assert (b2['before_qty'], b2['after_qty']) == (5, 8)

        # 最近进价 / 平均进价（README：所有入库批次单价的算术平均）
        row = next(r for r in m.InventoryModel.get_all() if r['sku'] == 'IT-Q-001')
        assert float(row['latest_price']) == pytest.approx(12.0)
        assert float(row['avg_price']) == pytest.approx(11.0)

        # 出库扣减 + 超卖拦截
        m.InventoryModel.stock_out(pid, 3, operator='t')
        inv = m.db.query_one("SELECT quantity FROM inventory WHERE product_id=%s", (pid,))
        assert inv['quantity'] == 5
        with pytest.raises(ValueError, match='库存不足'):
            m.InventoryModel.stock_out(pid, 999)


# ==========================================
#  搜索与低库存预警
# ==========================================
class TestSearchAndLowStock:
    def test_search_and_low_stock(self, test_db):
        import models as m
        pid = m.ProductModel.create('集成测试低库存品', 'IT-LOW-001')
        m.InventoryModel.stock_in(pid, 10)
        m.db.execute("UPDATE inventory SET min_stock=100 WHERE product_id=%s", (pid,))

        found = m.ProductModel.search('集成测试低库存品')
        assert len(found) == 1 and found[0]['sku'] == 'IT-LOW-001'
        assert m.ProductModel.search('不存在的商品XYZ') == []

        low = m.InventoryModel.get_low_stock(threshold=50)
        assert 'IT-LOW-001' in [r['sku'] for r in low]  # quantity(10) <= min_stock(100)


# ==========================================
#  审计日志落库
# ==========================================
class TestAuditIntegration:
    def test_log_roundtrip(self, test_db):
        import models as m
        m.AuditLog.log('create', 'suppliers', 999,
                       new_data={'名称': '集成测试供应商'}, operator='tester')
        rows = m.AuditLog.get_by_table('suppliers', limit=10)
        hit = next(r for r in rows if r['record_id'] == 999 and r['operator'] == 'tester')
        assert '集成测试供应商' in hit['new_data']


# ==========================================
#  仪表盘统计
# ==========================================
class TestDashboard:
    def test_counts_sane(self, test_db):
        import models as m
        stats = m.StatsModel.get_dashboard()
        assert stats['total_products'] >= 5       # 含示例数据
        assert stats['total_categories'] >= 5
        assert isinstance(stats['low_stock_count'], int)
        assert isinstance(stats['category_stats'], list)

    def test_total_value_estimate_is_monetary(self, test_db):
        """回归测试：total_value_estimate 必须是金额（quantity*unit_price），而非数量合计"""
        import models as m
        pid = m.ProductModel.create('集成测试价值品', 'IT-VAL-001', unit_price=25)
        m.InventoryModel.stock_in(pid, 4, unit_price=25)

        expected = m.db.query_one(
            "SELECT COALESCE(SUM(i.quantity * p.unit_price), 0) AS v "
            "FROM inventory i JOIN products p ON i.product_id=p.id"
        )['v']
        stats = m.StatsModel.get_dashboard()
        assert float(stats['total_value_estimate']) == pytest.approx(float(expected))


# ==========================================
#  Excel 导入端到端（Flask API + 真实数据库）
# ==========================================
class TestExcelUploadE2E:
    def test_upload_creates_product_and_transaction(self, test_db):
        import models as m
        from app import app as flask_app

        client = flask_app.test_client()
        buf = make_xlsx([['商品名称', '数量'], ['集成测试导入品A', 7], ['', 3]])
        resp = client.post('/api/upload', data={
            'file': (buf, 'e2e.xlsx'), 'mode': 'replace'},
            content_type='multipart/form-data')

        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['rows_imported'] == 1  # 无名称行被跳过

        rows = m.ProductModel.search('集成测试导入品A')
        assert len(rows) == 1 and rows[0]['quantity'] == 7

        txns = m.db.query(
            "SELECT * FROM transactions WHERE notes LIKE %s", ('%e2e.xlsx%',))
        assert len(txns) == 1
        assert txns[0]['batch_no'].startswith('Excel-')
        assert (txns[0]['before_qty'], txns[0]['after_qty']) == (0, 7)
