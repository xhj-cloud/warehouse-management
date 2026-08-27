# 仓库管理系统 - 安全修复记录

**时间**：2026-08-25 21:17（北京时间）
**范围**：开源代码去默认密码 + 线上实例凭据轮换 + 内网 IP 泄露清理

---

## 一、背景与风险

仓库已公开（GitHub public，CC BY-NC 4.0 许可证），但代码与部署文件中内置了固定默认凭据：

| 暴露项 | 位置 | 风险 |
|--------|------|------|
| `admin/admin123` 管理员账户 | models.py 首次启动自动创建、README、systemd/docker 示例 | 任何能访问实例的人可直接以管理员登录 |
| `warehouse/warehouse123` 数据库账号 | config.py 默认值、.env.example、docker-compose.yml、deploy.sh、README SQL 示例 | 数据库凭据可被直接利用（若端口暴露） |
| MySQL root 密码 `root123` | docker-compose.yml | 同上 |
| 内网 IP `100.101.x.x` | ai_service.py、app.py、templates/index.html、README、测试文档 | 泄露内部网络拓扑 |

**线上实例核查结果（修复前）**：admin 登录密码仍为默认值（经 werkzeug 哈希比对确认）；应用端口 5050 绑定 0.0.0.0。即公开仓库中的凭据与线上实例完全一致，属高危状态。

## 二、代码改动（10 个文件）

### 核心安全逻辑
- **`models.py`**：重写 `seed_admin_if_empty()`——不再内置固定默认密码。users 表为空时：设置了环境变量 `AUTH_PASSWORD` 则用它作为 admin 初始密码；未设置则自动生成一次性随机强密码并打印到启动日志（仅此一次显示）。幂等性与无库静默跳过行为保持不变。
- **`config.py`**：移除 `DB_PASSWORD` 硬编码默认值（改为必填）；补上 `load_dotenv()`——python-dotenv 此前在依赖中但从未被调用，`.env` 实际不生效，现已真正加载（已存在的环境变量优先级高于 .env）。

### 部署文件（密码一律由部署者自行设定）
- **`.env.example`**：DB_PASSWORD 留空必填；新增 AUTH_USER/AUTH_PASSWORD 配置段及说明。
- **`docker-compose.yml`**：删除全部硬编码密码，改用 `${VAR:?报错提示}` 必填语法——未填 .env 时 compose 直接拒绝启动并给出明确指引；透传 AUTH_USER/AUTH_PASSWORD。
- **`deploy.sh`**：MySQL 密码、管理员初始密码改为必填（空则循环重输）；确认摘要中密码打码不回显；生成的 .env 写入 AUTH 配置并 chmod 600；systemd 单元加入 AUTH_USER/AUTH_PASSWORD；删除修改 config.py 的 sed 段。
- **`README.md`**：所有默认密码替换为「你的数据库密码 / 你的管理员初始密码」占位符；首次启动说明重写为新机制。

### 内网 IP 泄露清理（5 处）
- `ai_service.py`：健康检查报错改为动态显示实际配置的 base_url
- `app.py`：启动横幅 AI 服务地址改为动态输出配置值
- `templates/index.html`：侧边栏硬编码服务器 IP 改为 JS 动态显示当前访问的 hostname
- `tests/test_integration.py`、README 文档注释：IP 替换为占位符

## 三、线上实例凭据轮换（100.101.x.x:5050）

| 项目 | 操作 | 结果 |
|------|------|------|
| admin 登录密码 | users 表哈希更新为强随机密码（新值经安全渠道交付，**不入库**） | ✅ 旧密码 `admin123` 登录返回 401；新密码登录成功 |
| MySQL warehouse 用户 | `ALTER USER CURRENT_USER()` 轮换为强随机密码 | ✅ 应用以新密码连接数据库正常 |
| systemd 单元 | AUTH_PASSWORD / DB_PASSWORD 同步更新（原文件已备份） | ✅ daemon-reload + restart 后服务 active |

> 说明：环境变量超级管理员兜底（AUTH_USER/AUTH_PASSWORD）与 users 表哈希使用同一组新凭据，两条登录路径均已脱离公开默认值。

## 四、验证结果

| 项目 | 结果 |
|------|------|
| 本地全量测试 `pytest tests/ -q` | ✅ 150 passed, 10 skipped（集成测试需真实 MySQL，自动跳过） |
| Python 语法 / deploy.sh bash -n | ✅ 通过 |
| 跟踪文件残留扫描（admin123/warehouse123/root123/内网 IP） | ✅ 无残留（仅 tests/test_api.py 中 monkeypatch 测试夹具字符串，非真实默认值） |
| 线上：旧密码登录 | ✅ 401 拒绝 |
| 线上：新密码登录 + /api/dashboard | ✅ success:true / 200 |
| 服务状态 | ✅ active（debug=True 按用户要求保持） |

## 五、给部署者的安全建议

1. **首次启动**：优先通过 `.env` 或 systemd `Environment=` 显式设定 `AUTH_PASSWORD`；若留空，请妥善保存启动日志中一次性打印的随机初始密码，并登录后立即修改。
2. **数据库**：`DB_PASSWORD` 必填，请使用强密码；MySQL 仅监听 localhost（默认即如此），不要对公网开放 3306。
3. **应用端口**：5050 绑定 0.0.0.0 以便内网访问，请确保防火墙/网络边界只允许可信网段到达该端口。
4. **SECRET_KEY**：生产部署务必替换为随机长字符串（影响 Session Cookie 签名）。
