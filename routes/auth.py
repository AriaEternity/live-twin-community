"""用户认证：注册 / 登录 / 登出 / 个人设置 / 找回密码（人工）/ 头像上传"""
import os
import re
import secrets
from urllib.parse import urlparse

from PIL import Image

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import IntegrityError
from models import db, User
from security import rate_limit, validate_password
from wordfilter import find_sensitive, log_hit

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# 账号名（登录用）：2-24 位，仅允许字母、数字、下划线
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{2,24}$')
# 昵称（展示用）：B 站式黑名单制——中日韩文字、字母、数字、常见符号（☆・等）都可以，
# 只禁止空白、控制字符和零宽字符（零宽可绕过防仿冒撞名检测）；
# 长度按"汉字等非 ASCII 算 2、ASCII 算 1"计，2-24
_NICKNAME_RE = re.compile(r'^[^\s\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\ufeff]+$')
# 邮箱：基础格式校验（仅要求 a@b.c，不强求域名合法性）
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def validate_username(username):
    """校验账号名格式，返回错误信息或 None"""
    if not _USERNAME_RE.match(username):
        return '账号名须为 2-24 位，仅允许字母、数字、下划线'
    return None


def _nickname_char_len(s):
    """昵称长度：非 ASCII（汉字、日文、全角符号等）算 2，ASCII 算 1"""
    return sum(2 if ord(ch) > 127 else 1 for ch in s)


def validate_nickname(nickname):
    """校验昵称格式与长度，返回错误信息或 None"""
    if not _NICKNAME_RE.match(nickname):
        return '昵称不能包含空格或控制字符'
    ln = _nickname_char_len(nickname)
    if not (2 <= ln <= 24):
        return '昵称总字符数须为 2-24，其中汉字等非 ASCII 字符按 2 个字符计算'
    return None


def validate_registration(username, email):
    """返回注册字段校验错误列表（空表 = 通过）"""
    errors = []
    err = validate_username(username)
    if err:
        errors.append(err)
    if not _EMAIL_RE.match(email):
        errors.append('邮箱格式不正确')
    return errors


def display_name_taken(name, exclude_user_id=None):
    """展示名是否与他人冲突：与他人的昵称或账号名相同（忽略大小写）都算仿冒。
    没设昵称的人展示的就是账号名，所以两个字段都要查。"""
    lowered = name.lower()
    q = User.query.filter(
        db.or_(db.func.lower(User.nickname) == lowered,
               db.func.lower(User.username) == lowered))
    if exclude_user_id is not None:
        q = q.filter(User.id != exclude_user_id)
    return q.first() is not None


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('forum.index'))

    if request.method == 'POST':
        # 注册限速：每 IP 每分钟 5 次（防批量注册）
        if rate_limit('register', 5, 60):
            abort(429)
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        nickname = request.form.get('nickname', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        errors = validate_registration(username, email)
        if User.query.filter_by(username=username).first():
            errors.append('该用户名已被注册')
        if User.query.filter_by(email=email).first():
            errors.append('该邮箱已被注册')
        # 防仿冒：用户名会成为初始昵称（展示名），不能与现有用户的昵称/账号名冲突
        if display_name_taken(username):
            errors.append('该用户名与他人的昵称重复，请换一个')
        # 选填昵称：填了就按昵称规则校验 + 防仿冒；不填则默认与用户名相同
        if nickname:
            err = validate_nickname(nickname)
            if err:
                errors.append(err)
            elif display_name_taken(nickname):
                errors.append('该昵称已被他人使用，换一个吧')
            else:
                # 违规词红线硬拦截：命中即拒绝注册，日志留痕，绝不回显具体命中词
                hit_word = find_sensitive(nickname)
                if hit_word:
                    log_hit('nickname', hit_word, nickname, username, request.remote_addr or '')
                    errors.append('该昵称包含违规信息，请更换')
        errors.extend(validate_password(password, username, email))
        if password != confirm:
            errors.append('两次密码不一致')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('auth/register.html')

        # 公开注册始终是普通用户；管理员只能通过维护者CLI显式授予
        user = User(username=username, email=email,
                    is_admin=False)
        user.set_password(password)
        # 注册时填了昵称就用昵称，否则初始昵称与用户名相同；之后都可在设置中修改
        user.nickname = nickname or username
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # 并发注册撞用户名/邮箱唯一约束（先查后插的竞态窗口），按已注册处理
            db.session.rollback()
            flash('该用户名或邮箱已被注册', 'error')
            return render_template('auth/register.html')
        login_user(user)
        flash(f'欢迎加入 Live☆Twin 社区，{username}！', 'success')
        return redirect(url_for('forum.index'))

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('forum.index'))

    if request.method == 'POST':
        # 登录限速：每 IP 每分钟 10 次（防爆破）
        if rate_limit('login', 10, 60):
            abort(429)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = 'remember' in request.form

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            # 开放重定向防护：只允许站内相对路径
            # 拒绝 https://…、//evil.com、/\evil.com（反斜杠会被浏览器规范化为 //）
            if (not next_page
                    or not next_page.startswith('/')
                    or next_page.startswith('//')
                    or '\\' in next_page
                    or urlparse(next_page).netloc):
                next_page = url_for('forum.index')
            flash(f'欢迎回来，{username}！', 'success')
            return redirect(next_page)

        flash('用户名或密码错误', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录', 'info')
    return redirect(url_for('main.index'))


@auth_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """个人设置：昵称与个性签名。账号名（用户名）不可在此修改；头像走 /auth/avatar 上传。"""
    if request.method == 'POST':
        new_nickname = request.form.get('nickname', '').strip()

        # 昵称留空视为恢复默认：置回空串，display_name 会回退为账号名
        if new_nickname:
            err = validate_nickname(new_nickname)
            if err:
                flash(err, 'error')
                return render_template('auth/settings.html')
            # 防仿冒：不能与他人的昵称或账号名相同（自己的不算）
            if display_name_taken(new_nickname, exclude_user_id=current_user.id):
                flash('该昵称已被他人使用，换一个吧', 'error')
                return render_template('auth/settings.html')
            # 违规词红线硬拦截：命中即拒绝保存，日志留痕，绝不回显具体命中词
            hit_word = find_sensitive(new_nickname)
            if hit_word:
                log_hit('nickname', hit_word, new_nickname, current_user.username,
                        request.remote_addr or '')
                flash('该昵称包含违规信息，请更换', 'error')
                return render_template('auth/settings.html')

        current_user.nickname = new_nickname
        # 签名最长 200 字符，超出直接截断；头像只走 /auth/avatar 上传，本表单不再触碰 avatar_url
        current_user.signature = request.form.get('signature', '').strip()[:200]
        db.session.commit()
        flash('个人设置已保存', 'success')
        # 保存成功直接回论坛首页；校验失败仍在原页重渲染报错
        return redirect(url_for('forum.index'))

    return render_template('auth/settings.html')


# 头像允许的图片格式（Pillow img.format 判断，不信赖用户提供的文件名/扩展名）
_ALLOWED_AVATAR_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}


