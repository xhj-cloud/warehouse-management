"""
仓库管理系统 - 数据库操作模型
"""

import pymysql
import decimal
import json
import threading
from pymysql.constants import FIELD_TYPE
from contextlib import contextmanager
from config import MYSQL_CONFIG
from datetime import datetime, date


def _clean_value(obj):
    """递归清洗查询结果中的 Decimal/bytes/date，确保 JSON 可序列化"""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    if isinstance(obj, dict):
        return {k: _clean_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_value(v) for v in obj]
    return obj


# 让 pymysql 把 DECIMAL 直接转成 float
_conversions = pymysql.converters.conversions.copy()
_conversions[FIELD_TYPE.DECIMAL] = float
_conversions[FIELD_TYPE.NEWDECIMAL] = float


class Database:
    """MySQL 数据库连接管理

    - 单条 SQL：每次独立连接、独立提交（保持向后兼容）。
    - 多语句事务：用 ``with db.transaction():`` 包住业务逻辑，同一线程内
      所有 query/execute 会复用同一个连接，中途异常整体回滚。
    """

    def __init__(self):
        self.config = {**MYSQL_CONFIG, 'conv': _conversions}
        self._local = threading.local()

    @contextmanager
    def transaction(self):
        """在单个事务里执行多步写操作；异常自动回滚。"""
        conn = pymysql.connect(**self.config, cursorclass=pymysql.cursors.DictCursor)
        conn.begin()
        self._local.conn = conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._local.conn = None
            conn.close()

    @contextmanager
    def get_connection(self):
        shared = getattr(self._local, 'conn', None)
        if shared is not None:
            # 已在事务中：复用连接，事务由 transaction() 统一提交/回滚
            yield shared
            return
        conn = pymysql.connect(**self.config, cursorclass=pymysql.cursors.DictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql, params=None):
        """执行查询并返回所有结果"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return _clean_value(cursor.fetchall())

    def query_one(self, sql, params=None):
        """执行查询并返回单条结果"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return _clean_value(cursor.fetchone())

    def execute(self, sql, params=None):
        """执行增删改操作，返回影响行数"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                rows = cursor.execute(sql, params)
                return rows, cursor.lastrowid

    def execute_many(self, sql, params_list):
        """批量执行"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                rows = cursor.executemany(sql, params_list)
                return rows


db = Database()


# ==========================================
#  商品分类
# ==========================================
class CategoryModel:
    @staticmethod
    def get_all():
        return db.query("SELECT * FROM categories ORDER BY name")

    @staticmethod
    def get_by_id(cat_id):
        return db.query_one("SELECT * FROM categories WHERE id = %s", (cat_id,))

    @staticmethod
    def create(name, description=''):
        _, lid = db.execute(
            "INSERT INTO categories (name, description) VALUES (%s, %s)",
            (name, description)
        )
        return lid

    @staticmethod
    def update(cat_id, name, description=''):
        db.execute(
            "UPDATE categories SET name=%s, description=%s WHERE id=%s",
            (name, description, cat_id)
        )

    @staticmethod
    def delete(cat_id):
        db.execute("DELETE FROM categories WHERE id=%s", (cat_id,))


