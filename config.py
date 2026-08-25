"""
仓库管理系统 - 配置文件
"""

import os

from dotenv import load_dotenv

# 加载项目根目录的 .env（若存在）。不覆盖已存在的环境变量：
# systemd Environment= / shell export 的优先级高于 .env。
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# MySQL 数据库配置
MYSQL_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'warehouse'),
    # 必填：不再内置默认密码，请通过 .env / systemd Environment= 自行设定强密码
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'warehouse_db'),
    'charset': 'utf8mb4',
}

# LM Studio AI 配置
LM_STUDIO_CONFIG = {
    'base_url': os.getenv('LM_STUDIO_URL', 'http://127.0.0.1:1234/v1'),
    # 默认模型：Qwen3.8-27B Q8（完整路径含 .gguf 子目录，需在 LM Studio 中加载该模型）
    'model': os.getenv('LM_STUDIO_MODEL', 'lmstudio-community/qwen3.8-27b-q8_0.gguf/qwen3.8-27b-q8_0.gguf'),
    'temperature': 0.7,
    # Qwen3.8 是思考型模型，会先推理(reasoning_content)再出正文。若 max_tokens 太紧，
    # 推理会吃光预算导致正文为空。给足预算让推理+正文都有空间；同时在 system prompt 里
    # 约束"少推理、直接答"，避免推理失控。
    'max_tokens': 16384,
    # Qwen3.8 是思考型模型：会先生成 reasoning_content 再出正文，单次完整分析和
    # 长文补货建议实测可达 80-120s，60s 太紧会误报"超时"。放宽到 180s；
    # 流式分析/对话走 stream=True 每次只有少量增量，不受此上限影响。
    'timeout': 180,
    # 库存分析正文硬上限（字符数）：思考型模型容易长篇大论，单次分析实测可达 3000+ 字、
    # 耗时 2-4 分钟。除在 system prompt 里约束"简短"外，analyze_stream 还按此值做硬截断——
    # 正文累计达到该长度即停止读取流并直接返回（同时提前断开与 LM Studio 的连接）。
    'max_analyze_chars': int(os.getenv('ANALYZE_MAX_CHARS', '600')),
}

# Flask 配置
SECRET_KEY = os.getenv('SECRET_KEY', 'warehouse-secret-key-change-in-production')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 最大上传 50MB
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

# ==========================================
# HTTP Basic 认证（防内网任意 curl 直接改库存/删数据）
# ------------------------------------------
# 生产/内网部署：务必设置 AUTH_USER / AUTH_PASSWORD。
# 显式关闭认证仅用于纯本地快速联调：DISABLE_AUTH=true（回退到无认证，谨慎）。
# ==========================================
AUTH_USER = os.getenv('AUTH_USER', 'admin')
# 超级管理员兜底密码；留空则无兜底（首次启动 users 表为空时会自动生成随机初始密码并打印到日志）
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD', '')
AUTH_DISABLED = os.getenv('DISABLE_AUTH', '').lower() in ('1', 'true', 'yes')

# 库存预警阈值
LOW_STOCK_THRESHOLD = 10  # 低于此数量触发低库存预警