@auth_bp.route('/avatar', methods=['POST'])
@login_required
def upload_avatar():
    """上传头像：Pillow 校验真图片，统一处理为 256×256 JPEG 存 uploads/avatars/"""
    # 上传限速：独立桶每 IP 每小时 10 次（不与登录/发帖桶互相消耗）
    if rate_limit('avatar_upload', 10, 3600):
        abort(429)

    file = request.files.get('avatar')
    if not file or not file.filename:
        flash('请选择要上传的头像图片', 'error')
        return redirect(url_for('auth.settings'))

    try:
        img = Image.open(file.stream)
        img.load()  # 强制解码，确认是真实完整的图片而非伪装文件
    except Exception:
        flash('请上传有效的图片文件', 'error')
        return redirect(url_for('auth.settings'))
    if img.format not in _ALLOWED_AVATAR_FORMATS:
        flash('仅支持 JPEG / PNG / WebP / GIF 格式的头像', 'error')
        return redirect(url_for('auth.settings'))

    # 去 alpha：透明底用白底合成，避免转 JPEG 后透明区域变黑
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel('A'))
        img = bg
    else:
        img = img.convert('RGB')

    # 居中裁方形（取短边）后缩放到 256×256
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((256, 256), Image.LANCZOS)

    # 随机文件名（与 save_images 同为 32 位 hex，不使用用户提供的文件名，杜绝路径穿越与碰撞）
    filename = secrets.token_hex(16) + '.jpg'
    avatar_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'avatars')
    os.makedirs(avatar_dir, exist_ok=True)  # exist_ok 保证并发下安全
    img.save(os.path.join(avatar_dir, filename), 'JPEG', quality=85)

    # 旧头像文件不删除：项目 uploads/ 只增不减策略（见运维手册「数据备份与回滚」）
    current_user.avatar_url = f'/uploads/avatars/{filename}'
    try:
        db.session.commit()
    except IntegrityError:
        # 踩坑 #23：commit 必须兜底 rollback，避免会话卡死在失败状态
        db.session.rollback()
        flash('头像保存失败，请稍后再试', 'error')
        return redirect(url_for('auth.settings'))

    flash('头像已更新', 'success')
    return redirect(url_for('auth.settings'))


@auth_bp.route('/forgot', methods=['GET'])
def forgot():
    """找回密码：自助找回已下线。

    旧方案把注册邮箱当密保答案（匹配即签发重置 token），但邮箱对熟人来说
    是可猜的半公开信息，猜到即可绕过密码接管账号，故改为纯提示页：
    引导用户联系站长人工重置（scripts/admin_reset_password.py）。
    本路由不再签发任何 token，/auth/reset/<token> 已一并下线。
    """
    return render_template('auth/forgot.html')
