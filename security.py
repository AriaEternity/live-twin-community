"""轻量安全件：CSRF 令牌 + 落库速率限制 + 注册密码强度校验"""
import random
import re
import secrets
import time

from flask import abort, request, session
from sqlalchemy import text

CSRF_KEY = '_csrf_token'


def generate_csrf_token():
    """每会话一个令牌，模板里用 {{ csrf_token() }} 取用"""
    if CSRF_KEY not in session:
        session[CSRF_KEY] = secrets.token_hex(16)
    return session[CSRF_KEY]


def csrf_protect():
    """before_request 钩子：所有 POST 请求校验表单令牌"""
    if request.method == 'POST':
        token = session.get(CSRF_KEY)
        if not token or not secrets.compare_digest(
            token, request.form.get('_csrf_token', '')
        ):
            abort(403)


# ── 注册密码强度校验（对齐主流站点规则） ──────────────

# 常见弱密码黑名单（小写比较，覆盖历年泄露榜 Top 高频项）
COMMON_PASSWORDS = {
    '12345678', '123456789', '1234567890', '12345678910',
    'password', 'password1', 'passw0rd', 'qwerty123', 'qwertyuiop',
    '11111111', '00000000', '66666666', '88888888',
    'abc12345', 'abcd1234', 'a1234567', 'aa123456',
    'iloveyou', 'letmein1', 'admin123', 'root1234',
    '1qaz2wsx', '1q2w3e4r', 'qwer1234', 'asdf1234', 'zxcv1234',
    '5201314a', 'woaini1314', 'a5201314', 'qazwsx123',
    'livetwin', 'yugioh123',
}


def validate_password(password, username='', email=''):
    """校验注册密码，返回错误信息列表（空列表 = 通过）。

    规则（参照主流站点）：
    - 8-64 位可打印 ASCII（不允许空格与中文）
    - 必须同时包含字母和数字（符号可选）
    - 不能全是同一字符，不能是键盘/泄露榜常见弱密码
    - 不能包含用户名或邮箱前缀
    """
    errors = []
    if not 8 <= len(password) <= 64:
        errors.append('密码长度须为 8-64 位')
        return errors  # 长度不合规时不再叠加其他提示
    if not re.fullmatch(r'[\x21-\x7e]+', password):
        errors.append('密码只能由字母、数字和常见符号组成，不能含空格或中文')
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        errors.append('密码必须同时包含字母和数字')
    if len(set(password)) == 1:
        errors.append('密码不能全是同一个字符')
    if password.lower() in COMMON_PASSWORDS:
        errors.append('密码过于常见，容易被猜到，请换一个')
    if username and len(username) >= 2 and username.lower() in password.lower():
        errors.append('密码不能包含用户名')
    email_prefix = email.split('@')[0] if email else ''
    if email_prefix and len(email_prefix) >= 3 and email_prefix.lower() in password.lower():
        errors.append('密码不能包含邮箱前缀')
    return errors

# ── 滑动窗口限速（SQLite 落库计数，多 worker 共享、重启不清） ──
# 表 rate_hits（见 models.RateHit）：一行 = 一次命中（key = scope:IP，ts = epoch 秒）。
# 用 db.engine 独立连接 + 原生 SQL 读写，刻意不碰 request 的 db.session，
# 避免限速逻辑误提交/回滚调用方挂在 session 上的业务对象。

# 全表清扫的兜底窗口：现有最大限速窗口为 3600s，2 倍即 7200s 外的行对任何
# 限速判定都无意义，可安全删除（每次调用仅 1% 概率触发，摊薄清扫成本）。
SWEEP_CUTOFF_SEC = 7200


def rate_limit(scope, max_hits, window_sec):
    """按 IP+用途限速，超限返回 True（调用方应 abort(429)）"""
    from models import db  # 延迟导入：避免 models ↔ security 循环依赖

    key = f'{scope}:{request.remote_addr}'
    now = time.time()
    try:
        with db.engine.begin() as conn:
            # 1. 删本 key 已过期的旧行，保持窗口精确
            conn.execute(
                text('DELETE FROM rate_hits WHERE key = :key AND ts <= :cutoff'),
                {'key': key, 'cutoff': now - window_sec},
            )
            # 2. 1% 概率全表清扫过期行，防止冷 key 残留行无限堆积
            if random.random() < 0.01:
                conn.execute(
                    text('DELETE FROM rate_hits WHERE ts < :cutoff'),
                    {'cutoff': now - SWEEP_CUTOFF_SEC},
                )
            # 3. 统计本 key 窗口内命中数
            hits = conn.execute(
                text('SELECT COUNT(*) FROM rate_hits WHERE key = :key AND ts > :cutoff'),
                {'key': key, 'cutoff': now - window_sec},
            ).scalar()
            if hits >= max_hits:
                return True
            # 4. 未超限：计入本次
            conn.execute(
                text('INSERT INTO rate_hits (key, ts) VALUES (:key, :ts)'),
                {'key': key, 'ts': now},
            )
        return False
    except Exception:
        # fail-open：DB 抖动（锁/磁盘满/坏库）不能变成全站 429/500，
        # 宁可短暂放行限速，也不让正常用户不可用。
        try:
            db.session.rollback()
        except Exception:
            pass
        return False
