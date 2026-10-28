# -*- coding: utf-8 -*-
"""私信功能端到端验证（临时库，不碰 instance/ygo.db）。
用法：FLASK_DEBUG=1 DATABASE_URL="sqlite:///<临时库路径>" python -X utf8 scripts/smoke_messages.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import db, User, PrivateMessage, MessageAttachment, Board, Thread, Post, Notification

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


with app.app_context():
    # 造两个用户 + 一个管理员（级联删除用例用）
    u1 = User(username='msgtest_a', email='a@t.com', nickname='测试甲')
    u1.set_password('passw0rd1')
    u2 = User(username='msgtest_b', email='b@t.com', nickname='测试乙')
    u2.set_password('passw0rd1')
    u3 = User(username='msgtest_c', email='c@t.com', nickname='测试丙')
    u3.set_password('passw0rd1')
    adm = User(username='msgtest_admin', email='ad@t.com', is_admin=True)
    adm.set_password('passw0rd1')
    db.session.add_all([u1, u2, u3, adm])
    db.session.commit()
    id_a, id_b, id_c = u1.id, u2.id, u3.id

# 未登录拦截
r = c.get('/messages/')
check('未登录 inbox 302', r.status_code == 302 and '/auth/login' in r.headers['Location'])

# 登录甲
r = c.get('/auth/login')
tok = csrf_from(r.get_data(as_text=True))
r = c.post('/auth/login', data={'username': 'msgtest_a', 'password': 'passw0rd1', '_csrf_token': tok})
check('甲登录 302', r.status_code == 302)

# 对话页渲染（真实模板）
r = c.get(f'/messages/{id_b}')
html = r.get_data(as_text=True)
check('对话页 200', r.status_code == 200)
check('对话页含对方昵称', '测试乙' in html)
check('对话页含表单', 'name="content"' in html)

# 甲 → 乙 发私信
tok = csrf_from(html)
r = c.post(f'/messages/{id_b}', data={'content': '你好呀，这是测试私信', '_csrf_token': tok})
check('发送 302 回对话页', r.status_code == 302 and f'/messages/{id_b}' in r.headers['Location'])
with app.app_context():
    m = PrivateMessage.query.filter_by(sender_id=id_a).first()
    check('落库且未读', m is not None and m.is_read is False and m.content == '你好呀，这是测试私信')

# 缺 CSRF 拒绝
r = c.post(f'/messages/{id_b}', data={'content': '无令牌'})
check('无 CSRF 403', r.status_code == 403)

# 空内容 / 超长
r = c.get(f'/messages/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/messages/{id_b}', data={'content': '   ', '_csrf_token': tok})
with app.app_context():
    check('空内容未落库', PrivateMessage.query.filter_by(sender_id=id_a).count() == 1)
r = c.post(f'/messages/{id_b}', data={'content': 'x' * 2001, '_csrf_token': tok})
with app.app_context():
    check('超长未落库', PrivateMessage.query.filter_by(sender_id=id_a).count() == 1)

# 发给自己
r = c.post(f'/messages/{id_a}', data={'content': 'self', '_csrf_token': tok}, follow_redirects=False)
with app.app_context():
    check('发给自己未落库', PrivateMessage.query.filter_by(recipient_id=id_a).count() == 0)

# ── 私信图片附件（与论坛附件同体系：uploads/ 随机文件名）──────────────
# 最小的合法 PNG 文件头；服务端只校验扩展名，不解析字节内容
PNG_BYTES = b'\x89PNG\r\n\x1a\n'
# 记录本用例真实落盘的附件文件名，结尾逐个删除（uploads/ 里的其他文件不碰）
created_files = []


def latest_msg():
    """甲 → 乙 最新一条私信"""
    return PrivateMessage.query.filter_by(
        sender_id=id_a, recipient_id=id_b).order_by(PrivateMessage.id.desc()).first()


# (2) 图文混合：content='看图' + 一张 gif → 消息行与附件行各自字段正确
r = c.get(f'/messages/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/messages/{id_b}', data={
    'content': '看图', '_csrf_token': tok,
    'images': (io.BytesIO(PNG_BYTES), 'photo.gif'),
})
check('图文消息 302', r.status_code == 302 and f'/messages/{id_b}' in r.headers['Location'])
with app.app_context():
    m = latest_msg()
    check('图文消息落库 content=看图', m is not None and m.content == '看图')
    check('图文消息 images 关系 1 行', m is not None and len(m.images) == 1)
    if m and m.images:
        fn = m.images[0].filename
        created_files.append(fn)
        check('gif 附件名为 uuid.hex 形式', re.fullmatch(r'[0-9a-f]{32}\.gif', fn) is not None)
        check('gif 附件已落盘', os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], fn)))

# (1) 纯图消息：空文字 + 一张 png（放在最后发，保证乙 inbox 摘要为 [图片]）
r = c.get(f'/messages/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/messages/{id_b}', data={
    'content': '', '_csrf_token': tok,
    'images': (io.BytesIO(PNG_BYTES), 't.png'),
})
check('纯图消息 302', r.status_code == 302 and f'/messages/{id_b}' in r.headers['Location'])
with app.app_context():
    m = latest_msg()
    check('纯图消息落库 content 为空', m is not None and m.content == '')
    check('纯图消息 images 关系 1 行', m is not None and len(m.images) == 1)
    if m and m.images:
        fn = m.images[0].filename
        created_files.append(fn)
        check('png 附件名为 uuid.hex 形式', re.fullmatch(r'[0-9a-f]{32}\.png', fn) is not None)
        check('png 附件已落盘', os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], fn)))

# (3) 一次传 4 张（超过 MAX_IMAGES_PER_POST=3）→ 拒绝未落库
with app.app_context():
    cnt_before = PrivateMessage.query.filter_by(sender_id=id_a).count()
    att_before = MessageAttachment.query.count()
r = c.get(f'/messages/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/messages/{id_b}', data={
    'content': '', '_csrf_token': tok,
    'images': [(io.BytesIO(PNG_BYTES), f'a{i}.png') for i in range(4)],
})
with app.app_context():
    check('4 张图未落库消息', PrivateMessage.query.filter_by(sender_id=id_a).count() == cnt_before)
    check('4 张图未落库附件', MessageAttachment.query.count() == att_before)

# (4) 非白名单扩展名（.txt）→ 拒绝未落库
r = c.get(f'/messages/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/messages/{id_b}', data={
    'content': '', '_csrf_token': tok,
    'images': (io.BytesIO(b'not an image'), 'evil.txt'),
})
with app.app_context():
    check('txt 附件未落库消息', PrivateMessage.query.filter_by(sender_id=id_a).count() == cnt_before)
    check('txt 附件未落库附件', MessageAttachment.query.count() == att_before)

# (5) 对话页渲染图片链接 /uploads/<随机文件名>
r = c.get(f'/messages/{id_b}')
html = r.get_data(as_text=True)
check('对话页 200 且含 /uploads/ 图片链接',
      r.status_code == 200 and all(f'/uploads/{fn}' in html for fn in created_files))

# 不存在用户 404
r = c.get('/messages/99999')
check('不存在用户 404', r.status_code == 404)

# 顶栏带私信入口（甲自己无未读；乙登录后应有徽章）
r = c.get('/forum/')
check('甲顶栏有私信入口', '/messages/' in r.get_data(as_text=True))

# 登出换乙
c.get('/auth/logout')
r = c.get('/auth/login')
tok = csrf_from(r.get_data(as_text=True))
c.post('/auth/login', data={'username': 'msgtest_b', 'password': 'passw0rd1', '_csrf_token': tok})

# 乙顶栏未读徽章 = 1
r = c.get('/forum/')
html = r.get_data(as_text=True)
check('乙顶栏有私信未读徽章', '✉️' in html and 'notif-badge' in html)

# 乙 inbox 列表
r = c.get('/messages/')
html = r.get_data(as_text=True)
check('乙 inbox 200', r.status_code == 200)
# 会话最新一条已是纯图消息，摘要不再含旧文本「你好呀」，只断言昵称；
# 摘要内容由下一条 [图片] 断言覆盖
check('inbox 含甲昵称', '测试甲' in html)
# (6) 会话最新一条是纯图消息 → inbox 摘要显示「[图片]」
check('inbox 纯图消息摘要为 [图片]', '[图片]' in html)

# 乙打开对话 → 未读清零
r = c.get(f'/messages/{id_a}')
html = r.get_data(as_text=True)
check('乙对话页 200 且含消息', r.status_code == 200 and '你好呀，这是测试私信' in html)
with app.app_context():
    check('未读已清零', PrivateMessage.query.filter_by(recipient_id=id_b, is_read=False).count() == 0)

# 乙回复甲
tok = csrf_from(html)
r = c.post(f'/messages/{id_a}', data={'content': '收到！回复你 [em:ksk-star]', '_csrf_token': tok})
check('乙回复 302', r.status_code == 302)

# 个人空间发私信按钮（甲的空间对乙可见）
r = c.get(f'/forum/user/{id_a}')
html = r.get_data(as_text=True)
check('他人空间显示发私信按钮', f'/messages/{id_a}' in html and '发私信' in html)
r = c.get(f'/forum/user/{id_b}')
check('自己空间不显示按钮', '发私信' not in r.get_data(as_text=True))

# 红线词拦截（如果本地词库有词，取第一个词验证）
try:
    with open('instance/sensitive_words_red.txt', encoding='utf-8') as f:
        word = next((ln.strip() for ln in f if ln.strip() and not ln.startswith('#')), None)
except FileNotFoundError:
    word = None
if word:
    r = c.get(f'/messages/{id_a}')
    tok = csrf_from(r.get_data(as_text=True))
    r = c.post(f'/messages/{id_a}', data={'content': f'包含{word}的内容', '_csrf_token': tok})
    with app.app_context():
        check('红线词未落库', PrivateMessage.query.filter_by(sender_id=id_b).count() == 1)
else:
    print('  SKIP 红线词验证（无本地词库）')

# ── inbox 会话排序 + 铃铛合并未读计数 + 通知页未读私信横幅 ──────────
# 当前登录态是乙；先换丙，丙 → 甲 连发 2 条未读（保证甲丙会话最新，排在甲乙之前）
c.get('/auth/logout')
r = c.get('/auth/login')
tok = csrf_from(r.get_data(as_text=True))
c.post('/auth/login', data={'username': 'msgtest_c', 'password': 'passw0rd1', '_csrf_token': tok})
for text in ('丙的第一条私信', '丙的第二条私信'):
    r = c.get(f'/messages/{id_a}')
    tok = csrf_from(r.get_data(as_text=True))
    r = c.post(f'/messages/{id_a}', data={'content': text, '_csrf_token': tok})
check('丙→甲 连发 2 条 302', r.status_code == 302)
with app.app_context():
    check('丙→甲 2 条未读落库', PrivateMessage.query.filter_by(
        sender_id=id_c, recipient_id=id_a, is_read=False).count() == 2)

# 登出换甲，看 inbox：甲丙、甲乙两个会话都在，且丙会话排在乙会话之前（丙的消息最新）
c.get('/auth/logout')
r = c.get('/auth/login')
tok = csrf_from(r.get_data(as_text=True))
c.post('/auth/login', data={'username': 'msgtest_a', 'password': 'passw0rd1', '_csrf_token': tok})
r = c.get('/messages/')
html = r.get_data(as_text=True)
check('甲 inbox 200', r.status_code == 200)
# 会话行链接形如 href="/messages/<对方id>"，带上引号与斜杠避免 id 前缀误匹配
pos_c = html.find(f'href="/messages/{id_c}"')
pos_b = html.find(f'href="/messages/{id_b}"')
check('inbox 含甲丙、甲乙两个会话', pos_c != -1 and pos_b != -1)
check('丙会话排在乙会话之前', pos_c != -1 and pos_b != -1 and pos_c < pos_b)

# 甲打开丙对话 → 丙的未读清零
r = c.get(f'/messages/{id_c}')
check('甲开丙对话 200', r.status_code == 200)
with app.app_context():
    check('丙的未读已清零', PrivateMessage.query.filter_by(
        sender_id=id_c, recipient_id=id_a, is_read=False).count() == 0)
    unread_from_b = PrivateMessage.query.filter_by(
        sender_id=id_b, recipient_id=id_a, is_read=False).count()

# 若前面流程把乙→甲的未读清了，补发一条，保证铃铛合并用例有 1 条未读私信
if unread_from_b == 0:
    c.get('/auth/logout')
    r = c.get('/auth/login')
    tok = csrf_from(r.get_data(as_text=True))
    c.post('/auth/login', data={'username': 'msgtest_b', 'password': 'passw0rd1', '_csrf_token': tok})
    r = c.get(f'/messages/{id_a}')
    tok = csrf_from(r.get_data(as_text=True))
    c.post(f'/messages/{id_a}', data={'content': '乙补发的未读私信', '_csrf_token': tok})
    c.get('/auth/logout')
    r = c.get('/auth/login')
    tok = csrf_from(r.get_data(as_text=True))
    c.post('/auth/login', data={'username': 'msgtest_a', 'password': 'passw0rd1', '_csrf_token': tok})

# ORM 直接造一条未读通知：甲发帖（种子板块首块），Notification(recipient=甲, actor=乙, kind='reply')
with app.app_context():
    board = Board.query.first()
    th = Thread(title='冒烟测试帖', content='铃铛合并计数用', board_id=board.id, user_id=id_a)
    db.session.add(th)
    db.session.flush()
    po = Post(content='首帖', thread_id=th.id, user_id=id_a)
    db.session.add(po)
    db.session.flush()
    nt = Notification(recipient_id=id_a, actor_id=id_b, thread_id=th.id, post_id=po.id,
                      kind='reply', is_read=False)
    db.session.add(nt)
    db.session.commit()
    tid, pid, nid = th.id, po.id, nt.id
    expected_unread = (
        Notification.query.filter_by(recipient_id=id_a, is_read=False).count()
        + PrivateMessage.query.filter_by(recipient_id=id_a, is_read=False).count()
    )
check('造通知后甲未读合计 = 2（1 通知 + 1 私信）', expected_unread == 2)

# 铃铛徽章 = 未读通知数 + 未读私信数（base.html 合并写法 total_unread）
r = c.get('/forum/')
html = r.get_data(as_text=True)
m_bell = re.search(r'href="[^"]*forum/notifications[^"]*"[^>]*>(.*?)</a>', html, re.S)
m_badge = re.search(r'notif-badge">\s*([^<]+?)\s*<', m_bell.group(1)) if m_bell else None
check('铃铛徽章正则提取成功', m_badge is not None)
check('铃铛徽章数字 = 通知+私信之和', m_badge is not None and m_badge.group(1) == str(expected_unread))

# 通知页：有未读私信时显示横幅链接 /messages/，文案含「未读私信」
# （注意顺序：本页会把通知标为已读，必须放在铃铛断言之后）
r = c.get('/forum/notifications')
html = r.get_data(as_text=True)
check('通知页 200', r.status_code == 200)
check('通知页含未读私信横幅', '未读私信' in html and '/messages/' in html)

# 删用户级联清理私信（不清理会让收件人未读徽章永远清不掉）
# 乙 → 甲 造一条未读，管理员删乙，验证私信随用户删除
c.get('/auth/logout')
r = c.get('/auth/login')
tok = csrf_from(r.get_data(as_text=True))
c.post('/auth/login', data={'username': 'msgtest_a', 'password': 'passw0rd1', '_csrf_token': tok})
r = c.get(f'/messages/{id_b}')
tok = csrf_from(r.get_data(as_text=True))
c.post(f'/messages/{id_b}', data={'content': '级联测试未读', '_csrf_token': tok})
with app.app_context():
    check('级联测试未读落库', PrivateMessage.query.filter_by(
        sender_id=id_a, recipient_id=id_b, is_read=False).count() == 1)
    # 记下乙收发的私信所挂的附件 id，删乙后应一并清理
    msg_ids_b = [m.id for m in PrivateMessage.query.filter(
        (PrivateMessage.sender_id == id_b) | (PrivateMessage.recipient_id == id_b)).all()]
    att_ids_b = [a.id for a in MessageAttachment.query.filter(
        MessageAttachment.message_id.in_(msg_ids_b)).all()] if msg_ids_b else []
c.get('/auth/logout')
r = c.get('/auth/login')
tok = csrf_from(r.get_data(as_text=True))
c.post('/auth/login', data={'username': 'msgtest_admin', 'password': 'passw0rd1', '_csrf_token': tok})
r = c.get('/admin/users')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/admin/user/{id_b}/delete', data={'_csrf_token': tok})
check('管理员删乙 302', r.status_code == 302)
with app.app_context():
    check('乙的私信已级联清理', PrivateMessage.query.filter(
        (PrivateMessage.sender_id == id_b) | (PrivateMessage.recipient_id == id_b)
    ).count() == 0)
    check('甲的未读私信数清零', PrivateMessage.query.filter_by(
        recipient_id=id_a, is_read=False).count() == 0)
    # (7) 乙私信上的图片附件行也随级联清理（磁盘文件按项目惯例保留，结尾由本用例自行删除）
    check('乙的图片附件已级联清理', bool(att_ids_b) and
          MessageAttachment.query.filter(MessageAttachment.id.in_(att_ids_b)).count() == 0)
    # 乙已删，后续清理只需删甲与管理员
    db.session.remove()

# 清理测试数据
with app.app_context():
    MessageAttachment.query.delete()
    PrivateMessage.query.delete()
    # 本批新增用例造的 通知/楼层/主题（通知可能已随删乙级联清掉，按 id 幂等删）
    Notification.query.filter(Notification.id == nid).delete(synchronize_session=False)
    Post.query.filter(Post.id == pid).delete(synchronize_session=False)
    Thread.query.filter(Thread.id == tid).delete(synchronize_session=False)
    User.query.filter(User.username.in_(['msgtest_a', 'msgtest_b', 'msgtest_c', 'msgtest_admin'])).delete(synchronize_session=False)
    db.session.commit()

# 删除本用例在 uploads/ 真实落盘的附件文件（只删 uuid.hex.ext 形且为本用例创建的文件）
for fn in created_files:
    if re.fullmatch(r'[0-9a-f]{32}\.(png|jpg|jpeg|gif|webp)', fn):
        p = os.path.join(app.config['UPLOAD_FOLDER'], fn)
        if os.path.isfile(p):
            os.remove(p)

print(f'\n结果：{passed} 通过 / {failed} 失败')
sys.exit(1 if failed else 0)
