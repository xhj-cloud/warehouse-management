# 仓库管理系统

基于 Flask + MySQL + LM Studio AI 的仓库库存管理系统，支持网页管理、Excel 导入、AI 智能分析。

## 功能

- **仪表盘**：库存概览、低库存预警、分类分布柱状图
- **商品管理**：CRUD、搜索、供应商关联、进货价/售价、快速出入库
- **库存管理**：库存明细、库位、最近进价、平均进价、批次明细、导出库存单
- **分类管理**：快速添加、内联编辑、点击即改
- **供应商管理**：快速添加、内联编辑、Excel 批量导入
- **客户管理**：快速添加、内联编辑、Excel 批量导入
- **出入库记录**：完整流水、客户关联、单价/金额、供应商
- **出库单**：搜索选择商品和客户、填写数量售价、一键提交出库并下载 Excel
- **批次明细**：点击批次按钮查看每批进货的时间/数量/单价/金额/供应商
- **Excel 导入**：商品/供应商/客户三个工作表，增量模式，SKU 和库位自动生成
- **AI 对话**：自然语言操作库存、创建供应商/客户、创建出库单（含下载链接）
- **AI 分析**：综合分析、低库存预警、补货建议、趋势分析
- **操作日志**：所有增删改操作可追溯、可按类型筛选

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python Flask |
| 数据库 | MySQL 8.0 |
| 前端 | 原生 HTML/CSS/JS（SPA，10 个功能页） |
| AI | LM Studio（OpenAI 兼容 API）+ qwen3.6-35b-a3b |

## 项目结构

```
warehouse-management/
├── app.py                  # Flask 主应用（所有 API 端点）
├── config.py               # 配置文件（数据库/AI/上传）
├── models.py               # 数据模型（9 个模型类）
├── ai_service.py           # AI 分析服务（LM Studio 集成）
├── init_db.sql             # 数据库初始化脚本（含迁移）
├── requirements.txt        # Python 依赖
├── deploy.sh               # 交互式一键部署脚本
├── Dockerfile / docker-compose.yml
├── generate_template.py    # Excel 三合一模板生成
├── README.md               # 项目说明
├── templates/
│   └── index.html          # 前端 SPA 页面
├── static/
│   ├── css/style.css       # 样式
│   └── js/app.js           # 前端逻辑
└── uploads/                # 上传文件目录
```

## 快速部署

```bash
# 1. 上传项目到服务器
scp -r warehouse-management/ user@server:~/桌面/

# 2. 运行部署脚本
cd ~/桌面/warehouse-management
chmod +x deploy.sh
./deploy.sh
```

## 数据库表

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| categories | 商品分类 | name, description |
| products | 商品 | sku, unit_price, sale_price, supplier_id |
| inventory | 库存 | quantity, location, min/max_stock |
| transactions | 出入库记录 | type, quantity, unit_price, supplier_id, customer_id |
| suppliers | 供应商 | name, contact_person, phone |
| customers | 客户 | name, contact_person, phone |
| excel_uploads | 上传记录 | filename, status |
| audit_log | 审计日志 | action, table_name, old_data, new_data |

## 服务管理

```bash
sudo systemctl status warehouse
sudo systemctl restart warehouse
sudo journalctl -u warehouse -f
```

## AI 能做什么

| 场景 | 对话示例 |
|------|----------|
| 入库 | 「入库 50 盒 M6 螺丝，从华强五金买的，单价 0.05」 |
| 出库 | 「出库 10 个 LED 灯珠给中建公司，售价 0.5」 |
| 调库存 | 「把电阻调成 500 个」 |
| 智能导入 | 「今天从淘宝买了 30 包 A4 纸和 100 个电容」 |
| 供应商 | 「新增供应商华为，联系人张经理」 |
| 客户 | 「加一个中建公司客户」 |
| 出库单 | 「创建出库单给中建公司，经办人张三，出库 2 个 LED 灯珠单价 0.5」 |
| 分析 | 「哪些商品快缺货了」「库存有什么风险」 |

## 出库单

出库单页面支持搜索选择商品和客户，填写数量和售价后一键提交出库并下载 Excel 出库单。

Excel 出库单格式：
```
出 库 单
日期: 2026-07-13  客户: 中建公司
序号  商品名称  规格  SKU  数量  单价  金额
  1   螺丝 M6   不锈钢 ...   2   ¥10  ¥20
  2   打印纸 A4  ...   ...   3   ¥15  ¥45
                      合计: ¥65
经办人: 张三    库管: 李四
仓库编号: WH-A01
```

## Excel 模板

运行 `python generate_template.py` 生成模板，三个工作表：

| 工作表 | 必填列 | 可选列 |
|--------|--------|--------|
| 商品导入 | 商品名称 | SKU、分类、供应商、单位、规格、数量、进货价、售价、库位等 |
| 供应商导入 | 名称 | 联系人、电话、邮箱、地址、备注 |
| 客户导入 | 名称 | 联系人、电话、邮箱、地址、备注 |

- SKU 和库位不填自动生成，默认增量模式

## 进价与批次

- 库存表「最近进价」：最近一次入库的实际单价
- 库存表「平均进价」：所有入库批次单价的算术平均
- 点击「批次」按钮查看每次进货的完整明细
- 手动入库和 AI 入库的批次记录统一可追溯
