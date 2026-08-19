/**
 * 仓库管理系统 - 前端 SPA 应用
 */

const API = '/api';
let currentPage = 'dashboard';

// ==========================================
//  工具函数
// ==========================================
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

// HTML 转义：所有用户可控数据（商品名/客户名/文件名/AI 回复等）经 innerHTML 渲染前必须先过这里，
// 防止存储型 XSS。注意：内联事件处理器里只允许传数字 id（Number() 强转），字符串一律走内存查找。
function escapeHtml(v) {
    return String(v == null ? '' : v)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// 数字强转：内联 onclick/onblur 里插值的 id/index 必须是纯数字，杜绝属性逃逸
function num(v) { const n = Number(v); return isNaN(n) ? 0 : n; }

async function fetchAPI(url, options = {}) {
    // 默认 2 分钟超时，避免请求挂起时页面永久卡死；AI 接口可传更长 timeout
    const timeoutMs = options.timeout || 120000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...options.headers },
            ...options,
            signal: controller.signal,
        });
        if (!resp.ok) {
            // 5xx/4xx：优先解析 JSON 错误体，失败则回退到状态码描述
            let detail = `服务器错误 (HTTP ${resp.status})`;
            try {
                const body = await resp.json();
                if (body && body.error) detail = body.error;
            } catch (_) { /* 非 JSON 响应（如 debug 页面） */ }
            return { success: false, error: detail };
        }
        return await resp.json();
    } catch (err) {
        if (err.name === 'AbortError') {
            return { success: false, error: '请求超时，请稍后重试' };
        }
        return { success: false, error: `网络错误: ${err.message}` };
    } finally {
        clearTimeout(timer);
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleString('zh-CN');
}

