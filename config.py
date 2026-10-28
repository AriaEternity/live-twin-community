"""Flask 配置"""
import os
import secrets
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # 生产环境必须由环境变量注入 SECRET_KEY（systemd 单元 Environment），
    # 缺失时直接拒绝启动，绝不回退到代码库里的公开兜底值；
    # 仅本地开发（python app.py，入口会置 FLASK_DEBUG=1）允许使用开发兜底值
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        if os.environ.get('FLASK_DEBUG') != '1':
            raise RuntimeError(
                'SECRET_KEY 环境变量未设置：生产请在 systemd Environment 配置；本地开发请用 python app.py 启动'
            )
        SECRET_KEY = secrets.token_hex(32)  # 仅开发：每进程随机，重启后会话失效
    # 本地调试用 FLASK_DEBUG=1 走 HTTP；生产由 nginx HTTPS 反代，必须标记 Secure
    DEBUG = (os.environ.get('FLASK_DEBUG') == '1')
    SESSION_COOKIE_SECURE = not DEBUG
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = not DEBUG
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'ygo.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # sqlite timeout 即 busy_timeout（秒）：浏览计数等高频写入在 WAL 下仍可能短暂持锁，
    # 让写请求排队等待而不是立刻报 database is locked
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 10}}
    # 分页（从论坛使用者角度：板块列表 15 条/页，帖子内 20 楼/页，兼顾加载速度与翻页频率）
    THREADS_PER_PAGE = 15
    POSTS_PER_PAGE = 20
    # 图片上传
    UPLOAD_FOLDER = os.path.abspath(
        os.environ.get('UPLOAD_FOLDER') or os.path.join(basedir, 'uploads'))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024          # 单请求上限 8MB
    ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_IMAGES_PER_POST = 3
    # 可选：保护已创建的主管理员不被降权/删除；注册流程不据此授予权限。
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '').strip()
    # 默认不信任客户端自带的代理头，仅在受控反代后显式启用。
    TRUST_PROXY_HEADERS = os.environ.get('TRUST_PROXY_HEADERS') == '1'
