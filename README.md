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

## 部署指南

### 环境要求

| 依赖 | 版本 |
|------|------|
| Python | 3.8+ |
| MySQL | 5.7+ 或 8.0 |
| pip | 最新版 |

可选：Nginx（反向代理）、LM Studio（AI 功能）

---

### Ubuntu / Debian 服务器部署

**方式一：一键部署脚本**

```bash
# 1. 上传项目到服务器
scp -r warehouse-management/ user@server:~/桌面/

# 2. 运行部署脚本
cd ~/桌面/warehouse-management
chmod +x deploy.sh
./deploy.sh
```

部署脚本会交互式询问 MySQL 密码、AI 地址、端口等，确认后自动完成全部配置。

**方式二：手动部署**

```bash
# 1. 安装依赖
sudo apt update && sudo apt install -y python3 python3-pip python3-venv mysql-server nginx

# 2. 创建数据库
sudo mysql -e "CREATE DATABASE warehouse_db CHARACTER SET utf8mb4;
  CREATE USER 'warehouse'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL ON warehouse_db.* TO 'warehouse'@'localhost'; FLUSH PRIVILEGES;"

# 3. 初始化表
mysql -u warehouse -p warehouse_db < init_db.sql

# 4. 安装 Python 依赖
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 5. 配置环境变量（修改 config.py 或创建 .env）
# DB_HOST / DB_PASSWORD / LM_STUDIO_URL 等

# 6. 启动
python app.py
# 访问 http://服务器IP:5050
```

**配置开机自启：**

```bash
sudo tee /etc/systemd/system/warehouse.service > /dev/null << EOF
[Unit]
Description=Warehouse Management System
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python $(pwd)/app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now warehouse
```

---

### macOS 部署

```bash
# 1. 安装 Homebrew（如未安装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. 安装依赖
brew install python@3 mysql

# 3. 启动 MySQL
brew services start mysql

# 4. 创建数据库
mysql -u root -e "CREATE DATABASE warehouse_db CHARACTER SET utf8mb4;
  CREATE USER 'warehouse'@'localhost' IDENTIFIED BY 'warehouse123';
  GRANT ALL ON warehouse_db.* TO 'warehouse'@'localhost'; FLUSH PRIVILEGES;"

# 5. 安装运行
cd warehouse-management
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
mysql -u warehouse -pwarehouse123 warehouse_db < init_db.sql
python app.py

# 访问 http://localhost:5050
```

---

### Windows 部署

**方式一：WSL2（推荐）**

```powershell
# 在 WSL2 Ubuntu 中按 Ubuntu 部署流程操作
wsl --install -d Ubuntu
```

**方式二：原生 Windows**

```powershell
# 1. 安装 Python 3.8+（python.org 下载）
# 2. 安装 MySQL 8.0（dev.mysql.com 下载）
# 3. 打开 PowerShell

cd warehouse-management
python -m venv venv
.\venv\Scripts\activate

# 安装依赖（pymysql 需要编译工具）
pip install -r requirements.txt

# 如果 pymysql 安装失败，安装预编译版：
# pip install pymysql --only-binary=:all:

# 创建数据库
mysql -u root -e "CREATE DATABASE warehouse_db CHARACTER SET utf8mb4;
  CREATE USER 'warehouse'@'localhost' IDENTIFIED BY 'warehouse123';
  GRANT ALL ON warehouse_db.* TO 'warehouse'@'localhost'; FLUSH PRIVILEGES;"

mysql -u warehouse -pwarehouse123 warehouse_db < init_db.sql

# 启动
python app.py
# 访问 http://localhost:5050
```

---

### Docker 部署

```bash
# 使用 docker-compose
docker compose up -d

# 或手动构建
docker build -t warehouse .
docker run -d -p 5050:5050 \
  -e DB_HOST=mysql_host \
  -e DB_PASSWORD=your_password \
  -e LM_STUDIO_URL=http://your-lmstudio:1234/v1 \
  warehouse
```

### AI 配置（可选）

AI 功能依赖 LM Studio，不配置也不影响基础仓库管理功能。

```bash
# 1. 在 AI 服务器上下载 LM Studio（lmstudio.ai）
# 2. 下载模型 qwen3.6-35b-a3b 或其他兼容模型
# 3. 启动 LM Studio 的本地 API 服务（默认端口 1234）
# 4. 在 config.py 或 .env 中配置 LM_STUDIO_URL
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
