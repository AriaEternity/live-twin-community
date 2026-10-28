# -*- coding: utf-8 -*-
"""收藏帖子 + 关注用户功能端到端验证（临时库，不碰 instance/ygo.db）。
用法：FLASK_DEBUG=1 DATABASE_URL="sqlite:///<临时库路径>" python -X utf8 scripts/smoke_social.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import db, User, Board, Thread, Post, Notification, Favorite, Follow

c = app.test_client()
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  PASS {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


def csrf_from(html):
    m = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def login(username):
    """登出后以指定用户登录（CSRF 令牌取自登录页，会话内通用）"""
    c.get('/auth/logout')
    r = c.get('/auth/login')
    tok = csrf_from(r.get_data(as_text=True))
    return c.post('/auth/login',
                  data={'username': username, 'password': 'passw0rd1', '_csrf_token': tok})


def fav_count(thread_id, user_id):
    return Favorite.query.filter_by(user_id=user_id, thread_id=thread_id).count()


def follow_count(follower_id, followee_id):
    return Follow.query.filter_by(follower_id=follower_id, followee_id=followee_id).count()


with app.app_context():
    # 造三个普通用户 + 一个管理员（级联删除用例用）
    ua = User(username='social_a', email='sa@t.com', nickname='社交甲')
    ua.set_password('passw0rd1')
    ub = User(username='social_b', email='sb@t.com', nickname='社交乙')
    ub.set_password('passw0rd1')
    uc = User(username='social_c', email='sc@t.com', nickname='社交丙')
    uc.set_password('passw0rd1')
    adm = User(username='social_admin', email='sad@t.com', is_admin=True)
    adm.set_password('passw0rd1')
    db.session.add_all([ua, ub, uc, adm])
    db.session.commit()
    id_a, id_b, id_c = ua.id, ub.id, uc.id

    # 甲直接 ORM 造一个主题 + 首帖（种子板块首块；只带走 id，避免跨上下文用游离对象）
    board_id = Board.query.first().id
    th_a = Thread(title='收藏冒烟·甲的主题', content='甲的首帖正文', board_id=board_id, user_id=id_a)
    db.session.add(th_a)
    db.session.flush()
    po_a = Post(content='甲的首帖正文', thread_id=th_a.id, user_id=id_a)
    db.session.add(po_a)
    db.session.commit()
    tid_a = th_a.id

# ── (1) 未登录拦截 ──────────────────────────────────
r = c.get('/forum/favorites')
check('未登录 /forum/favorites 302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
r = c.get('/forum/following')
check('未登录 /forum/following 302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
# 未登录 POST：csrf_protect 是 before_request、先于 login_required，
# 无令牌直接 403（见下方 CSRF 用例）；带有效令牌才走到登录拦截 302
r = c.get('/auth/login')
anon_tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/forum/thread/{tid_a}/favorite', data={'_csrf_token': anon_tok})
check('未登录 POST 收藏（带令牌）302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
r = c.post(f'/forum/user/{id_a}/follow', data={'_csrf_token': anon_tok})
check('未登录 POST 关注（带令牌）302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
with app.app_context():
    check('未登录操作未落库', Favorite.query.count() == 0 and Follow.query.count() == 0)

# ── (2) 乙收藏甲帖：切换语义 ─────────────────────────
login('social_b')
r = c.get(f'/forum/thread/{tid_a}')
html = r.get_data(as_text=True)
check('帖子页 200 且未收藏态显示「☆ 收藏」', r.status_code == 200 and '☆ 收藏' in html)
tok = csrf_from(html)
r = c.post(f'/forum/thread/{tid_a}/favorite', data={'_csrf_token': tok})
check('乙收藏 302 回帖子页', r.status_code == 302 and f'/forum/thread/{tid_a}' in r.headers['Location'])
with app.app_context():
    check('收藏落库 1 行', fav_count(tid_a, id_b) == 1)
r = c.get(f'/forum/thread/{tid_a}')
html = r.get_data(as_text=True)
check('帖子页显示「已收藏」', '已收藏' in html)
check('帖子页显示收藏数 1', '★ 1</span>' in html)

# 再 POST → 取消
r = c.post(f'/forum/thread/{tid_a}/favorite', data={'_csrf_token': csrf_from(html)})
check('再 POST 取消收藏 302', r.status_code == 302)
with app.app_context():
    check('取消后 favorites 表空', Favorite.query.count() == 0)
r = c.get(f'/forum/thread/{tid_a}')
html = r.get_data(as_text=True)
check('取消后帖子页回到「☆ 收藏」', '已收藏' not in html and '☆ 收藏' in html)

# 再收藏恢复（供后续列表/级联用例使用）
r = c.post(f'/forum/thread/{tid_a}/favorite', data={'_csrf_token': csrf_from(html)})
with app.app_context():
    check('再收藏恢复 1 行', fav_count(tid_a, id_b) == 1)

# ── (3) 我的收藏列表 ────────────────────────────────
r = c.get('/forum/favorites')
html = r.get_data(as_text=True)
check('乙 /forum/favorites 200', r.status_code == 200)
check('乙收藏列表含甲帖标题', '收藏冒烟·甲的主题' in html)
login('social_a')
r = c.get('/forum/favorites')
html = r.get_data(as_text=True)
check('甲 /forum/favorites 200', r.status_code == 200)
check('甲未收藏，列表不含该帖标题', '收藏冒烟·甲的主题' not in html)

# ── (4) 乙关注甲：切换语义 + 自关注拒绝 ──────────────
login('social_b')
r = c.get(f'/forum/user/{id_a}')
html = r.get_data(as_text=True)
check('甲空间页 200 且未关注态显示「☆ 关注」', r.status_code == 200 and '☆ 关注' in html)
tok = csrf_from(html)
r = c.post(f'/forum/user/{id_a}/follow', data={'_csrf_token': tok})
check('乙关注甲 302 回空间页', r.status_code == 302 and f'/forum/user/{id_a}' in r.headers['Location'])
with app.app_context():
    check('关注落库 1 行', follow_count(id_b, id_a) == 1)
r = c.get(f'/forum/user/{id_a}')
html = r.get_data(as_text=True)
check('甲空间对乙显示「★ 已关注」', '★ 已关注' in html)
check('甲空间显示粉丝 1', '粉丝 1' in html)

# 关注自己 → 拒绝不落库
r = c.get(f'/forum/user/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/forum/user/{id_b}/follow', data={'_csrf_token': tok})
with app.app_context():
    check('乙关注自己未落库（仍只有乙→甲 1 行）',
          Follow.query.filter_by(follower_id=id_b).count() == 1)

# 重复 POST → 取消关注
r = c.get(f'/forum/user/{id_a}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/forum/user/{id_a}/follow', data={'_csrf_token': tok})
check('重复 POST 取消关注 302', r.status_code == 302)
with app.app_context():
    check('取消后 follows 表空', Follow.query.count() == 0)
r = c.get(f'/forum/user/{id_a}')
html = r.get_data(as_text=True)
check('取消后空间页回到「☆ 关注」', '★ 已关注' not in html and '☆ 关注' in html)

# ── (5) 重新关注：关注动态列表 + 计数 ────────────────
r = c.post(f'/forum/user/{id_a}/follow', data={'_csrf_token': csrf_from(html)})
with app.app_context():
    check('重新关注恢复 1 行', follow_count(id_b, id_a) == 1)
r = c.get('/forum/following')
html = r.get_data(as_text=True)
check('乙 /forum/following 200', r.status_code == 200)
check('乙关注动态含甲的主题', '收藏冒烟·甲的主题' in html)

# ORM 造丙的主题（乙未关注丙），断言不出现在乙的动态里
with app.app_context():
    th_c = Thread(title='关注冒烟·丙的孤岛主题', content='丙的首帖正文',
                  board_id=board_id, user_id=id_c)
    db.session.add(th_c)
    db.session.flush()
    po_c = Post(content='丙的首帖正文', thread_id=th_c.id, user_id=id_c)
    db.session.add(po_c)
    db.session.commit()
    tid_c = th_c.id
r = c.get('/forum/following')
html = r.get_data(as_text=True)
check('乙动态不含未关注丙的主题', '关注冒烟·丙的孤岛主题' not in html)

# followee_count / follower_count：乙关注 1（甲）、粉丝 0；甲粉丝 1
r = c.get(f'/forum/user/{id_b}')
html = r.get_data(as_text=True)
check('乙空间显示关注 1', '关注 1' in html)
check('乙空间显示粉丝 0', '粉丝 0' in html)
with app.app_context():
    check('ORM：乙 followee=1 / follower=0，甲 follower=1',
          Follow.query.filter_by(follower_id=id_b).count() == 1
          and Follow.query.filter_by(followee_id=id_b).count() == 0
          and Follow.query.filter_by(followee_id=id_a).count() == 1)

# ── (6) 无 CSRF 的 POST → 403 且状态不变 ─────────────
r = c.post(f'/forum/thread/{tid_a}/favorite', data={})
check('无 CSRF POST 收藏 403', r.status_code == 403)
r = c.post(f'/forum/user/{id_a}/follow', data={})
check('无 CSRF POST 关注 403', r.status_code == 403)
with app.app_context():
    check('403 后收藏/关注行数不变',
          fav_count(tid_a, id_b) == 1 and follow_count(id_b, id_a) == 1)

# ── (6.5) 关注/粉丝列表页 + 空间页计数链接 + 按钮新文案 ──
# 新契约：GET /forum/followees 与 /forum/followers 仅看自己列表；
# 自己空间计数带链接、他人空间纯文本；按钮 ☆ 关注 / ★ 已关注（含隐藏「取消关注」）
c.get('/auth/logout')
r = c.get('/forum/followees')
check('未登录 /forum/followees 302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
r = c.get('/forum/followers')
check('未登录 /forum/followers 302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])

# 乙已关注甲（第(5)节恢复），甲未回关 → 乙关注列表含甲昵称但无「互关」
login('social_b')
r = c.get('/forum/followees')
html = r.get_data(as_text=True)
check('乙 /forum/followees 200', r.status_code == 200)
check('乙关注列表含甲昵称', '社交甲' in html)
# 「互关」/mutual-badge 字样会出现在页内 <style> 里，断言要对渲染出的徽章标记本身
check('甲未回关，乙关注列表无互关徽章', '<span class="mutual-badge">互关</span>' not in html)
r = c.get('/forum/followers')
html = r.get_data(as_text=True)
check('乙 /forum/followers 200', r.status_code == 200)
check('甲未回关，乙粉丝列表无甲昵称', '社交甲' not in html)

# 甲回关乙 → 乙关注列表出现「互关」，粉丝列表含甲昵称
login('social_a')
r = c.get(f'/forum/user/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
c.post(f'/forum/user/{id_b}/follow', data={'_csrf_token': tok})
with app.app_context():
    check('甲回关乙落库 1 行', follow_count(id_a, id_b) == 1)
login('social_b')
r = c.get('/forum/followees')
html = r.get_data(as_text=True)
check('互关后乙关注列表含互关徽章', '<span class="mutual-badge">互关</span>' in html)
check('未关注的丙不在乙关注列表', '社交丙' not in html)
r = c.get('/forum/followers')
html = r.get_data(as_text=True)
check('互关后乙粉丝列表含甲昵称', '社交甲' in html)
check('未关注的丙不在乙粉丝列表', '社交丙' not in html)

# 自己空间计数为链接，他人空间计数为纯文本
r = c.get(f'/forum/user/{id_b}')
html = r.get_data(as_text=True)
check('乙看自己空间：计数含 /forum/followees 链接', 'href="/forum/followees"' in html)
check('乙看自己空间：计数含 /forum/followers 链接', 'href="/forum/followers"' in html)
r = c.get(f'/forum/user/{id_a}')
html = r.get_data(as_text=True)
check('乙看甲空间：计数无 followees 链接', 'href="/forum/followees"' not in html)
check('乙看甲空间：计数无 followers 链接', 'href="/forum/followers"' not in html)

# 按钮文案：已关注 ★（含隐藏「取消关注」），未关注 ☆
check('乙看甲空间按钮为「★ 已关注」', '★ 已关注' in html)
check('已关注按钮含隐藏「取消关注」', '取消关注' in html)
r = c.get(f'/forum/user/{id_c}')
html = r.get_data(as_text=True)
check('乙看丙空间按钮为「☆ 关注」', '☆ 关注' in html)

# ── (7) 级联：删用户清 Favorite/Follow；删帖清收藏 ───
# 乙收藏丙的主题 + 关注丙，让丙的两侧都有数据可清
r = c.get(f'/forum/thread/{tid_c}')
tok = csrf_from(r.get_data(as_text=True))
c.post(f'/forum/thread/{tid_c}/favorite', data={'_csrf_token': tok})
r = c.get(f'/forum/user/{id_c}')
tok = csrf_from(r.get_data(as_text=True))
c.post(f'/forum/user/{id_c}/follow', data={'_csrf_token': tok})
with app.app_context():
    check('前置：乙收藏丙帖 1 行', fav_count(tid_c, id_b) == 1)
    check('前置：乙关注丙 1 行', follow_count(id_b, id_c) == 1)

# 管理员删丙 → 丙帖上的收藏、丙参与的关注关系全部清零
login('social_admin')
r = c.get('/admin/users')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/admin/user/{id_c}/delete', data={'_csrf_token': tok})
check('管理员删丙 302', r.status_code == 302)
with app.app_context():
    check('删丙后其主题上的收藏清零', Favorite.query.filter_by(thread_id=tid_c).count() == 0)
    check('删丙后其参与的关注关系清零', Follow.query.filter(
        (Follow.follower_id == id_c) | (Follow.followee_id == id_c)).count() == 0)
    check('删丙不影响乙收藏甲帖与乙关注甲',
          fav_count(tid_a, id_b) == 1 and follow_count(id_b, id_a) == 1)

# 管理员删甲帖（forum.delete_thread）→ 该帖 favorites 随 ORM 级联清零
r = c.get(f'/forum/thread/{tid_a}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/forum/thread/{tid_a}/delete', data={'_csrf_token': tok})
check('管理员删甲帖 302', r.status_code == 302)
with app.app_context():
    check('删帖后该帖收藏行级联清零', Favorite.query.filter_by(thread_id=tid_a).count() == 0)
    check('甲帖本体已删', Thread.query.filter_by(id=tid_a).count() == 0)
    db.session.remove()

# ── (8) 清理测试数据（按 id/用户幂等删）──────────────
with app.app_context():
    test_uids = [id_a, id_b, id_c]
    Favorite.query.filter(Favorite.user_id.in_(test_uids)).delete(synchronize_session=False)
    Follow.query.filter(
        (Follow.follower_id.in_(test_uids)) | (Follow.followee_id.in_(test_uids))
    ).delete(synchronize_session=False)
    Notification.query.filter(
        (Notification.recipient_id.in_(test_uids)) | (Notification.actor_id.in_(test_uids))
    ).delete(synchronize_session=False)
    Post.query.filter(Post.thread_id.in_([tid_a, tid_c])).delete(synchronize_session=False)
    Thread.query.filter(Thread.id.in_([tid_a, tid_c])).delete(synchronize_session=False)
    User.query.filter(User.username.in_(
        ['social_a', 'social_b', 'social_c', 'social_admin'])).delete(synchronize_session=False)
    db.session.commit()
    check('清理后测试用户/收藏/关注均无残留',
          User.query.filter(User.username.like('social_%')).count() == 0
          and Favorite.query.count() == 0
          and Follow.query.count() == 0)

print(f'\n结果：{passed} 通过 / {failed} 失败')
sys.exit(1 if failed else 0)
