"""Live☆Twin 粉丝论坛 —— Flask 应用入口"""
import os
import click
from datetime import timedelta

# 仅开发入口（python app.py）标记调试模式：config 据此放行开发用 SECRET_KEY 兜底值。
# gunicorn 以 `app:app` 导入本模块时 __name__ != '__main__'，生产保持严格校验。
if __name__ == '__main__':
    os.environ.setdefault('FLASK_DEBUG', '1')

from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from models import db, User, seed_default_boards
from security import generate_csrf_token, csrf_protect, validate_password
from emotes import render_content
from routes.main import main_bp
from routes.auth import auth_bp
from routes.forum import forum_bp
from routes.messages import messages_bp
from routes.cards import cards_bp
from routes.decks import decks_bp
from routes.groups import groups_bp
from routes.admin import admin_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # 部署在 nginx 反代之后：信任 X-Forwarded-*（限速/日志需要真实 IP）
    if app.config['TRUST_PROXY_HEADERS']:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # 确保 instance 与 uploads 目录存在
    os.makedirs(os.path.join(app.root_path, 'instance'), exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # 数据库
    db.init_app(app)

    # 登录管理
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.login_message = '请先登录后再访问'
    login_manager.login_message_category = 'info'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # 蓝图
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(forum_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(cards_bp)
    app.register_blueprint(decks_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(admin_bp)

    # CSRF：模板注入令牌 + 所有 POST 校验
    app.jinja_env.globals['csrf_token'] = generate_csrf_token
    app.before_request(csrf_protect)

    # 北京时间过滤器：naive datetime 视为 UTC，+8 小时后格式化（跨代理契约，模板里用 {{ dt|bjtime }}）
    @app.template_filter('bjtime')
    def bjtime(dt, fmt='%Y-%m-%d %H:%M'):
        if dt is None:
            return ''
        return (dt + timedelta(hours=8)).strftime(fmt)

    # 正文渲染过滤器：先转义再把 [em:id] 替换为白名单表情 <img>（emotes.py，模板里用 {{ content|render_content }}）
    app.add_template_filter(render_content, 'render_content')

    # ── 全站中文错误页 ──────────────────────────────
    ERROR_PAGES = {
        400: ('请求有误', '服务器无法理解这个请求，请检查输入后重试。'),
        403: ('没有访问权限', '抱歉，你没有权限访问这个页面。'),
        404: ('页面不存在', '你访问的页面不存在或已被移动。'),
        405: ('方法不允许', '当前页面不支持这种请求方式。'),
        413: ('文件太大', '附件超过 8MB 大小限制，请压缩或删减后再上传。'),
        429: ('操作太频繁', '操作太频繁，请约 1 分钟后再试。'),
        500: ('服务器开小差了', '服务器出了点问题，请稍后再试。'),
    }

    def _make_error_handler(code):
        def handler(e):
            title, message = ERROR_PAGES[code]
            # /api/ 路径保持 JSON 响应，不破坏现有 API（如 routes/cards.py）的行为
            if request.path.startswith('/api/'):
                return jsonify({'error': message}), code
            try:
                return render_template('errors.html',
                                       code=code, title=title, message=message), code
            except Exception:
                # 错误页自身渲染失败（如模板或通知数注入异常）时退化为纯文本，绝不二次 500
                return (f'{code} {title}：{message}', code,
                        {'Content-Type': 'text/plain; charset=utf-8'})
        return handler

    for _code in ERROR_PAGES:
        app.register_error_handler(_code, _make_error_handler(_code))

    # 初始化（建表 + 种子板块）
    with app.app_context():
        db.create_all()
        seed_default_boards()

    @app.cli.command('grant-admin')
    @click.argument('username')
    def grant_admin(username):
        """由持有部署命令行权限的维护者，给已注册账号显式授予管理员。"""
        user = User.query.filter_by(username=username).first()
        if user is None:
            raise click.ClickException('账号不存在，请先通过注册页创建普通账号')
        user.is_admin = True
        db.session.commit()
        click.echo('已授予指定账号管理员权限')

    @app.cli.command('reset-password')
    @click.argument('username')
    @click.password_option(confirmation_prompt=True)
    def reset_password(username, password):
        """核验用户身份后由维护者交互重置密码，不在日志中打印密码。"""
        user = User.query.filter_by(username=username).first()
        if user is None:
            raise click.ClickException('账号不存在')
        errors = validate_password(password, user.username, user.email)
        if errors:
            raise click.ClickException('；'.join(errors))
        user.set_password(password)
        db.session.commit()
        click.echo('密码已重置；新密码不会输出到日志')

    return app


app = create_app()

if __name__ == '__main__':
    # debug 开关与环境变量绑定：绝不在生产入口（gunicorn）意外开启 Werkzeug 调试器
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8080')))
