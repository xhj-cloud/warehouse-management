"""
仓库管理系统 - 配置文件
"""

import os

# MySQL 数据库配置
MYSQL_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'warehouse'),
    'password': os.getenv('DB_PASSWORD', 'warehouse123'),
    'database': os.getenv('DB_NAME', 'warehouse_db'),
    'charset': 'utf8mb4',
}

# LM Studio AI 配置
LM_STUDIO_CONFIG = {
    'base_url': os.getenv('LM_STUDIO_URL', 'http://127.0.0.1:1234/v1'),
    # 默认模型：Qwen3.8-27B Q8（完整路径含 .gguf 子目录，需在 LM Studio 中加载该模型）
    'model': os.getenv('LM_STUDIO_MODEL', 'lmstudio-community/qwen3.8-27b-q8_0.gguf/qwen3.8-27b-q8_0.gguf'),
    'temperature': 0.7,
    'max_tokens': 4096,
    # Qwen3.8 是思考型模型：会先生成 reasoning_content 再出正文，单次完整分析和
    # 长文补货建议实测可达 80-120s，60s 太紧会误报"超时"。放宽到 180s；
    # 流式分析/对话走 stream=True 每次只有少量增量，不受此上限影响。
    'timeout': 180,
}

# Flask 配置
SECRET_KEY = os.getenv('SECRET_KEY', 'warehouse-secret-key-change-in-production')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 最大上传 50MB
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

# ==========================================
# HTTP Basic 认证（防内网任意 curl 直接改库存/删数据）
# ------------------------------------------
# 生产/内网部署：务必设置 AUTH_USER / AUTH_PASSWORD；未设置时认证禁用并打印警告，
# 仅用于纯本地快速联调。禁用方式：DISABLE_AUTH=true（回退到无认证，谨慎）。
# ==========================================
AUTH_USER = os.getenv('AUTH_USER', 'admin')
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD', '')      # 空则认证禁用（本地联调）
AUTH_DISABLED = os.getenv('DISABLE_AUTH', '').lower() in ('1', 'true', 'yes')

# 库存预警阈值
LOW_STOCK_THRESHOLD = 10  # 低于此数量触发低库存预警