function showToast(message, type = 'info') {
    const container = $('#toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        padding: 12px 20px; margin-bottom: 8px; border-radius: 8px;
        color: #fff; font-size: 14px; animation: slideIn 0.3s ease;
        ${type === 'success' ? 'background: #10b981;' :
          type === 'error' ? 'background: #ef4444;' :
          type === 'warning' ? 'background: #f59e0b;' :
          'background: #3b82f6;'}
    `;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ==========================================
//  页面导航
// ==========================================
function navigateTo(page) {
    currentPage = page;
    // 关闭残留的全屏弹层（进货批次等动态挂在 body 上），但保留商品弹窗节点
    $$('.modal-overlay').forEach(el => { if (el.id !== 'product-modal') el.remove(); });
    // 更新导航激活状态
    $$('.nav-item').forEach(el => el.classList.remove('active'));
    $(`.nav-item[data-page="${page}"]`)?.classList.add('active');
    // 切换页面
    $$('.page').forEach(el => el.classList.remove('active'));
    $(`#page-${page}`)?.classList.add('active');
    // 更新标题
    const titles = {
        dashboard: '仪表盘',
        products: '商品管理',
        inventory: '库存管理',
        categories: '分类管理',
        suppliers: '供应商管理',
        customers: '客户管理',
        logs: '操作日志',
        order: '出库单',
        'inbound-order': '入库单识别',
        transactions: '出入库记录',
        upload: '数据导入',
        ai: 'AI 智能分析',
        accounts: '账户管理',
    };
    $('#page-title').textContent = titles[page] || '';
    // 加载数据
    if (page === 'dashboard') loadDashboard();
    if (page === 'products') loadProducts();
    if (page === 'inventory') loadInventory();
    if (page === 'categories') loadCategories();
    if (page === 'suppliers') loadSuppliers();
    if (page === 'customers') loadCustomers();
    if (page === 'order') loadOrderPage();
    if (page === 'inbound-order') initInboundOrder();
    if (page === 'transactions') loadTransactions();
    if (page === 'logs') loadAuditLog();
    if (page === 'upload') loadUploads();
    if (page === 'ai') initAIPage();
    if (page === 'accounts') loadAccounts();
}

$$('.nav-item').forEach(item => {
    item.addEventListener('click', () => navigateTo(item.dataset.page));
});

// ==========================================
//  仪表盘
// ==========================================
async function loadDashboard() {
    const result = await fetchAPI(`${API}/dashboard`);
    if (!result.success) {
        showToast('加载仪表盘失败: ' + result.error, 'error');
        return;
    }
    const d = result.data;
    $('#stat-products').textContent = d.total_products;
    $('#stat-categories').textContent = d.total_categories;
    $('#stat-quantity').textContent = d.total_quantity.toLocaleString();
    $('#stat-lowstock').textContent = d.low_stock_count;

    // 加载低库存预警
    const lowResult = await fetchAPI(`${API}/inventory/low-stock`);
    if (lowResult.success) {
        renderLowStockTable(lowResult.data);
    }

    // 加载最近交易
    const txnResult = await fetchAPI(`${API}/transactions?limit=10`);
    if (txnResult.success) {
        renderRecentTransactions(txnResult.data);
    }

    // 渲染分类图表
    if (d.category_stats) {
        renderCategoryChart(d.category_stats);
    }
}

function renderLowStockTable(items) {
    const tbody = $('#low-stock-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:20px;">暂无低库存预警 🎉</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(item => `
        <tr>
            <td><strong>${escapeHtml(item.product_name)}</strong></td>
            <td>${escapeHtml(item.sku)}</td>
            <td><span class="badge badge-danger">${num(item.quantity)}</span></td>
            <td>${num(item.min_stock)}</td>
            <td>${escapeHtml(item.location || '-')}</td>
        </tr>
    `).join('');
}

function renderRecentTransactions(items) {
    const tbody = $('#recent-txn-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#94a3b8;padding:20px;">暂无交易记录</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(t => `
        <tr>
            <td>${escapeHtml(t.product_name)}</td>
            <td><span class="badge ${t.type === 'in' ? 'badge-success' : 'badge-warning'}">${t.type === 'in' ? '入库' : '出库'}</span></td>
            <td>${num(t.quantity)}</td>
            <td>${formatDate(t.created_at)}</td>
        </tr>
    `).join('');
}

function renderCategoryChart(stats) {
    const canvas = $('#category-chart');
    if (!canvas || !stats || stats.length === 0) return;

    // 简单柱状图用纯CSS实现
    const container = canvas.parentElement;
    const maxQty = Math.max(...stats.map(s => s.total_qty), 1);
    container.innerHTML = `
        <div style="display:flex;align-items:flex-end;gap:20px;height:180px;padding-top:20px;">
            ${stats.map(s => `
                <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;">
                    <span style="font-size:12px;font-weight:600;">${num(s.total_qty)}</span>
                    <div style="width:100%;max-width:60px;height:${((Number(s.total_qty)||0)/maxQty)*140}px;
                                background:linear-gradient(180deg, #3b82f6, #2563eb);
                                border-radius:6px 6px 0 0;min-height:4px;"></div>
                    <span style="font-size:11px;color:#64748b;text-align:center;">${escapeHtml(s.name)}</span>
                </div>
            `).join('')}
        </div>
    `;
}

// ==========================================
//  商品管理
// ==========================================
let currentProducts = [];

async function loadProducts() {
    const keyword = $('#product-search')?.value || '';
    const result = await fetchAPI(`${API}/products${keyword ? `?search=${encodeURIComponent(keyword)}` : ''}`);
    if (result.success) {
        currentProducts = result.data;
        renderProductsTable(result.data);
    } else {
        showToast('加载商品失败: ' + result.error, 'error');
    }
}

function renderProductsTable(products) {
    const tbody = $('#products-tbody');
    if (!products || products.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:30px;">暂无商品，请先导入数据</td></tr>';
        return;
    }
    tbody.innerHTML = products.map(p => `
        <tr>
            <td><strong>${escapeHtml(p.name)}</strong></td>
            <td>${escapeHtml(p.sku)}</td>
            <td>${escapeHtml(p.category_name || '-')}</td>
            <td>${escapeHtml(p.supplier_name || '-')}</td>
            <td>${escapeHtml(p.unit)}</td>
            <td>${escapeHtml(p.specification || '-')}</td>
            <td>${num(p.quantity).toLocaleString()}</td>
            <td>${escapeHtml(p.location || '-')}</td>
            <td>
                <div class="btn-group">
                    <button class="btn btn-outline btn-sm" onclick="editProduct(${num(p.id)})">编辑</button>
                    <button class="btn btn-outline btn-sm" onclick="quickStockIn(${num(p.id)})">入库</button>
                    <button class="btn btn-outline btn-sm" onclick="quickStockOut(${num(p.id)})">出库</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteProduct(${num(p.id)})">删除</button>
                </div>
            </td>
        </tr>
    `).join('');
}

async function loadCategoriesForSelect() {
    const result = await fetchAPI(`${API}/categories`);
    if (result.success) {
        const select = $('#product-category');
        if (select) {
            select.innerHTML = '<option value="">请选择分类</option>' +
                result.data.map(c => `<option value="${num(c.id)}">${escapeHtml(c.name)}</option>`).join('');
        }
    }
}

async function showProductModal(productId = null) {
    // 等待下拉选项加载完成后再赋值，避免 setTimeout 竞态导致编辑弹窗显示为空
    await Promise.all([loadCategoriesForSelect(), loadSuppliersForSelect()]);
    const modal = $('#product-modal');
    const title = $('#product-modal-title');
    if (productId) {
        title.textContent = '编辑商品';
        const p = currentProducts.find(x => x.id === productId);
        if (p) {
            $('#product-id').value = p.id;
            $('#product-name').value = p.name;
            $('#product-sku').value = p.sku;
            $('#product-category').value = p.category_id || '';
            $('#product-supplier').value = p.supplier_id || '';
            $('#product-unit').value = p.unit;
            $('#product-spec').value = p.specification || '';
            $('#product-unit-price').value = p.unit_price ?? 0;
            $('#product-sale-price').value = p.sale_price ?? 0;
            $('#product-location').value = p.location || '';
            $('#product-quantity').value = p.quantity ?? 0;
            $('#product-min-stock').value = p.min_stock ?? 0;
            $('#product-max-stock').value = p.max_stock ?? 9999;
            $('#product-desc').value = p.description || '';
        }
    } else {
        title.textContent = '新增商品';
        $('#product-id').value = '';
        $('#product-form').reset();
        $('#product-max-stock').value = '9999';
    }
    modal.classList.add('active');
}

function hideProductModal() {
    $('#product-modal').classList.remove('active');
}

async function saveProduct() {
    const id = $('#product-id').value;
    const data = {
        name: $('#product-name').value.trim(),
        sku: $('#product-sku').value.trim(),
        category_id: $('#product-category').value || null,
        supplier_id: $('#product-supplier').value || null,
        unit: $('#product-unit').value.trim() || '个',
        specification: $('#product-spec').value.trim(),
        description: $('#product-desc').value.trim(),
        location: $('#product-location').value.trim(),
        quantity: parseInt($('#product-quantity').value) || 0,
        min_stock: parseInt($('#product-min-stock').value) || 0,
        max_stock: parseInt($('#product-max-stock').value) || 9999,
        unit_price: parseFloat($('#product-unit-price').value) || 0,
        sale_price: parseFloat($('#product-sale-price').value) || 0,
    };
    if (!data.name || !data.sku) {
        showToast('商品名称和 SKU 不能为空', 'warning');
        return;
    }
    const url = id ? `${API}/products/${id}` : `${API}/products`;
    const method = id ? 'PUT' : 'POST';
    const result = await fetchAPI(url, { method, body: JSON.stringify(data) });
    if (result.success) {
        showToast(id ? '商品更新成功' : '商品创建成功', 'success');
        hideProductModal();
        loadProducts();
    } else {
        showToast('保存失败: ' + result.error, 'error');
    }
}

async function deleteProduct(id) {
    // 名称从内存数据查找（不再经内联 onclick 字符串传递，防属性逃逸）
    const p = currentProducts.find(x => x.id === id);
    const name = (p && p.name) ? p.name : ('商品 ' + id);
    if (!confirm(`确定要删除商品 "${name}" 吗？此操作不可撤销。`)) return;
    const result = await fetchAPI(`${API}/products/${id}`, { method: 'DELETE' });
    if (result.success) {
        showToast('删除成功', 'success');
        loadProducts();
    } else {
        showToast('删除失败: ' + result.error, 'error');
    }
}

function editProduct(id) { showProductModal(id); }

// ==========================================
//  快速出入库
// ==========================================
async function quickStockIn(productId) {
    // 名称从内存数据查找（不再经内联 onclick 字符串传递，防属性逃逸）
    var prod = currentProducts.find(function(p) { return p.id === productId; });
    var name = (prod && prod.name) ? prod.name : ('商品 ' + productId);
    var qty = prompt('请输入 "' + name + '" 入库数量:');
    if (!qty || isNaN(qty) || parseInt(qty) <= 0) return;
    // 选供应商
    var suppR = await fetchAPI(API + '/suppliers');
    var supps = suppR.success ? suppR.data : [];
    var suppList = supps.map(function(s,i) { return (i+1)+'. '+s.name; }).join('\n');
    var suppInp = prompt('选择供应商（输编号或新名称）：\n' + (suppList || '暂无'));
    if (suppInp === null) return;
    var supplierId = null;
    var idx = parseInt(suppInp) - 1;
    if (!isNaN(idx) && supps[idx]) {
        supplierId = supps[idx].id;
    } else if (suppInp.trim()) {
        var cr = await fetchAPI(API + '/suppliers', { method: 'POST', body: JSON.stringify({ name: suppInp.trim() }) });
        if (cr.success) supplierId = cr.id;
    }
    // 进货价（复用函数开头查到的 prod）
    var ref = (prod && prod.unit_price > 0) ? '(上次进价: ' + prod.unit_price + ')' : '';
    var priceInp = prompt('请输入 "' + name + '" 进货单价(元): ' + ref);
    if (priceInp === null) return;
    var unitPrice = parseFloat(priceInp) || 0;
    doStockIn(productId, parseInt(qty), supplierId, unitPrice);
}



async function doStockIn(productId, quantity, supplierId, unitPrice) {
    supplierId = supplierId || null;
    unitPrice = unitPrice || 0;
    var body = { product_id: productId, quantity: quantity, unit_price: unitPrice, operator: '管理员' };
    if (supplierId) body.supplier_id = supplierId;
    var result = await fetchAPI(API + '/inventory/stock-in', {
        method: 'POST',
        body: JSON.stringify(body),
    });
    if (result.success) {
        showToast('入库成功', 'success');
        loadProducts();
        loadInventory();
    } else {
        showToast('入库失败: ' + result.error, 'error');
    }
}


let _customersCache = null;

async function quickStockOut(productId) {
	    // 名称从内存数据查找（不再经内联 onclick 字符串传递，防属性逃逸）
	    const prod = currentProducts.find(p => p.id === productId);
	    const name = (prod && prod.name) ? prod.name : ('商品 ' + productId);
	    const qty = prompt('请输入 "' + name + '" 出库数量:');
	    if (!qty || isNaN(qty) || parseInt(qty) <= 0) return;
	    const ref = (prod && prod.sale_price > 0) ? '(参考价: ' + prod.sale_price + ')' : '';
	    const sp = prompt('请输入 "' + name + '" 出库单价(元): ' + ref);
	    if (sp === null) return;
	    const unitPrice = parseFloat(sp) || 0;
	    if (!_customersCache) {
	        const r = await fetchAPI('/api/customers');
	        _customersCache = r.success ? r.data : [];
	    }
	    const list = _customersCache.map((c,i) => (i+1)+'. '+c.name).join('\n');
	    const inp = prompt('选择客户（输编号或新名称）：\n' + (list || '暂无'));
	    if (inp === null) return;
	    let cid = null;
	    const idx = parseInt(inp) - 1;
	    if (!isNaN(idx) && _customersCache[idx]) {
	        cid = _customersCache[idx].id;
	    } else if (inp.trim()) {
	        const cr = await fetchAPI('/api/customers', { method: 'POST', body: JSON.stringify({ name: inp.trim() }) });
	        if (cr.success) { cid = cr.id; _customersCache = null; }
	    }
	    doStockOut(productId, parseInt(qty), cid, unitPrice);
	}

async function doStockOut(productId, quantity, customerId, unitPrice) {
    customerId = customerId || null;
    unitPrice = unitPrice || 0;
    var body = { product_id: productId, quantity: quantity, unit_price: unitPrice, operator: '管理员' };
    if (customerId) body.customer_id = customerId;
    var result = await fetchAPI(API + '/inventory/stock-out', {
        method: 'POST',
        body: JSON.stringify(body),
    });
    if (result.success) {
        showToast('出库成功', 'success');
        loadProducts();
        loadInventory();
    } else {
        showToast('出库失败: ' + result.error, 'error');
    }
}

// ==========================================
//  库存管理
// ==========================================
let currentInventory = [];   // 内存缓存：供内联事件处理器按 id 查名称/库位（替代字符串传参）

async function loadInventory() {
    const result = await fetchAPI(`${API}/inventory`);
    if (result.success) {
        currentInventory = result.data;
        renderInventoryTable(result.data);
    } else {
        showToast('库存加载失败: ' + result.error, 'error');
    }
}

function renderInventoryTable(items) {
    const tbody = $('#inventory-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;color:#94a3b8;padding:30px;">暂无库存数据</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(item => {
        const isLow = item.quantity <= item.min_stock;
        return `
            <tr>
                <td><strong>${escapeHtml(item.product_name)}</strong></td>
                <td>${escapeHtml(item.sku)}</td>
                <td>${escapeHtml(item.category_name || '-')}</td>
                <td>${escapeHtml(item.supplier_name || '-')}</td>
                <td style="font-weight:700;color:${isLow ? 'var(--danger)' : 'var(--text)'};">
                    ${num(item.quantity).toLocaleString()}
                </td>
                <td>${num(item.min_stock)}</td>
                <td>${num(item.max_stock)}</td>
                <td>${escapeHtml(item.location || '-')}</td>
                <td>${(item.latest_price && item.latest_price > 0) ? '¥'+parseFloat(item.latest_price).toFixed(2) : '-'}</td>
                <td>${(item.avg_price && item.avg_price > 0) ? '¥'+parseFloat(item.avg_price).toFixed(2) : '-'}</td>
                <td>
                    <button class="btn btn-outline btn-sm" onclick="showBatchDetail(${num(item.product_id)})">批次</button>
                    <button class="btn btn-outline btn-sm" onclick="editInventory(${num(item.product_id)})">调整</button>
                </td>
            </tr>
        `;
    }).join('');
}

async function showBatchDetail(pid) {
    // 名称从内存数据查找（不再经内联 onclick 字符串传递，防属性逃逸）
    var inv = (currentInventory || []).find(function(x) { return x.product_id === pid; });
    var pname = (inv && inv.product_name) ? inv.product_name : ('商品 ' + pid);
    var r = await fetchAPI(API + '/transactions?limit=500');
    if (!r.success) return;
    var batches = r.data.filter(function(t) { return t.product_id === pid && t.type === 'in'; });
    var html = '<h3>' + escapeHtml(pname) + ' - 进货批次</h3><table style=\"width:100%;font-size:13px;\"><tr><th>时间</th><th>数量</th><th>进价</th><th>金额</th><th>供应商</th></tr>';
    if (batches.length === 0) html += '<tr><td colspan=\"5\" style=\"text-align:center;\">暂无进货记录</td></tr>';
    else batches.forEach(function(b) {
        html += '<tr><td>' + formatDate(b.created_at) + '</td><td>+' + (Number(b.quantity)||0) + '</td><td>' + ((b.unit_price > 0) ? '¥' + Number(b.unit_price).toLocaleString() : '-') + '</td><td>' + ((b.unit_price > 0) ? '¥' + (Number(b.quantity)*Number(b.unit_price)).toLocaleString() : '-') + '</td><td>' + escapeHtml(b.supplier_name || '-') + '</td></tr>';
    });
    html += '</table>';
    var modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.onclick = function(e) { if (e.target === modal) modal.remove(); };
    modal.innerHTML = '<div class="modal" style="max-width:700px;"><div class="modal-header"><h3>' + escapeHtml(pname) + ' - 进货批次</h3><button class="modal-close" id="batch-close-btn">✕</button></div><div class="modal-body">' + html + '</div></div>';
    setTimeout(function() {
        var btn = document.getElementById('batch-close-btn');
        if (btn) btn.onclick = function() { modal.remove(); };
    }, 10);
    document.body.appendChild(modal);
}

function editInventory(productId) {
    // 原值从内存数据查找（不再经内联 onclick 字符串传递，防属性逃逸）
    const inv = (currentInventory || []).find(x => x.product_id === productId);
    const qty = inv ? inv.quantity : 0;
    const loc = inv ? (inv.location || '') : '';
    const minS = inv ? inv.min_stock : 0;
    const maxS = inv ? inv.max_stock : 9999;
    const newQty = prompt('新库存数量:', qty);
    if (newQty === null || isNaN(parseInt(newQty))) return;
    const newLoc = prompt('库位:', loc);
    const newMin = prompt('最低库存:', minS);
    const newMax = prompt('最高库存:', maxS);
    updateInventory(productId, parseInt(newQty), newLoc, parseInt(newMin), parseInt(newMax));
}

async function updateInventory(productId, quantity, location, minStock, maxStock) {
    const result = await fetchAPI(`${API}/inventory/${productId}`, {
        method: 'PUT',
        body: JSON.stringify({ quantity, location, min_stock: minStock, max_stock: maxStock }),
    });
    if (result.success) {
        showToast('库存更新成功', 'success');
        loadInventory();
    } else {
        showToast('更新失败: ' + result.error, 'error');
    }
}

// ==========================================
//  交易记录
// ==========================================
async function loadTransactions() {
    const result = await fetchAPI(`${API}/transactions?limit=200`);
    if (result.success) {
        renderTransactionsTable(result.data);
    } else {
        showToast('交易记录加载失败: ' + result.error, 'error');
    }
}

function renderTransactionsTable(items) {
    const tbody = $('#txn-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#94a3b8;padding:30px;">暂无交易记录</td></tr>';
        return;
    }
        var txHtml = '';
    for (var ti = 0; ti < items.length; ti++) {
        var t = items[ti];
        var priceStr = (t.unit_price && t.unit_price > 0) ? '¥' + t.unit_price : '-';
        var totalStr = (t.unit_price && t.unit_price > 0) ? '¥' + (t.quantity * t.unit_price).toLocaleString() : '-';
        txHtml += '<tr>' +
            '<td>' + formatDate(t.created_at) + '</td>' +
            '<td>' + escapeHtml(t.product_name) + '</td>' +
            '<td><span class="badge ' + (t.type === 'in' ? 'badge-success' : 'badge-warning') + '">' + (t.type === 'in' ? '入库' : '出库') + '</span></td>' +
            '<td>' + num(t.quantity) + '</td>' +
            '<td>' + num(t.before_qty) + ' → ' + num(t.after_qty) + '</td>' +
            '<td>' + priceStr + '</td>' +
            '<td>' + totalStr + '</td>' +
            '<td>' + escapeHtml(t.customer_name || '-') + '</td>' +
            '<td>' + escapeHtml(t.operator || '-') + '</td>' +
            '<td>' + escapeHtml(t.notes || '-') + '</td></tr>';
    }
    tbody.innerHTML = txHtml || '<tr><td colspan="10" style="text-align:center;color:#94a3b8;padding:30px;">暂无交易记录</td></tr>';
}

// ==========================================
//  Excel 上传
// ==========================================
async function loadUploads() {
    const result = await fetchAPI(`${API}/uploads`);
    if (result.success) {
        renderUploadsTable(result.data);
    } else {
        showToast('上传记录加载失败: ' + result.error, 'error');
    }
}

function renderUploadsTable(items) {
    const tbody = $('#uploads-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:30px;">暂无上传记录</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(u => `
        <tr>
            <td>${escapeHtml(u.filename)}</td>
            <td>${(Number(u.file_size) / 1024).toFixed(1)} KB</td>
            <td><span class="badge badge-${u.status==='success'?'success':u.status==='failed'?'danger':u.status==='partial'?'warning':'info'}">${escapeHtml(u.status)}</span></td>
            <td>${num(u.rows_processed)}</td>
            <td>${formatDate(u.uploaded_at)}</td>
        </tr>
    `).join('');
}

// 拖拽上传
const uploadZone = $('#upload-zone');
if (uploadZone) {
    uploadZone.addEventListener('click', () => $('#file-input').click());
    uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
    uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) uploadFile(files[0]);
    });
}

$('#file-input')?.addEventListener('change', (e) => {
    if (e.target.files.length > 0) uploadFile(e.target.files[0]);
});

function updateUploadHint() {
    const isIncrement = $('#import-mode').checked;
    const hint = $('#upload-hint');
    const base = `<strong>📋 Excel 模板说明：</strong><br>
        只需填写 <code>商品名称</code> 即可，SKU 和库位会自动生成<br>
        可选列：<code>SKU</code> <code>分类</code> <code>单位</code> <code>规格</code> <code>数量</code> <code>库位</code> <code>最低库存</code> <code>最高库存</code> <code>描述</code><br>`;
    if (isIncrement) {
        hint.innerHTML = base + `<span style="color:#10b981;">当前模式：增量导入（Excel 中的数量将累加到现有库存）</span>`;
    } else {
        hint.innerHTML = base + `<span style="color:#3b82f6;">当前模式：全量覆盖（Excel 中的数量直接作为最终库存）</span>`;
    }
}

async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', $('#import-mode').checked ? 'increment' : 'replace');

    $('#upload-status').textContent = '正在上传和解析...';
    $('#upload-status').style.color = '#3b82f6';

    try {
        const resp = await fetch(`${API}/upload`, { method: 'POST', body: formData });
        const result = await resp.json();
        if (result.success) {
            const partial = result.data.status === 'partial';
            $('#upload-status').textContent = partial
                ? `⚠️ 导入完成（部分行失败）：处理 ${result.data.rows_imported} 条数据`
                : `✅ 导入成功！处理 ${result.data.rows_imported} 条数据`;
            $('#upload-status').style.color = partial ? '#f59e0b' : '#10b981';
            if (result.data.errors && result.data.errors.length > 0) {
                showToast(`部分数据导入失败: ${result.data.errors.slice(0, 3).join('; ')}`, 'warning');
            }
            loadUploads();
            // 刷新其他页面数据
            loadDashboard();
            loadProducts();
            loadInventory();
        } else {
            $('#upload-status').textContent = `❌ ${result.error}`;
            $('#upload-status').style.color = '#ef4444';
        }
    } catch (err) {
        $('#upload-status').textContent = `❌ 网络错误: ${err.message}`;
        $('#upload-status').style.color = '#ef4444';
    } finally {
        // 清空文件输入，允许连续选择同一个文件
        const fi = $('#file-input');
        if (fi) fi.value = '';
    }
}

// ==========================================
//  AI 智能导入
// ==========================================
async function smartImport() {
    const input = $('#smart-import-input');
    const text = input.value.trim();
    if (!text) { showToast('请描述采购信息', 'warning'); return; }

    const status = $('#smart-import-status');
    status.textContent = '🤖 AI 正在解析...';
    status.style.color = '#3b82f6';
    input.disabled = true;

    const result = await fetchAPI(`${API}/ai/smart-import`, {
        method: 'POST',
        body: JSON.stringify({ text }),
    });

    input.disabled = false;
    if (result.success) {
        const d = result.data;
        // AI 解析出的商品名属于用户可控数据，必须转义后再入 HTML
        status.innerHTML = `✅ 成功导入 ${num(d.count)} 条：${d.imported.map(i => `${escapeHtml(i.name)} +${num(i.quantity)}`).join('，')}`;
        status.style.color = '#10b981';
        input.value = '';
        // 刷新其他数据
        loadProducts();
        loadInventory();
        loadTransactions();
    } else {
        status.textContent = '❌ ' + result.error;
        status.style.color = '#ef4444';
    }
}

// ==========================================
//  AI 智能分析
// ==========================================
async function initAIPage() {
    checkAIHealth();  // 仅检查健康状态
}

async function checkAIHealth() {
    const status = $('#ai-status-indicator');
    const text = $('#ai-status-text');
    try {
        const result = await fetchAPI(`${API}/ai/health`);
        if (result.success) {
            status.className = 'ai-status online';
            text.textContent = 'AI 服务在线';
        } else {
            status.className = 'ai-status offline';
            text.textContent = 'AI 服务离线';
        }
    } catch {
        status.className = 'ai-status offline';
        text.textContent = 'AI 服务离线';
    }
}

async function runAIAnalysis(type) {
    const container = $('#ai-result');
    container.innerHTML = '<div style="text-align:center;padding:40px;color:#94a3b8;">🤖 AI 正在分析中，请稍候...</div>';

    const result = await fetchAPI(`${API}/ai/analyze?type=${type}`);
    if (result.success) {
        // 与聊天页一致：先整体转义再渲染（防 AI 回显被污染的商品名造成存储型 XSS）
        container.innerHTML = `<div style="white-space:pre-wrap;line-height:1.8;font-size:14px;">${renderChatReply(result.data)}</div>`;
    } else {
        container.innerHTML = `<div style="color:var(--danger);">分析失败: ${escapeHtml(result.error)}</div>`;
    }
}

// 渲染 AI 回复：先整体转义 HTML（防 XSS），再把 [文字](/相对路径) 形式的 markdown 链接
// 转成可点击的 <a>。只放行以 / 开头的站内相对路径，javascript:/http:// 等一律按纯文本显示。
function renderChatReply(text) {
    const escaped = String(text == null ? '' : text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    return escaped.replace(/\[([^\]]+)\]\((\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

async function sendAIChat() {
    const input = $('#ai-chat-input');
    const message = input.value.trim();
    if (!message) return;

    const messagesContainer = $('#ai-chat-messages');
    // 添加用户消息（用 textContent，避免自 XSS）
    const userMsgEl = document.createElement('div');
    userMsgEl.className = 'ai-chat-msg user';
    userMsgEl.textContent = message;
    messagesContainer.appendChild(userMsgEl);
    input.value = '';
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // 添加 ai 占位
    const aiPlaceholder = document.createElement('div');
    aiPlaceholder.className = 'ai-chat-msg assistant';
    aiPlaceholder.textContent = '思考中...';
    messagesContainer.appendChild(aiPlaceholder);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    const result = await fetchAPI(`${API}/ai/chat`, {
        method: 'POST',
        body: JSON.stringify({ message }),
    });

    if (result.success) {
        // 用安全渲染替换纯文本：出库单下载链接 [点击下载出库单](/uploads/...) 可点击
        aiPlaceholder.innerHTML = renderChatReply(result.data);
    } else {
        aiPlaceholder.textContent = '抱歉，分析出错: ' + result.error;
    }
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

$('#ai-chat-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendAIChat();
    }
});

// ==========================================
//  初始化
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    // Toast 容器
    const toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    toastContainer.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;max-width:400px;';
    document.body.appendChild(toastContainer);

    // 添加全局关闭模态框
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('modal-overlay')) {
            e.target.classList.remove('active');
        }
    });

    // 搜索栏事件
    $('#product-search')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') loadProducts();
    });

    // 加载首页
    navigateTo('dashboard');
    checkAIHealth();
    // 探测当前账户（管理员才显示"账户管理"入口）
    loadAuthInfo();
});

// ==========================================
//  分类管理
// ==========================================
async function quickAddCategory() {
    var inp = $('#category-quick-add');
    var name = inp.value.trim();
    if (!name) return;
    var r = await fetchAPI(API + '/categories', { method: 'POST', body: JSON.stringify({ name: name }) });
    if (r.success) { showToast('已添加', 'success'); inp.value = ''; loadCategories(); }
    else showToast(r.error, 'error');
}

async function loadCategories() {
    var r = await fetchAPI(API + '/categories');
    if (r.success) {
        var t = $('#categories-tbody');
        t.innerHTML = r.data.map(function(c) {
            return '<tr>' +
                '<td contenteditable="true" onblur="updateCategoryField(' + num(c.id) + ',\'name\',this.textContent)" style="font-weight:600;">' + escapeHtml(c.name) + '</td>' +
                '<td contenteditable="true" onblur="updateCategoryField(' + num(c.id) + ',\'description\',this.textContent)">' + escapeHtml(c.description || '-') + '</td>' +
                '<td><button class="btn btn-danger btn-sm" onclick="deleteCategory(' + num(c.id) + ')">删除</button></td>' +
                '</tr>';
        }).join('') || '<tr><td colspan="3" style="text-align:center;padding:30px;">暂无分类</td></tr>';
    }
}

async function updateCategoryField(id, field, value) {
    var r = await fetchAPI(API + '/categories');
    if (!r.success) return;
    var cat = r.data.find(function(x) { return x.id === id; });
    if (!cat) return;
    var data = { name: cat.name, description: cat.description || '' };
    data[field] = value.trim();
    await fetchAPI(API + '/categories/' + id, { method: 'PUT', body: JSON.stringify(data) });
}

async function deleteCategory(id) {
    if (!confirm('确定删除？')) return;
    var r = await fetchAPI(API + '/categories/' + id, { method: 'DELETE' });
    if (r.success) { showToast('已删除', 'success'); loadCategories(); }
    else showToast(r.error, 'error');
}

// ==========================================
//  供应商管理
// ==========================================
let currentSuppliers = [];

async function quickAddSupplier() {
    const inp = $('#supplier-quick-add');
    const name = inp.value.trim();
    if (!name) return;
    const r = await fetchAPI(`${API}/suppliers`, { method: 'POST', body: JSON.stringify({ name }) });
    if (r.success) { showToast('供应商已添加', 'success'); inp.value = ''; loadSuppliers(); }
    else showToast(r.error, 'error');
}

async function loadSuppliers() {
    const r = await fetchAPI(`${API}/suppliers`);
    if (r.success) {
        currentSuppliers = r.data;
        const t = $('#suppliers-tbody');
        t.innerHTML = r.data.map(s => `
            <tr>
                <td contenteditable="true" onblur="updateSupplierField(${num(s.id)},'name',this.textContent)" style="font-weight:600;">${escapeHtml(s.name)}</td>
                <td contenteditable="true" onblur="updateSupplierField(${num(s.id)},'contact_person',this.textContent)">${escapeHtml(s.contact_person || '-')}</td>
                <td contenteditable="true" onblur="updateSupplierField(${num(s.id)},'phone',this.textContent)">${escapeHtml(s.phone || '-')}</td>
                <td><button class="btn btn-danger btn-sm" onclick="deleteSupplier(${num(s.id)})">删除</button></td>
            </tr>
        `).join('') || '<tr><td colspan="4" style="text-align:center;padding:30px;">暂无供应商，在上方输入框添加</td></tr>';
    }
}

async function updateSupplierField(id, field, value) {
    const s = currentSuppliers.find(x => x.id === id);
    if (!s) return;
    const data = { name: s.name, contact_person: s.contact_person, phone: s.phone, email: s.email || '', address: s.address || '', notes: s.notes || '' };
    data[field] = value.trim();
    await fetchAPI(`${API}/suppliers/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

async function deleteSupplier(id) {
    if (!confirm('确定删除？')) return;
    await fetchAPI(`${API}/suppliers/${id}`, { method: 'DELETE' });
    loadSuppliers();
}

async function loadSuppliersForSelect() {
    const r = await fetchAPI(`${API}/suppliers`);
    if (r.success) {
        const sel = $('#product-supplier');
        if (sel) sel.innerHTML = '<option value="">请选择供应商</option>' + r.data.map(s => `<option value="${num(s.id)}">${escapeHtml(s.name)}</option>`).join('');
    }
}

// ==========================================
//  客户管理
// ==========================================
let currentCustomers = [];

async function quickAddCustomer() {
    const inp = $('#customer-quick-add');
    const name = inp.value.trim();
    if (!name) return;
    const r = await fetchAPI(`${API}/customers`, { method: 'POST', body: JSON.stringify({ name }) });
    if (r.success) { showToast('客户已添加', 'success'); inp.value = ''; loadCustomers(); }
    else showToast(r.error, 'error');
}

async function loadCustomers() {
    const r = await fetchAPI(`${API}/customers`);
    if (r.success) {
        currentCustomers = r.data;
        const t = $('#customers-tbody');
        t.innerHTML = r.data.map(c => `
            <tr>
                <td contenteditable="true" onblur="updateCustomerField(${num(c.id)},'name',this.textContent)" style="font-weight:600;">${escapeHtml(c.name)}</td>
                <td contenteditable="true" onblur="updateCustomerField(${num(c.id)},'contact_person',this.textContent)">${escapeHtml(c.contact_person || '-')}</td>
                <td contenteditable="true" onblur="updateCustomerField(${num(c.id)},'phone',this.textContent)">${escapeHtml(c.phone || '-')}</td>
                <td><button class="btn btn-danger btn-sm" onclick="deleteCustomer(${num(c.id)})">删除</button></td>
            </tr>
        `).join('') || '<tr><td colspan="4" style="text-align:center;padding:30px;">暂无客户，在上方输入框添加</td></tr>';
    }
}

async function updateCustomerField(id, field, value) {
    const c = currentCustomers.find(x => x.id === id);
    if (!c) return;
    const data = { name: c.name, contact_person: c.contact_person, phone: c.phone, email: c.email || '', address: c.address || '', notes: c.notes || '' };
    data[field] = value.trim();
    await fetchAPI(`${API}/customers/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

async function deleteCustomer(id) {
    if (!confirm('确定删除？')) return;
    await fetchAPI(`${API}/customers/${id}`, { method: 'DELETE' });
    loadCustomers();
}

// ==========================================
//  供应商/客户 Excel 导入
// ==========================================
// ==========================================
//  出库单
// ==========================================
var orderItems = [];

async function loadOrderPage() {
    var cr = await fetchAPI(API + '/customers');
    window._orderCustomers = cr.success ? cr.data : [];
    var pr = await fetchAPI(API + '/products');
    window._orderProducts = pr.success ? pr.data : [];
}

var _customerDropdownData = [];
var _productDropdownData = [];

function searchCustomer() {
    var kw = (document.getElementById('order-customer-input').value || '').trim().toLowerCase();
    var dd = document.getElementById('customer-dropdown');
    var list = (window._orderCustomers || []).filter(function(c) { return !kw || c.name.toLowerCase().indexOf(kw) >= 0; }).slice(0, 15);
    _customerDropdownData = list;
    if (list.length === 0) { dd.style.display = 'none'; return; }
    dd.style.display = 'block';
    dd.innerHTML = list.map(function(c, i) {
        return '<div style="padding:6px 10px;cursor:pointer;" data-idx="' + num(i) + '">' + escapeHtml(c.name) + '</div>';
    }).join('');
}

function searchProduct() {
    var kw = (document.getElementById('order-product-input').value || '').trim().toLowerCase();
    var dd = document.getElementById('product-dropdown');
    var list = (window._orderProducts || []).filter(function(p) { return !kw || p.name.toLowerCase().indexOf(kw) >= 0 || p.sku.toLowerCase().indexOf(kw) >= 0; }).slice(0, 20);
    _productDropdownData = list;
    if (list.length === 0) { dd.style.display = 'none'; return; }
    dd.style.display = 'block';
    dd.innerHTML = list.map(function(p, i) {
        var price = (p.sale_price > 0) ? ' ¥' + Number(p.sale_price).toLocaleString() : '';
        return '<div style="padding:6px 10px;cursor:pointer;" data-idx="' + num(i) + '">' + escapeHtml(p.name) + ' <span style="color:#94a3b8;">' + escapeHtml(p.sku) + '</span>' + price + '</div>';
    }).join('');
}

// 下拉点击事件委托（用 document 避免元素未渲染）
document.addEventListener('mousedown', function(e) {
    var div = e.target.closest('#customer-dropdown [data-idx]');
    if (div) {
        e.preventDefault();
        var c = _customerDropdownData[parseInt(div.getAttribute('data-idx'))];
        if (c) {
            document.getElementById('order-customer').value = c.id;
            document.getElementById('order-customer-input').value = c.name;
            document.getElementById('customer-dropdown').style.display = 'none';
        }
    }
    div = e.target.closest('#product-dropdown [data-idx]');
    if (div) {
        e.preventDefault();
        var p = _productDropdownData[parseInt(div.getAttribute('data-idx'))];
        if (p) {
            document.getElementById('order-product-select').value = p.id;
            document.getElementById('order-product-input').value = p.name;
            document.getElementById('order-product-price').value = p.sale_price || 0;
            document.getElementById('product-dropdown').style.display = 'none';
        }
    }
});

// 点击空白关闭下拉
document.addEventListener('click', function(e) {
    var cd = document.getElementById('customer-dropdown');
    var pd = document.getElementById('product-dropdown');
    if (cd && e.target !== document.getElementById('order-customer-input')) cd.style.display = 'none';
    if (pd && e.target !== document.getElementById('order-product-input')) pd.style.display = 'none';
});

function addOrderItem() {
    var pid = parseInt(document.getElementById('order-product-select').value);
    var qty = parseInt(document.getElementById('order-product-qty').value);
    var price = parseFloat(document.getElementById('order-product-price').value);
    if (!pid) { showToast('请选择商品', 'warning'); return; }
    if (isNaN(qty) || qty <= 0) { showToast('请输入大于 0 的出货数量', 'warning'); return; }
    var prod = window._orderProducts.find(function(p) { return p.id === pid; });
    if (!prod) return;
    // 售价默认用商品已有售价
    if (isNaN(price) || price <= 0) price = prod.sale_price || 0;
    var existing = orderItems.find(function(i) { return i.product_id === pid; });
    if (existing) { existing.quantity += qty; if (price > 0) existing.sale_price = price; }
    else {
        orderItems.push({
            product_id: pid,
            name: prod.name,
            sku: prod.sku,
            unit: prod.unit,
            specification: prod.specification || '',
            sale_price: price,
            quantity: qty,
        });
    }
    document.getElementById('order-product-input').value = '';
    document.getElementById('order-product-qty').value = '1';
    document.getElementById('order-product-price').value = '0';
    renderOrderItems();
}

function removeOrderItem(index) {
    orderItems.splice(index, 1);
    renderOrderItems();
}

function resetOrder() {
    orderItems = [];
    renderOrderItems();
}

function renderOrderItems() {
    var tbody = document.getElementById('order-items-tbody');
    var total = 0;
    if (orderItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan=\"6\" style=\"text-align:center;padding:30px;\">请先添加商品</td></tr>';
    } else {
        tbody.innerHTML = orderItems.map(function(item, i) {
            var sub = Number(item.sale_price || 0) * Number(item.quantity || 0);
            total += sub;
            return '<tr><td><strong>' + escapeHtml(item.name) + '</strong><br><small>' + escapeHtml(item.specification) + '</small></td>' +
                '<td>' + escapeHtml(item.sku) + '</td>' +
                '<td><input type=\"number\" value=\"' + num(item.quantity) + '\" min=\"1\" onchange=\"updateOrderQty(' + num(i) + ',this.value)\" style=\"width:60px;\"></td>' +
                '<td><input type=\"number\" value=\"' + num(item.sale_price) + '\" step=\"0.01\" min=\"0\" onchange=\"updateOrderPrice(' + num(i) + ',this.value)\" style=\"width:80px;\"></td>' +
                '<td>' + (sub > 0 ? '¥' + sub.toLocaleString() : '-') + '</td>' +
                '<td><button class=\"btn btn-danger btn-sm\" onclick=\"removeOrderItem(' + num(i) + ')\">✕</button></td></tr>';
        }).join('');
    }
    document.getElementById('order-total').textContent = '合计: ¥' + total.toLocaleString();
}

function updateOrderQty(index, val) {
    var qty = parseInt(val);
    if (isNaN(qty) || qty <= 0) { showToast('数量必须大于 0', 'warning'); renderOrderItems(); return; }
    orderItems[index].quantity = qty;
    renderOrderItems();
}

function updateOrderPrice(index, val) {
    var price = parseFloat(val) || 0;
    orderItems[index].sale_price = price;
    renderOrderItems();
}

async function submitAndDownload() {
    var cid = document.getElementById('order-customer').value;
    var custName = document.getElementById('order-customer-input').value.trim();
    var operator = document.getElementById('order-operator').value.trim();
    if (!cid) { showToast('请选择客户', 'warning'); return; }
    if (!operator) { showToast('请填写经办人', 'warning'); return; }
    if (orderItems.length === 0) { showToast('请添加商品', 'warning'); return; }

    // 原子批量出库：后端预检全部商品 → 单事务内逐项扣减，任一失败整体不执行。
    // 修复 H5（原逐项调 stock-out，中途失败会造成部分出库且提示「出库成功」）。
    var items = orderItems.map(function(item) {
        return { product_id: item.product_id, quantity: item.quantity, unit_price: item.sale_price || 0 };
    });
    var r = await fetchAPI(API + '/order/submit', {
        method: 'POST',
        body: JSON.stringify({ items: items, customer_id: parseInt(cid), operator: operator }),
    });
    if (!r.success) {
        showToast('出库失败，未扣减库存: ' + r.error, 'error');
        return;
    }
    showToast('出库成功', 'success');
    loadProducts(); loadInventory(); loadTransactions();

    // 生成下载文件（出库已原子完成）
    downloadOrder();
    // 清空
    resetOrder();
}

function downloadOrder() {
    var custName = document.getElementById('order-customer-input').value.trim();
    var operator = document.getElementById('order-operator').value.trim();

    var data = {
        items: orderItems,
        customer: custName,
        operator: operator,
        keeper: document.getElementById('order-keeper').value.trim(),
        warehouse: document.getElementById('order-warehouse').value.trim(),
    };

    fetch(API + '/order/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    }).then(function(resp) {
        if (!resp.ok) {
            // 服务端返回 JSON 错误时展示具体原因，而不是笼统的「导出失败」
            return resp.json().catch(function() { return null; })
                .then(function(j) { throw new Error((j && j.error) || '导出失败'); });
        }
        return resp.blob();
    }).then(function(blob) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = '出库单_' + (custName || 'unknown') + '_' + new Date().toISOString().substring(0,10) + '.xlsx';
        a.click();
    }).catch(function(e) {
        showToast('导出失败: ' + e.message, 'error');
    });
}

// ==========================================
//  入库单识别
// ==========================================
function initInboundOrder() {
    var zone = document.getElementById('inbound-upload-zone');
    if (!zone) return;
    zone.onclick = function() { document.getElementById('inbound-file-input').click(); };
    zone.ondragover = function(e) { e.preventDefault(); zone.classList.add('dragover'); };
    zone.ondragleave = function() { zone.classList.remove('dragover'); };
    zone.ondrop = function(e) { e.preventDefault(); zone.classList.remove('dragover');
        if (e.dataTransfer.files[0]) previewInboundImage(e.dataTransfer.files[0]); };
}

function previewInboundImage(file) {
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(e) {
        document.getElementById('inbound-preview-img').src = e.target.result;
        document.getElementById('inbound-preview').style.display = 'block';
        document.getElementById('inbound-upload-zone').style.display = 'none';
        document.getElementById('inbound-result').style.display = 'none';
        // 保存 base64 数据
        window._inboundImageBase64 = e.target.result.split(',')[1];
    };
    reader.readAsDataURL(file);
}

let _inboundRecognizing = false;

async function recognizeInboundOrder() {
    if (_inboundRecognizing) return;   // 防重复提交（AI 识别耗时长，双击会重复入库）
    if (!window._inboundImageBase64) { showToast('请先上传图片', 'warning'); return; }
    _inboundRecognizing = true;
    var status = document.getElementById('inbound-status');
    var btn = document.getElementById('inbound-recognize-btn');
    status.textContent = '🤖 AI 正在识别入库单...';
    status.style.color = '#3b82f6';
    if (btn) btn.disabled = true;

    try {
        var r = await fetchAPI(API + '/ai/inbound-recognize', {
            method: 'POST',
            body: JSON.stringify({ image: window._inboundImageBase64 }),
            timeout: 300000,
        });

        if (r.success) {
            status.textContent = '✅ 识别成功！已导入 ' + r.data.count + ' 条记录';
            status.style.color = '#10b981';
            // 显示结果
            var div = document.getElementById('inbound-result');
            div.style.display = 'block';
            var html = '<div class="card"><h4>📋 识别结果</h4><table style="font-size:13px;"><tr><th>商品</th><th>SKU</th><th>数量</th><th>单价</th></tr>';
            r.data.imported.forEach(function(i) {
                html += '<tr><td>' + escapeHtml(i.name) + '</td><td>' + escapeHtml(i.sku) + '</td><td>+' + num(i.quantity) + '</td><td>' + ((i.unit_price > 0) ? '¥' + num(i.unit_price) : '-') + '</td></tr>';
            });
            html += '</table></div>';
            div.innerHTML = html;
            loadProducts(); loadInventory();
        } else {
            status.textContent = '❌ ' + r.error;
            status.style.color = '#ef4444';
        }
    } finally {
        _inboundRecognizing = false;
        if (btn) btn.disabled = false;
    }
}

// ==========================================
//  导出库存单
// ==========================================
function exportInventory() {
    fetch(API + '/inventory').then(function(r) { return r.json(); }).then(function(data) {
        if (!data.success) { showToast('导出失败：无法获取库存数据', 'error'); return; }
        return fetch(API + '/order/export-inventory', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items: data.data }),
        });
    }).then(function(r) {
        if (!r) return;
        if (!r.ok) {
            return r.json().catch(function() { return null; })
                .then(function(j) { throw new Error((j && j.error) || ('HTTP ' + r.status)); });
        }
        return r.blob();
    }).then(function(b) {
        if (!b) return;
        var url = URL.createObjectURL(b);
        var a = document.createElement('a'); a.href = url;
        a.download = '库存清单_' + new Date().toISOString().substring(0,10) + '.xlsx'; a.click();
        setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
    }).catch(function(e) {
        showToast('导出失败: ' + e.message, 'error');
    });
}

async function uploadSupplierExcel(file) {
    const fd = new FormData(); fd.append('file', file);
    try {
        const resp = await fetch(`${API}/upload/suppliers`, { method: 'POST', body: fd });
        const r = await resp.json();
        if (r.success) { showToast(`成功导入 ${r.data.rows_imported} 个供应商`, 'success'); loadSuppliers(); }
        else showToast('导入失败: ' + r.error, 'error');
    } catch(e) { showToast('网络错误', 'error'); }
}

// ==========================================
//  操作日志
// ==========================================
async function loadAuditLog(table) {
    table = table || '';
    const r = await fetchAPI(API + '/audit-log?limit=200' + (table ? '&table=' + table : ''));
    if (r.success) {
        var labels = { create: '创建', update: '更新', delete: '删除', stock_in: '入库', stock_out: '出库' };
        var t = document.getElementById('audit-log-tbody');
        var html = '';
        for (var i = 0; i < r.data.length; i++) {
            var l = r.data[i];
            var badge = 'badge-warning';
            if (l.action.indexOf('delete') >= 0) badge = 'badge-danger';
            else if (l.action.indexOf('create') >= 0 || l.action.indexOf('stock_in') >= 0) badge = 'badge-success';
            var oldStr = (l.old_data || '-');
            if (oldStr.length > 80) oldStr = oldStr.substring(0, 80);
            var newStr = (l.new_data || '-');
            if (newStr.length > 80) newStr = newStr.substring(0, 80);
            html += '<tr>' +
                '<td>' + formatDate(l.created_at) + '</td>' +
                '<td><span class="badge ' + badge + '">' + escapeHtml(labels[l.action] || l.action) + '</span></td>' +
                '<td>' + escapeHtml(l.table_name) + '</td>' +
                '<td>' + escapeHtml(l.record_id || '-') + '</td>' +
                '<td>' + escapeHtml(oldStr) + '</td>' +
                '<td>' + escapeHtml(newStr) + '</td>' +
                '<td>' + escapeHtml(l.operator || '-') + '</td>' +
                '</tr>';
        }
        t.innerHTML = html || '<tr><td colspan="7" style="text-align:center;padding:30px;">暂无日志</td></tr>';
    } else {
        showToast('日志加载失败: ' + r.error, 'error');
    }
}

async function uploadCustomerExcel(file) {
    const fd = new FormData(); fd.append('file', file);
    try {
        const resp = await fetch(`${API}/upload/customers`, { method: 'POST', body: fd });
        const r = await resp.json();
        if (r.success) { showToast(`成功导入 ${r.data.rows_imported} 个客户`, 'success'); loadCustomers(); }
        else showToast('导入失败: ' + r.error, 'error');
    } catch(e) { showToast('网络错误', 'error'); }
}

// ==========================================
//  账户管理
// ==========================================
// 页面加载时探测当前账户：管理员才显示"账户管理"入口
async function loadAuthInfo() {
    // 首登时 Basic Auth 凭据可能尚未被浏览器缓存（首个 /api 请求尚未完成 401→凭据 往返），
    // 这里最多重试 3 次，间隔 400ms，确保能拿到真实角色。
    for (let attempt = 0; attempt < 3; attempt++) {
        const r = await fetchAPI(`${API}/auth/me`);
        if (r.success && r.data) {
            const info = r.data;
            const navAccounts = $('#nav-accounts');
            if (navAccounts) navAccounts.style.display = info.is_admin ? '' : 'none';
            const el = $('#accounts-current-user');
            if (el) el.textContent = '当前登录：' + info.username + (info.is_admin ? '（管理员）' : '（普通用户）');
            return;
        }
        await new Promise(res => setTimeout(res, 400));
    }
}

async function loadAccounts() {
    const r = await fetchAPI(`${API}/users`);
    if (!r.success) {
        showToast('加载账户失败: ' + r.error, 'error');
        const t = $('#users-tbody');
        if (t) t.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:30px;color:#c00;">' + escapeHtml(r.error) + '</td></tr>';
        return;
    }
    const t = $('#users-tbody');
    if (!r.data || r.data.length === 0) {
        t.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:30px;color:#94a3b8;">暂无账户</td></tr>';
        return;
    }
    t.innerHTML = r.data.map(u => `
        <tr>
            <td>${num(u.id)}</td>
            <td><strong>${escapeHtml(u.username)}</strong></td>
            <td><span class="badge ${u.role === 'admin' ? 'badge-success' : 'badge-warning'}">${u.role === 'admin' ? '管理员' : '普通用户'}</span></td>
            <td>${formatDate(u.created_at)}</td>
            <td><button class="btn btn-danger btn-sm" onclick="deleteUser('${num(u.id)}')">删除</button></td>
        </tr>
    `).join('');
}

async function createUser() {
    const username = $('#user-username').value.trim();
    const password = $('#user-password').value;
    const role = $('#user-role').value;
    if (!username) { showToast('请输入用户名', 'warning'); return; }
    if (!password || password.length < 6) { showToast('密码至少 6 位', 'warning'); return; }
    const r = await fetchAPI(`${API}/users`, {
        method: 'POST',
        body: JSON.stringify({ username, password, role }),
    });
    if (r.success) {
        showToast('账户创建成功', 'success');
        $('#user-username').value = '';
        $('#user-password').value = '';
        loadAccounts();
    } else {
        showToast('创建失败: ' + r.error, 'error');
    }
}

async function deleteUser(uid) {
    if (!confirm('确定删除该账户吗？此操作不可恢复。')) return;
    const r = await fetchAPI(`${API}/users/${uid}`, { method: 'DELETE' });
    if (r.success) {
        showToast('账户已删除', 'success');
        loadAccounts();
    } else {
        showToast('删除失败: ' + r.error, 'error');
    }
}
