# 仓库管理系统

基于 Flask + MySQL + LM Studio AI 的仓库库存管理系统，支持网页管理、Excel 导入、AI 智能分析、多账户登录。

> 最近更新：新增**登录认证（Session 7 天免登录）**、**账户管理（多用户/管理员）**、修复 5 个高危安全漏洞（详见文末「安全修复记录」）。

## 功能

- **登录认证**：所有页面需登录，Session 保持 7 天，刷新/重开浏览器免登录；退出登录一键生效
- **账户管理**（仅管理员）：创建/删除用户，角色分管理员/普通用户，密码加盐哈希存储
- **仪表盘**：库存概览、低库存预警、分类分布柱状图
- **商品管理**：CRUD、搜索、供应商关联、进货价/售价、快速出入库
- **库存管理**：库存明细、库位、最近进价、平均进价、批次明细、导出库存单
- **分类管理**：快速添加、内联编辑、点击即改
- **供应商管理**：快速添加、内联编辑、Excel 批量导入
- **客户管理**：快速添加、内联编辑、Excel 批量导入
- **出入库记录**：完整流水、客户关联、单价/金额、供应商、**操作者=登录账号**
- **出库单**：搜索选择商品和客户、填写数量售价、原子批量出库（单事务，失败整体回滚）并下载 Excel
- **批次明细**：点击批次按钮查看每批进货的时间/数量/单价/金额/供应商
- **Excel 导入**：商品/供应商/客户三个工作表，增量/覆盖两种模式，SKU 和库位自动生成，行级错误部分导入
- **AI 对话**：自然语言操作库存、创建供应商/客户、创建出库单（含下载链接）
- **AI 分析**：综合分析、低库存预警、补货建议、趋势分析
- **操作日志**：所有增删改操作可追溯、可按类型筛选

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python Flask |
| 数据库 | MySQL 5.7+ / 8.0 |
| 前端 | 原生 HTML/CSS/JS（SPA，13 个功能页） |
| AI | LM Studio（OpenAI 兼容 API）+ qwen3.6-35b-a3b |
| 认证 | Session + Cookie（HttpOnly，7 天有效）；密码 werkzeug scrypt 加盐哈希 |

## 项目结构

```
warehouse-management/
├── app.py                  # Flask 主应用（所有 API 端点 + 认证 + 账户管理）
├── config.py               # 配置文件（数据库/AI/上传/认证环境变量）
├── models.py               # 数据模型（10 个模型类，含 UserModel）
├── ai_service.py           # AI 分析服务（LM Studio 集成）
├── init_db.sql             # 数据库初始化脚本（含幂等迁移、users 表）
├── requirements.txt        # Python 依赖
├── deploy.sh               # 交互式一键部署脚本
├── Dockerfile / docker-compose.yml
├── generate_template.py    # Excel 三合一模板生成
├── README.md               # 项目说明
├── README_修复记录_*.md    # 高危漏洞修复与功能迭代的专项记录
├── templates/
│   ├── index.html          # 前端 SPA 页面
│   └── login.html          # 登录页
├── static/
│   ├── css/style.css       # 样式
│   └── js/app.js           # 前端逻辑
└── uploads/                # 上传文件目录
```

## 登录与账户管理

### 首次启动

- 数据库 `users` 表为空时，应用启动自动创建管理员账户 **`admin`**（不内置任何固定默认密码）：
  - 设置了环境变量 `AUTH_PASSWORD` → 用它作为初始密码；
  - 未设置 → 自动生成一次性随机强密码并打印到启动日志（仅此一次显示，请妥善保存）。
- 登录后建议立即在「账户管理」页修改密码或创建自己的账户

### 角色区别

| 能力 | 管理员 (admin) | 普通用户 (user) |
|------|:---:|:---:|
| 全部业务功能（商品/库存/出入库/导入导出/AI） | ✅ | ✅ |
| 账户管理（查看/创建/删除用户） | ✅ | ❌（403 拒绝） |

