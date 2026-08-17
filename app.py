"""
仓库管理系统 - Flask 主应用
"""

import os
import sys
import json
import decimal
import traceback
import logging
import pandas as pd
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from flask.json.provider import DefaultJSONProvider

# 错误日志文件
ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'error.log')
logging.basicConfig(
    filename=ERROR_LOG,
    level=logging.ERROR,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
_logger = logging.getLogger(__name__)
from werkzeug.utils import secure_filename

from config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS, SECRET_KEY, MAX_CONTENT_LENGTH
from models import (
    db, CategoryModel, ProductModel, InventoryModel,
    TransactionModel, ExcelUploadModel, StatsModel,
    SupplierModel, CustomerModel, AuditLog,
)
from ai_service import ai_service


class CustomJSONProvider(DefaultJSONProvider):
    """自定义 JSON 提供器，处理 Decimal / date 等类型"""
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        return super().default(obj)


app = Flask(__name__)
app.json = CustomJSONProvider(app)
app.secret_key = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ==========================================
#  审计日志 API
# ==========================================
@app.route('/api/audit-log')
def api_audit_log():
    try:
        limit = int(request.args.get('limit', 200))
        table = request.args.get('table', '')
        if table:
            logs = AuditLog.get_by_table(table, limit)
        else:
            logs = AuditLog.get_recent(limit)
        return jsonify({'success': True, 'data': logs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  库存批次 API
# ==========================================
@app.route('/api/inventory/batches')
def api_inventory_batches():
    """获取每个商品按供应商/进价的进货批次汇总"""
    try:
        batches = db.query("""
            SELECT p.id AS product_id, p.name AS product_name, p.sku,
                   p.unit, p.unit_price AS avg_price, i.quantity AS total_qty,
                   t.batch_no, t.unit_price AS batch_price,
                   s.name AS supplier_name,
                   SUM(CASE WHEN t.type='in' THEN t.quantity ELSE 0 END) -
                   SUM(CASE WHEN t.type='out' THEN t.quantity ELSE 0 END) AS net_qty
            FROM products p
            JOIN inventory i ON p.id = i.product_id
            LEFT JOIN transactions t ON t.product_id = p.id
            LEFT JOIN suppliers s ON p.supplier_id = s.id
            GROUP BY p.id, p.name, p.sku, p.unit, p.unit_price, i.quantity,
                     s.name, t.batch_no, t.unit_price
            HAVING net_qty > 0
            ORDER BY p.name, t.id DESC
        """)
        return jsonify({'success': True, 'data': batches})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  出库单 Excel 导出
# ==========================================
@app.route('/api/order/export-inventory', methods=['POST'])
def api_export_inventory():
    """导出库存清单 Excel"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from io import BytesIO
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '库存清单'
    hfill = PatternFill(start_color='2563EB', end_color='2563EB', fill_type='solid')
    hfont = Font(color='FFFFFF', bold=True, size=11)
    bd = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    ca = Alignment(horizontal='center', vertical='center')
    ws['A1'] = '库存清单'; ws['A1'].font = Font(size=16, bold=True); ws['A1'].alignment = ca
    ws.merge_cells('A1:K1')
    ws['A2'] = '导出日期: ' + datetime.now().strftime('%Y-%m-%d')
    headers = ['商品名称','SKU','分类','供应商','单位','规格','库存','最低库存','最高库存','库位','最近进价']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=ci, value=h); c.fill = hfill; c.font = hfont; c.alignment = ca; c.border = bd
    items = request.json.get('items', [])
    for idx, item in enumerate(items, 1):
        r = 4 + idx
        for ci, k in enumerate(['product_name','sku','category_name','supplier_name','unit','','quantity','min_stock','max_stock','location','latest_price'], 1):
            v = item.get(k, '') if k else (item.get('specification', '') or '')
            if k == 'latest_price' and v and float(v) > 0: v = '¥' + str(v)
            c = ws.cell(row=r, column=ci, value=v if v is not None else '')
            c.border = bd; c.alignment = ca
    for i, w in enumerate([18,15,12,12,8,15,8,10,10,10,12], 1):
        ws.column_dimensions[chr(64+i)].width = w
    out = BytesIO(); wb.save(out); out.seek(0)
    return send_file(out, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='库存清单_' + datetime.now().strftime('%Y%m%d') + '.xlsx')


@app.route('/api/order/export', methods=['POST'])
def api_order_export():
    """生成出库单 Excel 文件"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from io import BytesIO

    data = request.json
    items = data.get('items', [])
    customer = data.get('customer', '')
    operator = data.get('operator', '')
    keeper = data.get('keeper', '')
    warehouse_no = data.get('warehouse', '')
    now_str = datetime.now().strftime('%Y-%m-%d')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '出库单'

    # 样式
    title_font = Font(size=16, bold=True)
    header_fill = PatternFill(start_color='2563EB', end_color='2563EB', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    label_font = Font(size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))
    center = Alignment(horizontal='center', vertical='center')
    right_align = Alignment(horizontal='right', vertical='center')
    left_align = Alignment(horizontal='left', vertical='center')

    # 标题
    ws.merge_cells('A1:G1')
    ws['A1'] = '出 库 单'
    ws['A1'].font = title_font
    ws['A1'].alignment = center

    # 日期和客户（标题下方一行，无空行）
    ws['A2'] = '日期:'; ws['A2'].font = label_font; ws['A2'].alignment = right_align
    ws['B2'] = now_str; ws['B2'].font = label_font; ws['B2'].alignment = left_align
    ws['D2'] = '客户:'; ws['D2'].font = label_font; ws['D2'].alignment = right_align
    ws['E2'] = customer; ws['E2'].font = label_font; ws['E2'].alignment = left_align

    # 表头（第3行开始，无空行）
    headers = ['序号', '商品名称', '规格', 'SKU', '数量', '单价(元)', '金额(元)']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.fill = header_fill; cell.font = header_font
        cell.alignment = center; cell.border = thin_border

    # 数据行
    total = 0
    for idx, item in enumerate(items, 1):
        row = 3 + idx
        price = float(item.get('sale_price', 0))
        qty = int(item.get('quantity', 0))
        sub = round(price * qty, 2)
        total += sub
        values = [idx, item.get('name', ''), item.get('specification', ''),
                  item.get('sku', ''), qty, price, sub]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.border = thin_border
            cell.alignment = center if col != 6 and col != 7 else right_align

    # 合计行（紧接数据）
    sum_row = 3 + len(items) + 1
    ws.merge_cells(f'A{sum_row}:E{sum_row}')
    ws.cell(row=sum_row, column=1, value='合计').font = Font(bold=True, size=12)
    ws.cell(row=sum_row, column=1).alignment = Alignment(horizontal='right')
    ws.cell(row=sum_row, column=6, value='').border = thin_border
    total_cell = ws.cell(row=sum_row, column=7, value=total)
    total_cell.font = Font(bold=True, size=12, color='FF0000')
    total_cell.border = thin_border; total_cell.alignment = right_align

    # 经办人、库管、仓库编号（合计下方，无空行）
    bot = sum_row + 1
    ws.cell(row=bot, column=1, value='经办人:').font = label_font
    ws.cell(row=bot, column=1).alignment = right_align
    ws.cell(row=bot, column=2, value=operator).font = label_font
    ws.cell(row=bot, column=2).alignment = left_align
    ws.cell(row=bot, column=4, value='库管:').font = label_font
    ws.cell(row=bot, column=4).alignment = right_align
    ws.cell(row=bot, column=5, value=keeper).font = label_font
    ws.cell(row=bot, column=5).alignment = left_align
    ws.cell(row=bot+1, column=1, value='仓库编号:').font = label_font
    ws.cell(row=bot+1, column=1).alignment = right_align
    ws.cell(row=bot+1, column=2, value=warehouse_no).font = label_font
    ws.cell(row=bot+1, column=2).alignment = left_align

    # 列宽
    widths = [6, 22, 20, 15, 8, 12, 12]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    # 输出
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"出库单_{customer}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=filename)


# ==========================================
#  入库单视觉识别
# ==========================================
@app.route('/api/ai/inbound-recognize', methods=['POST'])
def api_inbound_recognize():
    """AI 识别入库单图片"""
    import base64
    try:
        data = request.json
        image_b64 = data.get('image', '')
        if not image_b64:
            return jsonify({'success': False, 'error': '请上传图片'}), 400

        items = ai_service.recognize_inbound_image(image_b64)
        imported = []
        for item in items:
            name = item.get('name', '').strip()
            qty = int(item.get('quantity', 0))
            if not name or qty <= 0: continue
            sku = item.get('sku', '').strip() or name[:6].upper()
            cat_name = item.get('supplier', '').strip()  # 可能 AI 错填
            # 处理分类
            cat_id = _auto_category(name)
            # 处理供应商
            sup_id = None
            sup_name = item.get('supplier', '').strip()
            if sup_name:
                sups = SupplierModel.get_all()
                smap = {s['name']: s['id'] for s in sups}
                if sup_name in smap: sup_id = smap[sup_name]
                else: sup_id = SupplierModel.create(sup_name)
            # 创建或查找商品
            prod = ProductModel.get_by_sku(sku) if sku else None
            if prod:
                pid = prod['id']
            else:
                pid = ProductModel.create(name, sku, cat_id, sup_id,
                    item.get('unit', '个').strip() or '个',
                    item.get('specification', '').strip() or '')
            # 入库
            up = float(item.get('unit_price', 0) or 0)
            InventoryModel.stock_in(pid, qty, 'AI视觉识别', 'AI', f"入库单识别导入", unit_price=up, supplier_id=sup_id)
            imported.append({'name': name, 'sku': sku, 'quantity': qty, 'unit_price': up})

        return jsonify({'success': True, 'data': {'items': items, 'imported': imported, 'count': len(imported)}})
    except RuntimeError as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'AI 识别结果解析失败，请重试或使用更清晰的图片'}), 400
    except Exception as e:
        _logger.error(f"入库单识别错误: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  页面路由
# ==========================================
@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


@app.route('/uploads/<path:filename>')
def download_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# ==========================================
#  仪表盘 API
# ==========================================
@app.route('/api/dashboard')
def api_dashboard():
    """获取仪表盘数据"""
    try:
        stats = StatsModel.get_dashboard()
        return jsonify({'success': True, 'data': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  分类 API
# ==========================================
@app.route('/api/categories')
def api_categories():
    """获取所有分类"""
    try:
        categories = CategoryModel.get_all()
        return jsonify({'success': True, 'data': categories})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/categories', methods=['POST'])
def api_category_create():
    """创建分类"""
    try:
        data = request.json
        cat_id = CategoryModel.create(data['name'], data.get('description', ''))
        return jsonify({'success': True, 'id': cat_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/categories/<int:cat_id>', methods=['PUT'])
def api_category_update(cat_id):
    """更新分类"""
    try:
        data = request.json
        CategoryModel.update(cat_id, data['name'], data.get('description', ''))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/categories/<int:cat_id>', methods=['DELETE'])
def api_category_delete(cat_id):
    """删除分类"""
    try:
        CategoryModel.delete(cat_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==========================================
#  供应商 API
# ==========================================
@app.route('/api/suppliers')
def api_suppliers():
    try:
        suppliers = SupplierModel.get_all()
        return jsonify({'success': True, 'data': suppliers})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/suppliers', methods=['POST'])
def api_supplier_create():
    try:
        d = request.json
        sid = SupplierModel.create(d['name'], d.get('contact_person', ''), d.get('phone', ''),
                                    d.get('email', ''), d.get('address', ''), d.get('notes', ''))
        AuditLog.log('create', 'suppliers', sid, new_data=d, operator='管理员')
        return jsonify({'success': True, 'id': sid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/suppliers/<int:sid>', methods=['PUT'])
def api_supplier_update(sid):
    try:
        d = request.json
        old = SupplierModel.get_by_id(sid)
        SupplierModel.update(sid, d['name'], d.get('contact_person', ''), d.get('phone', ''),
                             d.get('email', ''), d.get('address', ''), d.get('notes', ''))
        AuditLog.log('update', 'suppliers', sid, old_data=old, new_data=d, operator='管理员')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/suppliers/<int:sid>', methods=['DELETE'])
def api_supplier_delete(sid):
    try:
        old = SupplierModel.get_by_id(sid)
        SupplierModel.delete(sid)
        AuditLog.log('delete', 'suppliers', sid, old_data=old, operator='管理员')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==========================================
#  客户 API
# ==========================================
@app.route('/api/customers')
def api_customers():
    try:
        customers = CustomerModel.get_all()
        return jsonify({'success': True, 'data': customers})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/customers', methods=['POST'])
def api_customer_create():
    try:
        d = request.json
        cid = CustomerModel.create(d['name'], d.get('contact_person', ''), d.get('phone', ''),
                                    d.get('email', ''), d.get('address', ''), d.get('notes', ''))
        AuditLog.log('create', 'customers', cid, new_data=d, operator='管理员')
        return jsonify({'success': True, 'id': cid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/customers/<int:cid>', methods=['PUT'])
def api_customer_update(cid):
    try:
        d = request.json
        old = CustomerModel.get_by_id(cid)
        CustomerModel.update(cid, d['name'], d.get('contact_person', ''), d.get('phone', ''),
                             d.get('email', ''), d.get('address', ''), d.get('notes', ''))
        AuditLog.log('update', 'customers', cid, old_data=old, new_data=d, operator='管理员')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/customers/<int:cid>', methods=['DELETE'])
def api_customer_delete(cid):
    try:
        old = CustomerModel.get_by_id(cid)
        CustomerModel.delete(cid)
        AuditLog.log('delete', 'customers', cid, old_data=old, operator='管理员')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==========================================
#  商品 API
# ==========================================
@app.route('/api/products')
def api_products():
    """获取所有商品"""
    try:
        keyword = request.args.get('search', '')
        if keyword:
            products = ProductModel.search(keyword)
        else:
            products = ProductModel.get_all()
        return jsonify({'success': True, 'data': products})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/products/<int:prod_id>')
def api_product_detail(prod_id):
    """获取商品详情"""
    try:
        product = ProductModel.get_by_id(prod_id)
        if not product:
            return jsonify({'success': False, 'error': '商品不存在'}), 404
        return jsonify({'success': True, 'data': product})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/products', methods=['POST'])
def api_product_create():
    """创建商品"""
    try:
        data = request.json
        prod_id = ProductModel.create(
            name=data['name'],
            sku=data['sku'],
            category_id=data.get('category_id'),
            supplier_id=data.get('supplier_id'),
            unit=data.get('unit', '个'),
            specification=data.get('specification', ''),
            description=data.get('description', ''),
            unit_price=float(data.get('unit_price', 0) or 0),
            sale_price=float(data.get('sale_price', 0) or 0),
        )
        # 如果有初始库存，用 stock_in 生成入库记录
        if data.get('quantity') is not None and int(data.get('quantity', 0)) > 0:
            InventoryModel.stock_in(
                prod_id,
                quantity=int(data['quantity']),
                operator='初始创建',
                notes='新建商品初始库存',
                unit_price=float(data.get('unit_price', 0) or 0),
                supplier_id=data.get('supplier_id'),
            )
            # 额外更新库位和阈值
            if data.get('location') or data.get('min_stock') is not None:
                db.execute(
                    "UPDATE inventory SET location=%s, min_stock=%s, max_stock=%s WHERE product_id=%s",
                    (data.get('location', ''), int(data.get('min_stock', 0)),
                     int(data.get('max_stock', 9999)), prod_id)
                )
        AuditLog.log('create', 'products', prod_id, new_data=data, operator='管理员')
        return jsonify({'success': True, 'id': prod_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/products/<int:prod_id>', methods=['PUT'])
def api_product_update(prod_id):
    """更新商品（含库存信息）"""
    try:
        data = request.json
        old = ProductModel.get_by_id(prod_id)
        ProductModel.update(
            prod_id,
            name=data['name'],
            sku=data['sku'],
            category_id=data.get('category_id'),
            supplier_id=data.get('supplier_id'),
            unit=data.get('unit', '个'),
            specification=data.get('specification', ''),
            description=data.get('description', ''),
            unit_price=float(data.get('unit_price', 0) or 0),
            sale_price=float(data.get('sale_price', 0) or 0),
        )
        AuditLog.log('update', 'products', prod_id, old_data=old, new_data=data, operator='管理员')
        # 同步更新库存信息
        if data.get('quantity') is not None:
            InventoryModel.update(
                prod_id,
                quantity=int(data['quantity']),
                location=data.get('location', ''),
                min_stock=int(data.get('min_stock', 0)),
                max_stock=int(data.get('max_stock', 9999)),
            )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/products/<int:prod_id>', methods=['DELETE'])
def api_product_delete(prod_id):
    """删除商品"""
    try:
        old = ProductModel.get_by_id(prod_id)
        ProductModel.delete(prod_id)
        AuditLog.log('delete', 'products', prod_id, old_data=old, operator='管理员')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==========================================
#  库存 API
# ==========================================
@app.route('/api/inventory')
def api_inventory():
    """获取所有库存"""
    try:
        inv = InventoryModel.get_all()
        return jsonify({'success': True, 'data': inv})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/inventory/low-stock')
def api_low_stock():
    """获取低库存预警"""
    try:
        threshold = int(request.args.get('threshold', 10))
        items = InventoryModel.get_low_stock(threshold)
        return jsonify({'success': True, 'data': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/inventory/<int:product_id>', methods=['PUT'])
def api_inventory_update(product_id):
    """更新库存"""
    try:
        data = request.json
        InventoryModel.update(
            product_id,
            quantity=int(data['quantity']),
            location=data.get('location', ''),
            min_stock=int(data.get('min_stock', 0)),
            max_stock=int(data.get('max_stock', 9999)),
        )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/inventory/stock-in', methods=['POST'])
def api_stock_in():
    """入库"""
    try:
        data = request.json
        up = float(data.get('unit_price', 0) or 0)
        sid = data.get('supplier_id')
        InventoryModel.stock_in(
            product_id=int(data['product_id']),
            quantity=int(data['quantity']),
            batch_no=data.get('batch_no', ''),
            operator=data.get('operator', ''),
            notes=data.get('notes', ''),
            unit_price=up,
            supplier_id=sid,
        )
        # 更新商品最近进价和供应商
        pid = int(data['product_id'])
        if up > 0:
            db.execute("UPDATE products SET unit_price=%s WHERE id=%s", (up, pid))
        if sid:
            db.execute("UPDATE products SET supplier_id=%s WHERE id=%s", (sid, pid))
        AuditLog.log('stock_in', 'inventory', int(data['product_id']),
                     new_data={'qty': int(data['quantity']), 'unit_price': up, 'supplier_id': sid},
                     operator=data.get('operator', ''))
        return jsonify({'success': True, 'message': '入库成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/inventory/stock-out', methods=['POST'])
def api_stock_out():
    """出库"""
    try:
        data = request.json
        InventoryModel.stock_out(
            product_id=int(data['product_id']),
            quantity=int(data['quantity']),
            batch_no=data.get('batch_no', ''),
            operator=data.get('operator', ''),
            notes=data.get('notes', ''),
            customer_id=data.get('customer_id'),
            unit_price=float(data.get('unit_price', 0) or 0),
        )
        AuditLog.log('stock_out', 'inventory', int(data['product_id']),
                     new_data={'qty': int(data['quantity']), 'customer_id': data.get('customer_id')},
                     operator=data.get('operator', ''))
        return jsonify({'success': True, 'message': '出库成功'})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  交易记录 API
# ==========================================
@app.route('/api/transactions')
def api_transactions():
    """获取交易记录"""
    try:
        limit = int(request.args.get('limit', 100))
        txn = TransactionModel.get_all(limit)
        return jsonify({'success': True, 'data': txn})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/transactions/stats')
def api_transaction_stats():
    """获取交易统计"""
    try:
        days = int(request.args.get('days', 30))
        stats = TransactionModel.get_stats(days)
        return jsonify({'success': True, 'data': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  Excel 上传 API
# ==========================================
@app.route('/api/upload', methods=['POST'])
def api_upload():
    """上传 Excel 文件并导入数据"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请选择文件'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '请选择文件'}), 400

        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': '仅支持 .xlsx .xls .csv 格式'}), 400

        # 导入模式：replace=全量覆盖, increment=增量累加
        mode = request.form.get('mode', 'replace')

        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
        file.save(filepath)
        file_size = os.path.getsize(filepath)

        # 记录上传
        upload_id = ExcelUploadModel.create(filename, file_size)
        ExcelUploadModel.update_status(upload_id, 'processing')

        # 解析 Excel
        try:
            if filename.endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath, engine='openpyxl')

            # 映射列名（支持中英文列名）
            col_map = {
                '商品名称': 'name', '名称': 'name', 'name': 'name',
                'SKU': 'sku', 'sku': 'sku', '编码': 'sku', '编号': 'sku',
                '分类': 'category', 'category': 'category',
                '单位': 'unit', 'unit': 'unit',
                '规格': 'specification', 'specification': 'specification',
                '数量': 'quantity', 'quantity': 'quantity', '库存': 'quantity',
                '位置': 'location', 'location': 'location', '库位': 'location',
                '最低库存': 'min_stock', 'min_stock': 'min_stock',
                '最高库存': 'max_stock', 'max_stock': 'max_stock',
                '描述': 'description', 'description': 'description',
            }

            # 标准化列名
            df.columns = [str(c).strip() for c in df.columns]
            rename_map = {}
            for col in df.columns:
                if col in col_map:
                    rename_map[col] = col_map[col]
            df.rename(columns=rename_map, inplace=True)

            rows_imported = 0
            errors = []
            # 用于自动生成库位的计数器
            loc_counters = {}

            for idx, row in df.iterrows():
                try:
                    name = str(row.get('name', '')).strip()
                    sku = str(row.get('sku', '')).strip()

                    # 空单元格被 pandas 读成 NaN，str() 后变成字符串 "nan"，统一视为空值
                    if name.lower() == 'nan':
                        name = ''
                    if sku.lower() == 'nan':
                        sku = ''

                    if not name:
                        continue

                    # 处理分类
                    cat_name = str(row.get('category', '')).strip()
                    category_id = None
                    if cat_name:
                        existing_cat = CategoryModel.get_all()
                        cat_map = {c['name']: c['id'] for c in existing_cat}
                        if cat_name in cat_map:
                            category_id = cat_map[cat_name]
                        else:
                            category_id = CategoryModel.create(cat_name)

                    # 自动生成 SKU
                    if not sku:
                        prefix = (cat_name[:3] if cat_name else name[:3]).upper()
                        # 用分类+序号生成唯一 SKU
                        base = prefix
                        counter = 1
                        while True:
                            candidate = f'{base}-{counter:04d}'
                            if not ProductModel.get_by_sku(candidate):
                                break
                            counter += 1
                        sku = candidate

                    # 创建或更新商品
                    existing = ProductModel.get_by_sku(sku)
                    if existing:
                        ProductModel.update(
                            existing['id'],
                            name=name,
                            sku=sku,
                            category_id=category_id,
                            unit=str(row.get('unit', '个')).strip() or '个',
                            specification=str(row.get('specification', '')).strip(),
                            description=str(row.get('description', '')).strip(),
                        )
                        prod_id = existing['id']
                    else:
                        prod_id = ProductModel.create(
                            name=name,
                            sku=sku,
                            category_id=category_id,
                            unit=str(row.get('unit', '个')).strip() or '个',
                            specification=str(row.get('specification', '')).strip(),
                            description=str(row.get('description', '')).strip(),
                        )

                    # 更新库存（对比差异，自动生成出入库记录）
                    qty = row.get('quantity')
                    if qty is not None and str(qty).strip() != '' and str(qty).strip().lower() != 'nan':
                        new_qty = int(float(qty))
                        location = str(row.get('location', '')).strip()
                        # 自动生成库位
                        if not location:
                            cat_key = cat_name[:2] if cat_name else 'ZZ'
                            if cat_key not in loc_counters:
                                # 查当前分类最大编号
                                max_loc = db.query_one(
                                    """SELECT location FROM inventory i
                                       JOIN products p ON i.product_id=p.id
                                       WHERE p.category_id=%s AND i.location LIKE %s
                                       ORDER BY i.location DESC LIMIT 1""",
                                    (category_id, f'{cat_key}-%')
                                )
                                if max_loc and max_loc['location']:
                                    try:
                                        loc_counters[cat_key] = int(max_loc['location'].split('-')[1]) + 1
                                    except (ValueError, IndexError):
                                        loc_counters[cat_key] = 1
                                else:
                                    loc_counters[cat_key] = 1
                            else:
                                loc_counters[cat_key] += 1
                            location = f'{cat_key}-{loc_counters[cat_key]:03d}'
                        min_stock = int(float(row.get('min_stock', 0))) if row.get('min_stock') and str(row['min_stock']).strip() != '' else 0
                        max_stock = int(float(row.get('max_stock', 9999))) if row.get('max_stock') and str(row['max_stock']).strip() != '' else 9999

                        # 查询原库存
                        old_inv = db.query_one(
                            "SELECT quantity FROM inventory WHERE product_id = %s", (prod_id,)
                        )
                        old_qty = old_inv['quantity'] if old_inv else 0

                        # 增量模式：Excel 数量为新增量，累加到现有库存
                        if mode == 'increment':
                            diff = new_qty  # diff 为正，表示入库
                            new_qty = old_qty + new_qty
                        else:
                            diff = new_qty - old_qty

                        # 更新库存基础信息（数量、库位、阈值）
                        db.execute(
                            """UPDATE inventory SET quantity=%s, location=%s,
                               min_stock=%s, max_stock=%s WHERE product_id=%s""",
                            (new_qty, location, min_stock, max_stock, prod_id)
                        )

                        # 如果数量有变化，生成出入库记录
                        if diff != 0:
                            txn_type = 'in' if diff > 0 else 'out'
                            txn_qty = abs(diff)
                            batch_no = f'Excel-{upload_id}'
                            db.execute(
                                """INSERT INTO transactions
                                   (product_id, type, quantity, before_qty, after_qty, batch_no, operator, notes)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                (prod_id, txn_type, txn_qty, old_qty, new_qty,
                                 batch_no, 'Excel导入', f'文件: {filename}')
                            )

                    rows_imported += 1
                except Exception as e:
                    errors.append(f"第 {idx+2} 行: {str(e)}")

            ExcelUploadModel.update_status(upload_id, 'success', rows_imported)

            return jsonify({
                'success': True,
                'data': {
                    'upload_id': upload_id,
                    'rows_imported': rows_imported,
                    'total_rows': len(df),
                    'errors': errors[:20],  # 最多返回20条错误
                }
            })

        except Exception as e:
            ExcelUploadModel.update_status(upload_id, 'failed', 0, str(e))
            return jsonify({'success': False, 'error': f'文件解析失败: {str(e)}'}), 400

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/uploads')
def api_uploads():
    """获取上传记录"""
    try:
        records = ExcelUploadModel.get_recent()
        return jsonify({'success': True, 'data': records})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
#  供应商/客户 Excel 导入
# ==========================================
@app.route('/api/upload/suppliers', methods=['POST'])
def api_upload_suppliers():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请选择文件'}), 400
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, f"supplier_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
        file.save(filepath)

        if filename.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath, engine='openpyxl')

        col_map = {'名称': 'name', 'name': 'name', '供应商名称': 'name',
                   '联系人': 'contact', '电话': 'phone', 'phone': 'phone',
                   '邮箱': 'email', 'email': 'email',
                   '地址': 'address', 'address': 'address',
                   '备注': 'notes', 'notes': 'notes'}
        df.columns = [str(c).strip() for c in df.columns]
        rename_map = {c: col_map[c] for c in df.columns if c in col_map}
        df.rename(columns=rename_map, inplace=True)

        count = 0
        for _, row in df.iterrows():
            name = str(row.get('name', '')).strip()
            if not name: continue
            SupplierModel.create(
                name, str(row.get('contact', '')).strip(), str(row.get('phone', '')).strip(),
                str(row.get('email', '')).strip(), str(row.get('address', '')).strip(),
                str(row.get('notes', '')).strip())
            count += 1
        return jsonify({'success': True, 'data': {'rows_imported': count}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/upload/customers', methods=['POST'])
def api_upload_customers():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请选择文件'}), 400
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, f"customer_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
        file.save(filepath)

        if filename.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath, engine='openpyxl')

        col_map = {'名称': 'name', 'name': 'name', '客户名称': 'name',
                   '联系人': 'contact', '电话': 'phone', 'phone': 'phone',
                   '邮箱': 'email', 'email': 'email',
                   '地址': 'address', 'address': 'address',
                   '备注': 'notes', 'notes': 'notes'}
        df.columns = [str(c).strip() for c in df.columns]
        rename_map = {c: col_map[c] for c in df.columns if c in col_map}
        df.rename(columns=rename_map, inplace=True)

        count = 0
        for _, row in df.iterrows():
            name = str(row.get('name', '')).strip()
            if not name: continue
            CustomerModel.create(
                name, str(row.get('contact', '')).strip(), str(row.get('phone', '')).strip(),
                str(row.get('email', '')).strip(), str(row.get('address', '')).strip(),
                str(row.get('notes', '')).strip())
            count += 1
        return jsonify({'success': True, 'data': {'rows_imported': count}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==========================================
#  AI 分析 API
# ==========================================
@app.route('/api/ai/health')
def api_ai_health():
    """AI 服务健康检查"""
    ok, msg = ai_service.health_check()
    return jsonify({'success': ok, 'message': msg})


@app.route('/api/ai/analyze')
def api_ai_analyze():
    """AI 库存分析"""
    try:
        query_type = request.args.get('type', 'general')
        result = ai_service.analyze_inventory(query_type)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        _logger.error(f"AI 分析错误: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/smart-import', methods=['POST'])
def api_ai_smart_import():
    """AI 智能导入：自然语言 → 解析 → 入库"""
    try:
        data = request.json
        text = data.get('text', '')
        if not text:
            return jsonify({'success': False, 'error': '请输入采购信息'}), 400

        # 1. AI 解析自然语言
        items = ai_service.smart_import(text)

        # 2. 逐个导入
        imported = []
        for item in items:
            name = item.get('name', '').strip()
            sku = item.get('sku', '').strip()
            category_name = item.get('category', '').strip()
            unit = item.get('unit', '个').strip() or '个'
            qty = int(item.get('quantity', 0))
            notes = item.get('notes', '')

            if not name or not sku or qty <= 0:
                continue

            # 处理分类
            category_id = None
            if category_name:
                existing_cats = CategoryModel.get_all()
                cat_map = {c['name']: c['id'] for c in existing_cats}
                if category_name in cat_map:
                    category_id = cat_map[category_name]
                else:
                    category_id = CategoryModel.create(category_name)

            # 处理供应商
            supplier_id = None
            supplier_name = item.get('supplier', '') or item.get('supplier_name', '')
            if supplier_name.strip():
                existing_sups = SupplierModel.get_all()
                sup_map = {s['name']: s['id'] for s in existing_sups}
                if supplier_name.strip() in sup_map:
                    supplier_id = sup_map[supplier_name.strip()]
                else:
                    supplier_id = SupplierModel.create(supplier_name.strip())

            # 查找或创建商品
            existing = ProductModel.get_by_sku(sku)
            if existing:
                prod_id = existing['id']
                ProductModel.update(prod_id, name, sku, category_id, supplier_id, unit,
                                    item.get('specification', ''), notes)
            else:
                prod_id = ProductModel.create(name, sku, category_id, supplier_id, unit,
                                              item.get('specification', ''), notes)

            # 入库
            old_inv = db.query_one("SELECT quantity FROM inventory WHERE product_id = %s", (prod_id,))
            old_qty = old_inv['quantity'] if old_inv else 0
            new_qty = old_qty + qty

            db.execute(
                """INSERT INTO inventory (product_id, quantity) VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE quantity = %s""",
                (prod_id, new_qty, new_qty)
            )
            item_price = float(item.get('price', 0) or 0)
            db.execute(
                """INSERT INTO transactions
                   (product_id, type, quantity, unit_price, supplier_id, before_qty, after_qty, batch_no, operator, notes)
                   VALUES (%s, 'in', %s, %s, %s, %s, %s, %s, %s, %s)""",
                (prod_id, qty, item_price, supplier_id, old_qty, new_qty, 'AI智能导入', 'AI', notes))
            imported.append({'name': name, 'sku': sku, 'quantity': qty})

        return jsonify({
            'success': True,
            'data': {
                'parsed_text': text,
                'items': items,
                'imported': imported,
                'count': len(imported),
            }
        })

    except json.JSONDecodeError as e:
        _logger.error(f"AI 解析失败: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': f'AI 解析结果格式错误，请重新描述: {str(e)}'}), 400
    except Exception as e:
        _logger.error(f"智能导入错误: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    """AI 对话（支持直接修改库存）"""
    try:
        data = request.json
        message = data.get('message', '')
        if not message:
            return jsonify({'success': False, 'error': '请输入问题'}), 400

        result = ai_service.chat(message)
        actions, reply = ai_service.parse_actions(result)

        # 执行 AI 指令
        log = []
        if actions:
            for act in actions:
                action_type = act.get('action', '')
                sku = act.get('sku', '').strip()
                qty = int(act.get('quantity', 0))
                notes = act.get('notes', '')

                # 供应商/客户操作（不需 sku/quantity）
                if action_type == 'add_supplier':
                    name_s = act.get('name', '').strip()
                    if name_s:
                        SupplierModel.create(name_s, act.get('contact', ''), act.get('phone', ''))
                        log.append(f"新增供应商: {name_s}")
                    continue
                if action_type == 'add_customer':
                    name_c = act.get('name', '').strip()
                    if name_c:
                        CustomerModel.create(name_c, act.get('contact', ''), act.get('phone', ''))
                        log.append(f"新增客户: {name_c}")
                    continue

                if action_type == 'create_order':
                    # AI 创建出库单
                    cust_name = act.get('customer', '')
                    op = act.get('operator', 'AI')
                    keeper = act.get('keeper', '')
                    wh = act.get('warehouse', '')
                    order_items = act.get('items', [])
                    # 处理客户
                    cid = None
                    if cust_name:
                        custs = CustomerModel.get_all()
                        cmap = {c['name']: c['id'] for c in custs}
                        if cust_name in cmap:
                            cid = cmap[cust_name]
                        else:
                            cid = CustomerModel.create(cust_name)
                    # 逐项出库
                    done = []
                    for oi in order_items:
                        sku = oi.get('sku', '').strip()
                        qty = int(oi.get('quantity', 0))
                        price = float(oi.get('price', 0) or 0)
                        prod = ProductModel.get_by_sku(sku)
                        if prod and qty > 0:
                            InventoryModel.stock_out(prod['id'], qty, 'AI出库单', op,
                                                     customer_id=cid, unit_price=price)
                            done.append({'name': prod['name'], 'sku': sku, 'quantity': qty,
                                         'sale_price': price, 'specification': prod.get('specification', '')})
                    # 生成 Excel
                    import openpyxl
                    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
                    from io import BytesIO
                    from flask import send_file
                    now_str = datetime.now().strftime('%Y-%m-%d')
                    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '出库单'
                    tf = Font(size=16, bold=True); hf = Font(color='FFFFFF', bold=True, size=11)
                    hfill = PatternFill(start_color='2563EB', end_color='2563EB', fill_type='solid')
                    bd = Border(left=Side(style='thin'), right=Side(style='thin'),
                                top=Side(style='thin'), bottom=Side(style='thin'))
                    ca = Alignment(horizontal='center', vertical='center')
                    ra = Alignment(horizontal='right', vertical='center')
                    la = Alignment(horizontal='left', vertical='center')
                    ws.merge_cells('A1:G1'); ws['A1'] = '出 库 单'; ws['A1'].font = tf; ws['A1'].alignment = ca
                    ws.cell(row=2, column=1, value='日期:').alignment = ra
                    ws.cell(row=2, column=2, value=now_str).alignment = la
                    ws.cell(row=2, column=4, value='客户:').alignment = ra
                    ws.cell(row=2, column=5, value=cust_name).alignment = la
                    for ci, h in enumerate(['序号','商品名称','规格','SKU','数量','单价(元)','金额(元)'], 1):
                        c = ws.cell(row=3, column=ci, value=h); c.fill = hfill; c.font = hf; c.alignment = ca; c.border = bd
                    total = 0
                    for idx, oi in enumerate(done, 1):
                        r = 3 + idx; sub = round(oi['sale_price'] * oi['quantity'], 2); total += sub
                        for ci, v in enumerate([idx, oi['name'], oi['specification'], oi['sku'],
                                                 oi['quantity'], oi['sale_price'], sub], 1):
                            c = ws.cell(row=r, column=ci, value=v); c.border = bd
                            c.alignment = ca if ci < 6 else ra
                    sr = 3 + len(done) + 1
                    ws.merge_cells(f'A{sr}:E{sr}'); ws.cell(row=sr, column=1, value='合计').font = Font(bold=True)
                    ws.cell(row=sr, column=1).alignment = ra
                    tc = ws.cell(row=sr, column=7, value=total); tc.font = Font(bold=True, color='FF0000')
                    tc.border = bd; tc.alignment = ra
                    bot = sr + 1
                    ws.cell(row=bot, column=1, value='经办人:').alignment = ra
                    ws.cell(row=bot, column=2, value=op).alignment = la
                    ws.cell(row=bot, column=4, value='库管:').alignment = ra
                    ws.cell(row=bot, column=5, value=keeper).alignment = la
                    ws.cell(row=bot+1, column=1, value='仓库编号:').alignment = ra
                    ws.cell(row=bot+1, column=2, value=wh).alignment = la
                    for i, w in enumerate([6,22,20,15,8,12,12], 1):
                        ws.column_dimensions[chr(64+i)].width = w
                    fname = f"出库单_{cust_name}_{now_str}.xlsx"
                    fpath = os.path.join(UPLOAD_FOLDER, fname)
                    wb.save(fpath)
                    log.append(f"出库单: {cust_name}, {len(done)}项, 合计¥{total}")
                    reply = (reply or '出库单已创建') + f'\n\n📥 [点击下载出库单](/uploads/{fname})'
                    continue

                if action_type == 'smart_import':
                    items = ai_service.smart_import(act.get('text', ''))
                    for item in items:
                        _do_ai_stock_in(item.get('sku', ''), int(item.get('quantity', 0)),
                                       item.get('name', ''), item.get('notes', ''))
                        log.append(f"智能导入: {item['name']} +{item['quantity']}")
                    continue

                if not sku or qty <= 0:
                    continue

                if action_type == 'stock_in':
                    _do_ai_stock_in(sku, qty, act.get('name', ''),
                                    unit=act.get('unit', ''), category_name=act.get('category', ''),
                                    supplier_name=act.get('supplier', ''))
                    log.append(f"入库: {sku} +{qty}")
                elif action_type == 'stock_out':
                    _do_ai_stock_out(sku, qty, notes)
                    log.append(f"出库: {sku} -{qty}")
                elif action_type == 'set_quantity':
                    _do_ai_set_quantity(sku, qty, notes)
                    log.append(f"调库: {sku} = {qty}")
                else:
                    log.append(f"未知操作: {action_type}")

        reply = reply or result
        if log:
            reply += f'\n\n✅ 已执行: {"；".join(log)}'

        return jsonify({'success': True, 'data': reply, 'actions': log})
    except Exception as e:
        _logger.error(f"AI 对话错误: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


# 关键字 → 分类自动映射
CATEGORY_KEYWORDS = {
    '电子产品': ['电阻', '电容', '芯片', 'PCB', '二极管', 'LED', '传感器', '单片机', '电源', '电池', '模块', '电路板', '数据线', '连接线', '充电器', '适配器'],
    '机械零件': ['螺丝', '螺母', '垫圈', '轴承', '齿轮', '弹簧', '密封', '阀门', '法兰', '螺栓', '销', '轴', '链条', '皮带'],
    '原材料': ['钢材', '铝', '铜', '塑料', '橡胶', '玻璃', '木材', '布料', '皮革', '海绵', '泡棉'],
    '包装材料': ['纸箱', '胶带', '气泡', '标签', '打包', '泡沫', '缠绕', '封口', '包装袋', '快递袋'],
    '办公用品': ['打印纸', '笔', '文件夹', '订书', '胶水', '便签', '笔记本', '墨盒', '硒鼓', '档案'],
    '五金工具': ['扳手', '钳子', '锤子', '螺丝刀', '电钻', '锯', '尺', '刀具', '剪刀', '卷尺'],
}

def _auto_category(name):
    """根据商品名自动推断分类"""
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                cats = CategoryModel.get_all()
                cat_map = {c['name']: c['id'] for c in cats}
                if cat in cat_map:
                    return cat_map[cat]
                return CategoryModel.create(cat)
    return None


def _do_ai_stock_in(sku, qty, name_hint='', notes='', unit='', category_name='', supplier_name=''):
    """AI 执行入库（含自动分类和供应商关联）"""
    prod = ProductModel.get_by_sku(sku)
    if not prod:
        # 自动推断分类
        cat_id = _auto_category(name_hint or sku)
        if not cat_id and category_name:
            cats = CategoryModel.get_all()
            cat_map = {c['name']: c['id'] for c in cats}
            if category_name in cat_map:
                cat_id = cat_map[category_name]
            else:
                cat_id = CategoryModel.create(category_name)
        # 处理供应商
        sup_id = None
        if supplier_name:
            sups = SupplierModel.get_all()
            sup_map = {s['name']: s['id'] for s in sups}
            if supplier_name in sup_map:
                sup_id = sup_map[supplier_name]
            else:
                sup_id = SupplierModel.create(supplier_name)
        # 创建商品
        prod_id = ProductModel.create(
            name_hint or sku, sku, category_id=cat_id, supplier_id=sup_id,
            unit=unit or '个'
        )
    else:
        prod_id = prod['id']
        sup_id = prod.get('supplier_id')  # 已有商品用已有供应商
    InventoryModel.stock_in(prod_id, qty, 'AI操作', 'AI', notes, supplier_id=sup_id)
    # 更新供应商
    if sup_id:
        db.execute("UPDATE products SET supplier_id=%s WHERE id=%s", (sup_id, prod_id))


def _do_ai_stock_out(sku, qty, notes=''):
    """AI 执行出库"""
    prod = ProductModel.get_by_sku(sku)
    if not prod:
        raise ValueError(f'商品 {sku} 不存在')
    InventoryModel.stock_out(prod['id'], qty, 'AI操作', 'AI', notes)


def _do_ai_set_quantity(sku, qty, notes=''):
    """AI 执行库存调整"""
    prod = ProductModel.get_by_sku(sku)
    if not prod:
        raise ValueError(f'商品 {sku} 不存在')
    old = db.query_one("SELECT quantity FROM inventory WHERE product_id=%s", (prod['id'],))
    old_qty = old['quantity'] if old else 0
    db.execute("UPDATE inventory SET quantity=%s WHERE product_id=%s", (qty, prod['id']))
    if qty != old_qty:
        t = 'in' if qty > old_qty else 'out'
        db.execute(
            """INSERT INTO transactions
               (product_id, type, quantity, before_qty, after_qty, operator, notes)
               VALUES (%s, %s, %s, %s, %s, 'AI', %s)""",
            (prod['id'], t, abs(qty - old_qty), old_qty, qty, notes)
        )


# ==========================================
#  启动
# ==========================================
if __name__ == '__main__':
    print("=" * 60)
    print("  仓库管理系统启动中...")
    print("  访问地址: http://0.0.0.0:5050")
    print("  AI 服务: http://100.101.108.100:1234")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5050, debug=True)
