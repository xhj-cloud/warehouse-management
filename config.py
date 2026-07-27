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
    'base_url': os.getenv('LM_STUDIO_URL', 'http://100.101.108.100:1234/v1'),
    'model': os.getenv('LM_STUDIO_MODEL', 'qwen3.6-35b-a3b'),
    'temperature': 0.7,
    'max_tokens': 4096,
    'timeout': 60,
}

# Flask 配置
SECRET_KEY = os.getenv('SECRET_KEY', 'warehouse-secret-key-change-in-production')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 最大上传 50MB
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

# 库存预警阈值
LOW_STOCK_THRESHOLD = 10  # 低于此数量触发低库存预警