### 认证机制

- 登录后签发 **7 天有效 Session Cookie**（HttpOnly + SameSite=Lax），刷新页面/重开浏览器无需重复登录
- 退出登录（顶部栏按钮）立即清除 Session
- 出入库记录、操作日志的操作者 = **当前登录账号**（后端强制取登录态，防伪造）
- 环境变量 `AUTH_USER` / `AUTH_PASSWORD` 可作为超级管理员兜底（systemd 服务中配置）

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

部署脚本会交互式询问 MySQL 密码（必填）、管理员初始密码（必填）、AI 地址、端口等，确认后自动完成全部配置。

**方式二：手动部署**

```bash
# 1. 安装依赖
sudo apt update && sudo apt install -y python3 python3-pip python3-venv mysql-server nginx

# 2. 创建数据库
sudo mysql -e "CREATE DATABASE warehouse_db CHARACTER SET utf8mb4;
  CREATE USER 'warehouse'@'localhost' IDENTIFIED BY 'your_password';
  GRANT ALL ON warehouse_db.* TO 'warehouse'@'localhost'; FLUSH PRIVILEGES;"

# 3. 初始化表（含 users 表，幂等可重复执行）
mysql -u warehouse -p warehouse_db < init_db.sql

# 4. 安装 Python 依赖
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 5. 配置环境变量：复制示例并填写自己的密码（DB_PASSWORD / AUTH_PASSWORD 必填）
cp .env.example .env && vim .env

# 6. 启动（首次启动 users 表为空时自动创建 admin；初始密码来自 AUTH_PASSWORD，未设置则打印随机密码到日志）
python app.py
# 访问 http://服务器IP:5050 → 跳转登录页
```

**配置开机自启（systemd）：**

```bash
sudo tee /etc/systemd/system/warehouse.service > /dev/null << EOF
[Unit]
Description=Warehouse Management System
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment="DB_HOST=localhost"
Environment="DB_USER=warehouse"
Environment="DB_PASSWORD=你的数据库密码"
Environment="AUTH_USER=admin"
Environment="AUTH_PASSWORD=你的管理员初始密码"
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
  CREATE USER 'warehouse'@'localhost' IDENTIFIED BY '<你的数据库密码>';
  GRANT ALL ON warehouse_db.* TO 'warehouse'@'localhost'; FLUSH PRIVILEGES;"

# 5. 安装运行
cd warehouse-management
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
mysql -u warehouse -p<你的数据库密码> warehouse_db < init_db.sql
python app.py

# 访问 http://localhost:5050 → 跳转登录页（admin 初始密码见启动日志或 AUTH_PASSWORD）
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
  CREATE USER 'warehouse'@'localhost' IDENTIFIED BY '<你的数据库密码>';
  GRANT ALL ON warehouse_db.* TO 'warehouse'@'localhost'; FLUSH PRIVILEGES;"

mysql -u warehouse -p<你的数据库密码> warehouse_db < init_db.sql

# 启动
python app.py
# 访问 http://localhost:5050 → 跳转登录页（admin 初始密码见启动日志或 AUTH_PASSWORD）
```

---

### Docker 部署

```bash
# 使用 docker-compose（先 cp .env.example .env 并填写 DB_PASSWORD / MYSQL_ROOT_PASSWORD / AUTH_PASSWORD）
docker compose up -d

# 或手动构建
docker build -t warehouse .
docker run -d -p 5050:5050 \
  -e DB_HOST=mysql_host \
  -e DB_PASSWORD=你的数据库密码 \
  -e LM_STUDIO_URL=http://your-lmstudio:1234/v1 \
  -e AUTH_USER=admin \
  -e AUTH_PASSWORD=你的管理员初始密码 \
  warehouse
```

### AI 配置（可选）

AI 功能依赖 LM Studio，不配置也不影响基础仓库管理功能。

