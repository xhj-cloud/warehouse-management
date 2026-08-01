#!/bin/bash
# ============================================
#  仓库管理系统 - 通用部署脚本 v2
#  适用于 Ubuntu 20.04+
# ============================================

set -o pipefail

# ---- 颜色 ----
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; N='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---- 通用函数 ----
step()    { echo -e "${Y}[$1]${N} $2"; }
ok()      { echo -e "  ${G}✅${N} $1"; }
warn()    { echo -e "  ${R}⚠️${N}  $1"; }
die()     { echo -e "${R}❌ $1${N}"; exit 1; }
is_yes()  { [[ "$1" =~ ^[Yy] ]]; }

# ---- 检测环境 ----
detect_env() {
    DETECTED_USER="${SUDO_USER:-$USER}"
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    DETECTED_PYTHON=$(python3 --version 2>/dev/null || echo "未安装")

    # 桌面路径
    DETECTED_DESKTOP="$HOME/桌面"
    [ -d "$DETECTED_DESKTOP" ] || DETECTED_DESKTOP="$HOME/Desktop"
    # sudo 情况下用原始用户的桌面
    if [ -n "$SUDO_USER" ]; then
        DETECTED_DESKTOP="/home/$SUDO_USER/桌面"
        [ -d "$DETECTED_DESKTOP" ] || DETECTED_DESKTOP="/home/$SUDO_USER/Desktop"
        [ -d "$DETECTED_DESKTOP" ] || DETECTED_DESKTOP="$HOME"
    fi
    DETECTED_PROJECT="$DETECTED_DESKTOP/warehouse-management"

    # 检测 MySQL
    DETECTED_MYSQL="sudo mysql"
    mysql -u root -e "SELECT 1" 2>/dev/null && DETECTED_MYSQL="mysql -u root" || true
    sudo mysql -e "SELECT 1" 2>/dev/null && DETECTED_MYSQL="sudo mysql" || true

    # 检测 Python 版本
    PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info[0])" 2>/dev/null || echo 0)
    PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info[1])" 2>/dev/null || echo 0)
}

# ---- 询问配置 ----
ask_config() {
    clear
    echo -e "${C}"
    echo "╔══════════════════════════════════════════╗"
    echo "║      📦 仓库管理系统 - 通用部署工具      ║"
    echo "╚══════════════════════════════════════════╝"
    echo -e "${N}"

    echo -e "${G}📍 检测到当前环境:${N}"
    echo "   用户:     $DETECTED_USER"
    echo "   服务器IP: ${DETECTED_IP:-未知}"
    echo "   Python:   $DETECTED_PYTHON"
    echo "   MySQL:    $($DETECTED_MYSQL -e "SELECT VERSION()" 2>/dev/null || echo "未检测到")"
    echo ""

    echo -e "${Y}🔧 请配置以下信息（回车使用默认值）:${N}"
    echo ""

    read -p "1. 项目安装路径 [${DETECTED_PROJECT}]: " v; PROJECT_DIR="${v:-$DETECTED_PROJECT}"
    read -p "2. MySQL 密码 [warehouse123]: " v; MYSQL_PASSWORD="${v:-warehouse123}"
    read -p "3. 数据库用户 [warehouse]: " v; MYSQL_USER="${v:-warehouse}"
    read -p "4. 数据库名 [warehouse_db]: " v; MYSQL_DB="${v:-warehouse_db}"
    read -p "5. AI 地址 [http://127.0.0.1:1234/v1]: " v; AI_URL="${v:-http://127.0.0.1:1234/v1}"
    read -p "6. AI 模型 [qwen3.6-35b-a3b]: " v; AI_MODEL="${v:-qwen3.6-35b-a3b}"
    read -p "7. 服务端口 [5050]: " v; SERVICE_PORT="${v:-5050}"
    read -p "8. 安装 Nginx? [Y/n]: " v; INSTALL_NGINX="${v:-y}"
    read -p "9. 全新安装(会装 MySQL/Nginx)? [Y/n]: " v; IS_FRESH="${v:-y}"

    echo ""
    echo -e "${Y}────────────────────────────────────────${N}"
    echo -e "${C}📋 部署确认:${N}"
    printf "   %-14s %s\n" "项目路径:" "$PROJECT_DIR"
    printf "   %-14s %s\n" "MySQL:" "$MYSQL_USER / $MYSQL_PASSWORD @ $MYSQL_DB"
    printf "   %-14s %s\n" "AI:" "$AI_URL ($AI_MODEL)"
    printf "   %-14s %s\n" "端口:" "$SERVICE_PORT"
    printf "   %-14s %s\n" "Nginx:" "$(is_yes "$INSTALL_NGINX" && echo "是" || echo "否")"
    printf "   %-14s %s\n" "全新安装:" "$(is_yes "$IS_FRESH" && echo "是" || echo "否")"
    echo -e "${Y}────────────────────────────────────────${N}"
    echo ""

    read -p "确认部署? [Y/n]: " CONFIRM
    is_yes "${CONFIRM:-y}" || { echo "已取消"; exit 0; }
}