# ==========================================
#  商品
# ==========================================
class ProductModel:
    @staticmethod
    def get_all():
        sql = """
            SELECT p.*, c.name AS category_name, s.name AS supplier_name,
                   i.quantity, i.location, i.min_stock, i.max_stock
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN suppliers s ON p.supplier_id = s.id
            LEFT JOIN inventory i ON p.id = i.product_id
            ORDER BY p.updated_at DESC
        """
        return db.query(sql)

    @staticmethod
    def get_by_id(prod_id):
        sql = """
            SELECT p.*, c.name AS category_name, s.name AS supplier_name,
                   i.quantity, i.location, i.min_stock, i.max_stock
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN suppliers s ON p.supplier_id = s.id
            LEFT JOIN inventory i ON p.id = i.product_id
            WHERE p.id = %s
        """
        return db.query_one(sql, (prod_id,))

    @staticmethod
    def get_by_sku(sku):
        return db.query_one("SELECT * FROM products WHERE sku = %s", (sku,))

    @staticmethod
    def create(name, sku, category_id=None, supplier_id=None, unit='个', specification='',
               description='', unit_price=0, sale_price=0):
        _, lid = db.execute(
            """INSERT INTO products (name, sku, category_id, supplier_id, unit, specification,
               unit_price, sale_price, description)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (name, sku, category_id, supplier_id, unit, specification, unit_price, sale_price, description)
        )
        # 同时初始化库存记录
        db.execute(
            "INSERT IGNORE INTO inventory (product_id, quantity, location) VALUES (%s, 0, '')",
            (lid,)
        )
        return lid

    @staticmethod
    def update(prod_id, name, sku, category_id=None, supplier_id=None, unit='个', specification='',
               description='', unit_price=0, sale_price=0):
        db.execute(
            """UPDATE products SET name=%s, sku=%s, category_id=%s, supplier_id=%s, unit=%s,
               specification=%s, unit_price=%s, sale_price=%s, description=%s WHERE id=%s""",
            (name, sku, category_id, supplier_id, unit, specification, unit_price, sale_price, description, prod_id)
        )

    @staticmethod
    def delete(prod_id):
        db.execute("DELETE FROM products WHERE id=%s", (prod_id,))

    @staticmethod
    def search(keyword):
        sql = """
            SELECT p.*, c.name AS category_name,
                   i.quantity, i.location, i.min_stock, i.max_stock
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN inventory i ON p.id = i.product_id
            WHERE p.name LIKE %s OR p.sku LIKE %s
            ORDER BY p.name
        """
        kw = f'%{keyword}%'
        return db.query(sql, (kw, kw))


# ==========================================
#  库存
# ==========================================
class InventoryModel:
    @staticmethod
    def get_all():
        sql = """
            SELECT i.*, p.name AS product_name, p.sku, p.unit,
                   p.sale_price,
                   (SELECT t.unit_price FROM transactions t
                    WHERE t.product_id = i.product_id AND t.type='in' AND t.unit_price > 0
                    ORDER BY t.created_at DESC LIMIT 1) AS latest_price,
                   (SELECT AVG(t.unit_price) FROM transactions t
                    WHERE t.product_id = i.product_id AND t.type='in' AND t.unit_price > 0) AS avg_price,
                   c.name AS category_name, s.name AS supplier_name
            FROM inventory i
            JOIN products p ON i.product_id = p.id
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN suppliers s ON p.supplier_id = s.id
            ORDER BY p.name
        """
        return db.query(sql)

    @staticmethod
    def get_low_stock(threshold=10):
        sql = """
            SELECT i.*, p.name AS product_name, p.sku, p.unit,
                   c.name AS category_name
            FROM inventory i
            JOIN products p ON i.product_id = p.id
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE i.quantity <= i.min_stock OR i.quantity <= %s
            ORDER BY i.quantity ASC
        """
        return db.query(sql, (threshold,))

    @staticmethod
    def update(product_id, quantity, location='', min_stock=0, max_stock=9999):
        db.execute(
            """UPDATE inventory SET quantity=%s, location=%s, min_stock=%s, max_stock=%s
               WHERE product_id=%s""",
            (quantity, location, min_stock, max_stock, product_id)
        )

    @staticmethod
    def stock_in(product_id, quantity, batch_no='', operator='', notes='', unit_price=0, supplier_id=None):
        """入库操作（原子自增，避免并发读-改-写丢更新）"""
        qty = int(quantity)
        if qty <= 0:
            raise ValueError("入库数量必须大于 0")

        # 原子自增；库存行不存在时（rowcount==0）补建后再算
        affected, _ = db.execute(
            "UPDATE inventory SET quantity = quantity + %s WHERE product_id=%s", (qty, product_id))
        if affected == 0:
            try:
                db.execute("INSERT INTO inventory (product_id, quantity) VALUES (%s, %s)", (product_id, qty))
            except pymysql.err.IntegrityError:
                # 并发补建冲突（product_id 唯一）→ 对已有行再次自增
                db.execute(
                    "UPDATE inventory SET quantity = quantity + %s WHERE product_id=%s", (qty, product_id))

        row = db.query_one("SELECT quantity FROM inventory WHERE product_id=%s", (product_id,))
        after_qty = row['quantity'] if row else qty
        before_qty = after_qty - qty
        db.execute(
            """INSERT INTO transactions (product_id, type, quantity, unit_price, supplier_id,
               before_qty, after_qty, batch_no, operator, notes)
               VALUES (%s, 'in', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (product_id, qty, unit_price, supplier_id, before_qty, after_qty, batch_no, operator, notes)
        )

    @staticmethod
    def stock_out(product_id, quantity, batch_no='', operator='', notes='', customer_id=None, unit_price=0):
        """出库操作（条件原子扣减，杜绝超卖与并发负库存）"""
        qty = int(quantity)
        if qty <= 0:
            raise ValueError("出库数量必须大于 0")

        # 仅在库存足够时原子扣减；rowcount==0 说明库存行不存在或数量不足
        affected, _ = db.execute(
            "UPDATE inventory SET quantity = quantity - %s WHERE product_id=%s AND quantity >= %s",
            (qty, product_id, qty))
        if affected == 0:
            row = db.query_one("SELECT quantity FROM inventory WHERE product_id=%s", (product_id,))
            if not row:
                raise ValueError("商品库存记录不存在")
            raise ValueError(f"库存不足！当前库存: {row['quantity']}, 需要出库: {qty}")

        row = db.query_one("SELECT quantity FROM inventory WHERE product_id=%s", (product_id,))
        after_qty = row['quantity'] if row else 0
        before_qty = after_qty + qty
        db.execute(
            """INSERT INTO transactions (product_id, type, quantity, unit_price, before_qty, after_qty,
               batch_no, operator, notes, customer_id) VALUES (%s, 'out', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (product_id, qty, unit_price, before_qty, after_qty, batch_no, operator, notes, customer_id)
        )


# ==========================================
#  交易记录
# ==========================================
class TransactionModel:
    @staticmethod
    def get_all(limit=100):
        sql = """
            SELECT t.*, p.name AS product_name, p.sku, p.unit,
                   cu.name AS customer_name, s.name AS supplier_name
            FROM transactions t
            JOIN products p ON t.product_id = p.id
            LEFT JOIN customers cu ON t.customer_id = cu.id
            LEFT JOIN suppliers s ON t.supplier_id = s.id
            ORDER BY t.created_at DESC
            LIMIT %s
        """
        return db.query(sql, (limit,))

    @staticmethod
    def get_by_product(product_id, limit=50):
        sql = """
            SELECT t.*, p.name AS product_name
            FROM transactions t
            JOIN products p ON t.product_id = p.id
            WHERE t.product_id = %s
            ORDER BY t.created_at DESC
            LIMIT %s
        """
        return db.query(sql, (product_id, limit))

    @staticmethod
    def get_stats(days=30):
        """统计最近N天的出入库数据"""
        sql = """
            SELECT
                DATE(created_at) AS date,
                SUM(CASE WHEN type='in' THEN quantity ELSE 0 END) AS total_in,
                SUM(CASE WHEN type='out' THEN quantity ELSE 0 END) AS total_out,
                COUNT(*) AS transaction_count
            FROM transactions
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """
        return db.query(sql, (days,))


# ==========================================
#  Excel上传记录
# ==========================================
class ExcelUploadModel:
    @staticmethod
    def create(filename, file_size=0):
        _, lid = db.execute(
            "INSERT INTO excel_uploads (filename, file_size, status) VALUES (%s, %s, 'pending')",
            (filename, file_size)
        )
        return lid

    @staticmethod
    def update_status(upload_id, status, rows=0, error=''):
        db.execute(
            "UPDATE excel_uploads SET status=%s, rows_processed=%s, error_message=%s WHERE id=%s",
            (status, rows, error, upload_id)
        )

    @staticmethod
    def get_recent(limit=20):
        return db.query("SELECT * FROM excel_uploads ORDER BY uploaded_at DESC LIMIT %s", (limit,))


# ==========================================
#  统计数据
# ==========================================
class StatsModel:
    @staticmethod
    def get_dashboard():
        """获取仪表盘概览数据"""
        total_products = db.query_one("SELECT COUNT(*) AS cnt FROM products")['cnt']
        total_categories = db.query_one("SELECT COUNT(*) AS cnt FROM categories")['cnt']
        total_quantity = db.query_one("SELECT COALESCE(SUM(quantity), 0) AS cnt FROM inventory")['cnt']
        low_stock = db.query_one(
            "SELECT COUNT(*) AS cnt FROM inventory WHERE quantity <= min_stock"
        )['cnt']
        # 库存价值估算：数量 × 进货单价（unit_price），而非单纯的数量合计
        total_value_estimate = db.query_one(
            "SELECT COALESCE(SUM(i.quantity * p.unit_price), 0) AS cnt "
            "FROM inventory i JOIN products p ON i.product_id = p.id"
        )['cnt']

        # 分类库存统计
        category_stats = db.query("""
            SELECT c.name, COALESCE(SUM(i.quantity), 0) AS total_qty,
                   COUNT(DISTINCT p.id) AS product_count
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id
            LEFT JOIN inventory i ON p.id = i.product_id
            GROUP BY c.id, c.name
            ORDER BY total_qty DESC
        """)

        return {
            'total_products': total_products,
            'total_categories': total_categories,
            'total_quantity': total_quantity,
            'low_stock_count': low_stock,
            'total_value_estimate': total_value_estimate,
            'category_stats': category_stats,
            'total_suppliers': db.query_one("SELECT COUNT(*) AS cnt FROM suppliers")['cnt'],
            'total_customers': db.query_one("SELECT COUNT(*) AS cnt FROM customers")['cnt'],
        }


# ==========================================
#  供应商
# ==========================================
class SupplierModel:
    @staticmethod
    def get_all():
        return db.query("SELECT * FROM suppliers ORDER BY name")

    @staticmethod
    def get_by_id(sid):
        return db.query_one("SELECT * FROM suppliers WHERE id=%s", (sid,))

    @staticmethod
    def create(name, contact='', phone='', email='', address='', notes=''):
        _, lid = db.execute(
            "INSERT INTO suppliers (name, contact_person, phone, email, address, notes) VALUES (%s,%s,%s,%s,%s,%s)",
            (name, contact, phone, email, address, notes)
        )
        return lid

    @staticmethod
    def update(sid, name, contact='', phone='', email='', address='', notes=''):
        db.execute(
            "UPDATE suppliers SET name=%s, contact_person=%s, phone=%s, email=%s, address=%s, notes=%s WHERE id=%s",
            (name, contact, phone, email, address, notes, sid)
        )

    @staticmethod
    def delete(sid):
        db.execute("DELETE FROM suppliers WHERE id=%s", (sid,))


# ==========================================
#  客户
# ==========================================
class CustomerModel:
    @staticmethod
    def get_all():
        return db.query("SELECT * FROM customers ORDER BY name")

    @staticmethod
    def get_by_id(cid):
        return db.query_one("SELECT * FROM customers WHERE id=%s", (cid,))

    @staticmethod
    def create(name, contact='', phone='', email='', address='', notes=''):
        _, lid = db.execute(
            "INSERT INTO customers (name, contact_person, phone, email, address, notes) VALUES (%s,%s,%s,%s,%s,%s)",
            (name, contact, phone, email, address, notes)
        )
        return lid

    @staticmethod
    def update(cid, name, contact='', phone='', email='', address='', notes=''):
        db.execute(
            "UPDATE customers SET name=%s, contact_person=%s, phone=%s, email=%s, address=%s, notes=%s WHERE id=%s",
            (name, contact, phone, email, address, notes, cid)
        )

    @staticmethod
    def delete(cid):
        db.execute("DELETE FROM customers WHERE id=%s", (cid,))


# ==========================================
#  审计日志
# ==========================================
class AuditLog:
    @staticmethod
    def log(action, table_name, record_id, old_data=None, new_data=None, operator='system'):
        """记录操作日志"""
        db.execute(
            """INSERT INTO audit_log (action, table_name, record_id, old_data, new_data, operator)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (action, table_name, record_id,
             json.dumps(old_data, ensure_ascii=False, default=str) if old_data else None,
             json.dumps(new_data, ensure_ascii=False, default=str) if new_data else None,
             operator)
        )

    @staticmethod
    def get_recent(limit=100):
        return db.query("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT %s", (limit,))

    @staticmethod
    def get_by_table(table_name, limit=50):
        return db.query(
            "SELECT * FROM audit_log WHERE table_name=%s ORDER BY created_at DESC LIMIT %s",
            (table_name, limit)
        )