```bash
# 1. 在 AI 服务器上下载 LM Studio（lmstudio.ai）
# 2. 下载 Qwen3.8-27B（qwen3.8-27b-q8_0.gguf）或其他 OpenAI 兼容模型
# 3. 启动 LM Studio 的本地 API 服务（默认端口 1234）
# 4. 在 config.py 或 .env 中配置 LM_STUDIO_URL
```

## 数据库表

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| users | 系统用户（账户管理） | username(唯一), password_hash(scrypt), role(admin/user) |
| categories | 商品分类 | name, description |
| products | 商品 | sku, unit_price, sale_price, supplier_id |
| inventory | 库存 | quantity, location, min/max_stock |
| transactions | 出入库记录 | type, quantity, unit_price, supplier_id, customer_id, **operator(登录账号)** |
| suppliers | 供应商 | name, contact_person, phone |
| customers | 客户 | name, contact_person, phone |
| excel_uploads | 上传记录 | filename, status(pending/processing/success/failed/**partial**) |
| audit_log | 审计日志 | action, table_name, old_data, new_data, operator |

## 服务管理

```bash
sudo systemctl status warehouse
sudo systemctl restart warehouse
sudo journalctl -u warehouse -f
```

## 测试

测试分三层：

| 文件 | 内容 | 依赖 |
|------|------|------|
| `tests/test_models.py` | 库存增减、超卖拦截、审计序列化等模型逻辑 | 无（mock） |
| `tests/test_api.py` | Flask API 端点（CRUD/出入库/Excel 导入导出/AI 对话/账户管理/登录验证） | 无（test_client + mock） |
| `tests/test_ai_service.py` | action 指令解析、AI 输出 JSON 清洗、OCR 流程、健康检查 | 无（mock HTTP） |
| `tests/test_integration.py` | 真实 MySQL：建库脚本、批次价格链、低库存预警、Excel 导入端到端 | MySQL（不可达时自动跳过） |

```bash
# 安装测试依赖
pip install -r requirements.txt -r requirements-dev.txt

# 本地运行（无数据库也能跑，集成测试自动跳过；本地测试自动关闭认证）
pytest tests/ -v

# 指向服务器数据库跑集成测试（需 CREATE/DROP DATABASE 权限；
# 会自动创建 warehouse_test_<pid> 独立库并在结束后删除，不触碰业务数据）
DB_HOST=<你的数据库服务器IP> pytest tests/test_integration.py -v
```

## 安全修复记录

最近一轮修复了 5 个高危漏洞并新增多账户认证，详细记录见 `README_修复记录_20260820_011221.md`。摘要：

| 漏洞 | 修复 |
|------|------|
| 存储型 XSS（前端零转义） | 新增 `escapeHtml()` 全量转义；内联事件只传数字 id、名称内存查找 |
| AI 分析页未转义 | 复用安全渲染 `renderChatReply` |
| "设为绝对值"并发竞态 | `SELECT ... FOR UPDATE` 行锁，杜绝丢失更新 |
| 全 API 无认证 | 登录认证 + 账户管理（多用户/RBAC） |
| 手工出库单无事务 | 新增 `/api/order/submit` 批量原子出库，失败整体回滚 |

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
出库为**原子批量操作**：预检全部商品库存 → 单事务内逐项扣减，任一商品失败则整体不执行（不产生半截出库）。

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

- SKU 和库位不填自动生成；增量模式（数量累加）与覆盖模式（数量取文件值）可选
- 有行级错误的文件以 `partial` 状态入库并在前端黄色提示，不影响正确行

## 进价与批次

- 库存表「最近进价」：最近一次入库的实际单价
- 库存表「平均进价」：所有入库批次单价的算术平均
- 点击「批次」按钮查看每次进货的完整明细
- 手动入库、Excel 导入、AI 入库的批次记录统一可追溯（每单独立批次号）

## License / 许可证

本项目采用 [CC BY-NC 4.0](LICENSE)（Creative Commons 署名-非商业性使用 4.0 国际）协议开源：可以自由查看、使用、修改和分发，但**禁止任何商业用途**。