# ---- 1. 安装系统依赖 ----
install_deps() {
    step "1/7" "安装系统依赖..."
    if is_yes "$IS_FRESH"; then
        sudo apt update -y -qq
        sudo apt install -y -qq python3 python3-pip python3-venv
        sudo apt install -y -qq mysql-server nginx 2>/dev/null || warn "MySQL/Nginx 安装失败，可能已安装"
    fi

    # 验证 Python
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
        die "需要 Python 3.8+，当前: $DETECTED_PYTHON"
    fi
    ok "系统依赖就绪"
}

# ---- 2. 配置 MySQL ----
setup_mysql() {
    step "2/7" "配置 MySQL..."
    if is_yes "$IS_FRESH"; then
        sudo systemctl enable mysql 2>/dev/null || true
        sudo systemctl start mysql 2>/dev/null || {
            warn "MySQL 启动失败，检查是否已安装"
        }
    fi

    # 尝试多种方式创建数据库
    local sql="
CREATE DATABASE IF NOT EXISTS \`$MYSQL_DB\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$MYSQL_USER'@'localhost' IDENTIFIED BY '$MYSQL_PASSWORD';
GRANT ALL PRIVILEGES ON \`$MYSQL_DB\`.* TO '$MYSQL_USER'@'localhost';
FLUSH PRIVILEGES;
"
    local success=false
    for cmd in "sudo mysql" "mysql -u root" "mysql -u root -proot"; do
        if echo "$sql" | $cmd 2>/dev/null; then
            success=true; break
        fi
    done
    $success && ok "MySQL 数据库就绪" || warn "MySQL 配置失败，请手动建库"
}

# ---- 3. 部署文件 ----
deploy_files() {
    step "3/7" "部署项目文件..."
    mkdir -p "$PROJECT_DIR"

    if [ "$SCRIPT_DIR" != "$PROJECT_DIR" ]; then
        rsync -a --exclude='venv/' --exclude='__pycache__/' --exclude='uploads/*' \
              --exclude='.env' --exclude='*.log' \
              "$SCRIPT_DIR"/ "$PROJECT_DIR"/ 2>/dev/null || \
        cp -r "$SCRIPT_DIR"/* "$PROJECT_DIR"/
    fi

    mkdir -p "$PROJECT_DIR/uploads"
    chmod 755 "$PROJECT_DIR/uploads"
    ok "文件部署完成"
}

# ---- 4. 生成配置 ----
generate_config() {
    step "4/7" "生成配置文件..."

    # 仅当 .env 不存在时创建
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        cat > "$PROJECT_DIR/.env" << EOF
DB_HOST=localhost
DB_PORT=3306
DB_USER=$MYSQL_USER
DB_PASSWORD=$MYSQL_PASSWORD
DB_NAME=$MYSQL_DB
LM_STUDIO_URL=$AI_URL
LM_STUDIO_MODEL=$AI_MODEL
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "auto-$(date +%s)")
EOF
    fi

    # 更新 config.py（安全替换）
    if [ -f "$PROJECT_DIR/config.py" ]; then
        sed -i "s|http://100.101.108.100:1234/v1|$AI_URL|" "$PROJECT_DIR/config.py"
        sed -i "s|'model': os.getenv('LM_STUDIO_MODEL', '[^']*'|'model': os.getenv('LM_STUDIO_MODEL', '$AI_MODEL'|" "$PROJECT_DIR/config.py"
    fi
    ok "配置生成完成"
}

# ---- 5. 初始化数据库 ----
init_database() {
    step "5/7" "初始化数据库表..."
    local success=false
    for cmd in "sudo mysql $MYSQL_DB" "mysql -u $MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DB" "mysql -u root $MYSQL_DB"; do
        if $cmd < "$PROJECT_DIR/init_db.sql" 2>/dev/null; then
            success=true; break
        fi
    done
    $success && ok "数据库表初始化完成" || warn "数据库初始化失败，请手动: mysql $MYSQL_DB < init_db.sql"
}

# ---- 6. Python 依赖 ----
install_python() {
    step "6/7" "安装 Python 依赖..."

    # 如果 venv 存在但 Python 版本变了，重建
    if [ -f "$VENV_DIR/bin/python" ]; then
        local venv_ver=$("$VENV_DIR/bin/python" --version 2>/dev/null)
        if [ "$venv_ver" != "$DETECTED_PYTHON" ]; then
            warn "Python 版本变化，重建虚拟环境"
            rm -rf "$VENV_DIR"
        fi
    fi

    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR" || die "创建虚拟环境失败"
    fi

    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip -q
    pip install -r "$PROJECT_DIR/requirements.txt" -q || die "Python 依赖安装失败"
    deactivate
    ok "Python 依赖安装完成"
}

# ---- 7. 启动服务 ----
start_service() {
    step "7/7" "配置并启动服务..."

    # 用 sed 精确替换 app.py 中的端口（只替换 app.run 那行）
    sed -i "s/port=[0-9]\+/port=$SERVICE_PORT/" "$PROJECT_DIR/app.py" 2>/dev/null || true

    # 判断用哪个用户运行服务
    local run_user="$DETECTED_USER"
    id "$run_user" &>/dev/null || run_user="$USER"

    # 创建 systemd 服务
    sudo tee /etc/systemd/system/warehouse.service > /dev/null << SYSTEMDEOF
[Unit]
Description=Warehouse Management System
After=network.target
Wants=mysql.service

[Service]
Type=simple
User=$run_user
WorkingDirectory=$PROJECT_DIR
Environment="DB_HOST=localhost"
Environment="DB_USER=$MYSQL_USER"
Environment="DB_PASSWORD=$MYSQL_PASSWORD"
Environment="DB_NAME=$MYSQL_DB"
Environment="LM_STUDIO_URL=$AI_URL"
Environment="LM_STUDIO_MODEL=$AI_MODEL"
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMDEOF

    sudo systemctl daemon-reload
    sudo systemctl enable warehouse 2>/dev/null || true
    sudo systemctl restart warehouse

    # 等待启动
    sleep 2
    if sudo systemctl is-active --quiet warehouse; then
        ok "服务启动成功"
    else
        warn "服务启动失败，查看: sudo journalctl -u warehouse -n 20"
    fi

    # Nginx
    if is_yes "$INSTALL_NGINX"; then
        sudo tee /etc/nginx/sites-available/warehouse > /dev/null << NGINXEOF
server {
    listen 80;
    server_name _;
    client_max_body_size 50M;
    location / {
        proxy_pass http://127.0.0.1:$SERVICE_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
NGINXEOF
        sudo ln -sf /etc/nginx/sites-available/warehouse /etc/nginx/sites-enabled/
        sudo rm -f /etc/nginx/sites-enabled/default
        sudo systemctl restart nginx 2>/dev/null || true
    fi

    # 防火墙
    command -v ufw &>/dev/null && {
        sudo ufw allow "$SERVICE_PORT/tcp" 2>/dev/null || true
        is_yes "$INSTALL_NGINX" && sudo ufw allow 80/tcp 2>/dev/null || true
    }
}

# ---- 完成 ----
show_done() {
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo ""
    echo -e "${C}"
    echo "╔══════════════════════════════════════════╗"
    echo "║         ✅ 部署完成！                    ║"
    echo "╚══════════════════════════════════════════╝"
    echo -e "${N}"
    echo ""
    echo -e "  ${G}📍 访问:${N}  http://${SERVER_IP:-localhost}:$SERVICE_PORT"
    is_yes "$INSTALL_NGINX" && echo -e "            http://${SERVER_IP:-localhost}"
    echo ""
    echo -e "  ${G}🔧 管理:${N}"
    echo "     sudo systemctl status warehouse"
    echo "     sudo systemctl restart warehouse"
    echo "     sudo journalctl -u warehouse -f"
    echo ""
    echo -e "  ${G}📁 路径:${N} $PROJECT_DIR"
    echo -e "  ${G}🤖 AI:${N}   $AI_URL ($AI_MODEL)"
    echo ""
}

# ==========================================
#  主流程
# ==========================================
detect_env
ask_config
VENV_DIR="$PROJECT_DIR/venv"
echo -e "${G}🚀 开始部署...${N}\n"
install_deps
setup_mysql
deploy_files
generate_config
init_database
install_python
start_service
show_done
