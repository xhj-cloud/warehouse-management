"""
pytest 公共配置：把项目根目录加入 sys.path，提供 app / client / fake_db fixtures。
单元测试与 API 测试均不依赖真实数据库（模型层用 mock 替代）。
"""

import os
import sys

# 本地单元/API 测试不依赖真实认证：显式关闭 HTTP Basic Auth。
# （生产环境默认开启，由未设置 DISABLE_AUTH + 数据库用户表保证。必须在 import app 前设置。）
os.environ.setdefault('DISABLE_AUTH', 'true')

# 让 `import app` / `import models` / `import ai_service` 可用
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest


@pytest.fixture()
def app_mod():
    """导入 Flask 主应用模块（导入时不建立数据库连接）"""
    import app as m
    return m


@pytest.fixture()
def client(app_mod):
    """Flask test client"""
    return app_mod.app.test_client()


@pytest.fixture()
def fake_db():
    from tests.fake_db import FakeDB
    return FakeDB()
