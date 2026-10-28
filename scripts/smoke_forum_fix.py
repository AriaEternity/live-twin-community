# -*- coding: utf-8 -*-
"""论坛高优先级修复冒烟测试：临时数据库 + Flask test_client，全程不碰 instance/ygo.db。

覆盖：注册 → 发帖（失败回填）→ 回复（失败回填/成功跳最后一页锚点）→ 点赞 →
通知 → go 路由单条已读 → 删帖后通知页不 500 → 残留通知防御 →
找回密码（自助找回已下线：提示页渲染 / 重置路由 404）→ 昵称防仿冒 →
个人空间（双分页）→ 主题编辑（权限/锁定/CSRF）→ 论坛搜索（命中徽标/空态/限速）→
@提及通知 → 楼层引用 → 管理日志 → 板块排序与回复通知回归 →
头像上传（权限/CSRF/格式校验/裁剪落盘/展示/限速/只增不减）→
注册可选中文昵称 + B 站式昵称规则 → 站内公告（发布/首页弹窗/历史页/权限/日志）→
违禁词红线硬拦截（标题/正文/回复/注册昵称/空格拆解绕过/命中日志留痕）。
运行：python scripts/smoke_forum_fix.py
"""
import io
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

from PIL import Image

# 必须在 import app 之前注入：config 据此放行开发 SECRET_KEY，并把数据库指向临时文件
os.environ['FLASK_DEBUG'] = '1'
_tmp_db = os.path.join(tempfile.gettempdir(), 'ygo_smoke_forum_fix.db')
if os.path.exists(_tmp_db):
    os.remove(_tmp_db)
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmp_db

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app  # noqa: E402
app.config['ADMIN_USERNAME'] = 'synthetic_admin'  # 仅合成库中测试主管理员保护
from models import db, User, Thread, Post, Notification, ModerationLog, Announcement  # noqa: E402

# bjtime 过滤器由 app.py 的负责代理注册；测试环境里若尚未合入则本地兜底，保证模板可渲染
if 'bjtime' not in app.jinja_env.filters:
    def _bjtime(dt, fmt='%Y-%m-%d %H:%M'):
        if dt is None:
            return ''
        return (dt + timedelta(hours=8)).strftime(fmt)
    app.jinja_env.filters['bjtime'] = _bjtime

CSRF_RE = re.compile(r'name="_csrf_token" value="([0-9a-f]+)"')
_passed = []
_failed = []


def check(name, cond, detail=''):
    if cond:
        _passed.append(name)
        print(f'  [通过] {name}')
    else:
        _failed.append(name)
        print(f'  [失败] {name} {detail}')


def csrf(client, url):
    """GET 一个带表单的页面，从 HTML 里抠出该会话的 CSRF 令牌"""
    resp = client.get(url)
    html = resp.get_data(as_text=True)
    m = CSRF_RE.search(html)
    if not m:
        raise AssertionError(
            f'页面 {url} 未找到 CSRF 令牌：status={resp.status_code} '
            f'前200字符={html[:200]!r}')
    return m.group(1)


def register(client, username, email, password, ip=None, nickname=None):
    token = csrf(client, '/auth/register')
    data = {
        '_csrf_token': token, 'username': username, 'email': email,
        'password': password, 'confirm': password,
    }
    if nickname is not None:
        data['nickname'] = nickname
    return client.post('/auth/register', data=data,
                       environ_overrides={'REMOTE_ADDR': ip} if ip else None)


def login(client, username, password, next_page=None):
    url = '/auth/login' + (f'?next={next_page}' if next_page else '')
    token = csrf(client, '/auth/login')
    return client.post(url, data={
        '_csrf_token': token, 'username': username, 'password': password,
    })


def logout(client):
    return client.get('/auth/logout')


def make_image_bytes(fmt, size=(64, 64), mode='RGB', color=(238, 91, 166)):
    """Pillow 现场生成图片字节流（内存 BytesIO，不落盘）"""
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt)
    buf.seek(0)
    return buf


def upload_avatar(client, file_tuple, ip=None):
    """带 CSRF 的 POST /auth/avatar；file_tuple 为 (BytesIO, 文件名) 或 None"""
    token = csrf(client, '/auth/settings')
    data = {'_csrf_token': token}
    if file_tuple is not None:
        data['avatar'] = file_tuple
    return client.post('/auth/avatar', data=data,
                       content_type='multipart/form-data',
                       environ_overrides={'REMOTE_ADDR': ip} if ip else None)


PW = 'Zx9qwerty!0'  # 满足强度规则且不含用户名/邮箱前缀

# 注意：不要对多个 test_client 同时用 with（上下文保留会串登录态），普通客户端即可
ca, cb, cadmin = app.test_client(), app.test_client(), app.test_client()
if True:
    print('== 1. 注册三个账号：楼主 A / 回复者 B / 主管理员 ==')
    r = register(ca, 'smoke_user_a', 'smoke_a@example.com', PW)
    check('注册楼主 A', r.status_code == 302, f'status={r.status_code}')
    r = register(cb, 'smoke_user_b', 'smoke_b@example.com', PW)
    check('注册回复者 B', r.status_code == 302, f'status={r.status_code}')
    r = register(cadmin, 'synthetic_admin', 'smoke_admin@example.com', PW)
    check('注册主管理员', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        admin = User.query.filter_by(username='synthetic_admin').one()
        admin.is_admin = True
        db.session.commit()

    print('== 2. 发帖：校验失败回填 + 成功发帖 ==')
    # 标题过短 → 应重渲染（200）且保留已输入的标题与正文
    token = csrf(ca, '/forum/board/general/new')
    r = ca.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '短', 'content': '这是需要保留的正文内容',
    })
    body = r.get_data(as_text=True)
    check('发帖标题过短重渲染(200)', r.status_code == 200, f'status={r.status_code}')
    check('发帖失败保留标题', 'value="短"' in body)
    check('发帖失败保留正文', '这是需要保留的正文内容' in body)

    token = csrf(ca, '/forum/board/general/new')
    r = ca.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '双子卡组讨论帖', 'content': '一楼正文：直播双子太可爱了',
    })
    check('发帖成功跳转', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        t = Thread.query.filter_by(title='双子卡组讨论帖').first()
        assert t, '帖子未落库'
        tid = t.id
        op_post = t.posts.order_by(Post.created_at).first()

    print('== 3. 未登录：不渲染点赞表单、登录链接带 next ==')
    with app.test_client() as anon:
        body = anon.get(f'/forum/thread/{tid}').get_data(as_text=True)
        check('匿名不见点赞 POST 表单', f'/forum/post/{op_post.id}/like' not in body)
        check('匿名见「登录后点赞」链接', '登录后点赞' in body)
        check('点赞登录链接带 next 回跳', f'/auth/login?next=/forum/thread/{tid}' in body.replace('%2F', '/'))
        check('「请先登录后回复」带 next', body.count(f'/auth/login?next=/forum/thread/{tid}') >= 2
              or 'next=%2Fforum%2Fthread%2F' in body)

    print('== 4. 回复：失败回填 + 成功跳最后一页锚点 ==')
    # 内容过短 → 重渲染（200）且保留输入
    token = csrf(cb, f'/forum/thread/{tid}')
    r = cb.post(f'/forum/thread/{tid}/reply?page=1', data={
        '_csrf_token': token, 'content': '短',
    })
    body = r.get_data(as_text=True)
    check('回复过短重渲染(200)', r.status_code == 200, f'status={r.status_code}')
    check('回复失败保留输入', '>短</textarea>' in body)

    # 连续 20 条回复：连同首帖共 21 楼，第 20 条回复应落在第 2 页（每页 20 楼）
    last_location = None
    last_post_id = None
    for i in range(1, 21):
        token = csrf(cb, f'/forum/thread/{tid}')
        r = cb.post(f'/forum/thread/{tid}/reply?page=1', data={
            '_csrf_token': token, 'content': f'第 {i} 条回复内容占位',
        })
        assert r.status_code == 302, f'第 {i} 条回复失败 status={r.status_code}'
        last_location = r.headers['Location']
    with app.app_context():
        last_post = Post.query.filter_by(thread_id=tid) \
                              .order_by(Post.created_at.desc()).first()
        last_post_id = last_post.id
    check('回复成功跳到第 2 页', 'page=2' in last_location, f'Location={last_location}')
    check('跳转带新楼层锚点', last_location.endswith(f'#post-{last_post_id}'),
          f'Location={last_location}')

    print('== 5. 点赞（切换） ==')
    token = csrf(cb, f'/forum/thread/{tid}')
    r = cb.post(f'/forum/post/{op_post.id}/like', data={'_csrf_token': token},
                headers={'Referer': f'/forum/thread/{tid}?page=1'})
    check('点赞跳回来源页锚点', r.status_code == 302 and
          r.headers['Location'].endswith(f'#post-{op_post.id}'),
          f"Location={r.headers.get('Location')}")
    with app.app_context():
        check('点赞落库', op_post and Post.query.get(op_post.id).like_count == 1)

    print('== 6. 通知：列表 → go 路由单条已读 ==')
    r = ca.get('/forum/notifications')
    check('通知页 200', r.status_code == 200, f'status={r.status_code}')
    with app.app_context():
        a = User.query.filter_by(username='smoke_user_a').first()
        notif = Notification.query.filter_by(recipient_id=a.id, is_read=False) \
                                  .order_by(Notification.created_at.desc()).first()
        assert notif, '未产生通知'
        nid = notif.id
    # go 路由：标记已读并 302 到帖子页 + 锚点
    r = ca.get(f'/forum/notification/{nid}/go')
    check('go 路由 302', r.status_code == 302, f'status={r.status_code}')
    check('go 路由跳到帖子页锚点', f'/forum/thread/{tid}' in r.headers['Location']
          and '#post-' in r.headers['Location'], f"Location={r.headers.get('Location')}")
    with app.app_context():
        check('go 路由已置已读', Notification.query.get(nid).is_read is True)
    # 归属校验：B 访问 A 的通知应 404
    r = cb.get(f'/forum/notification/{nid}/go')
    check('go 路由越权 404', r.status_code == 404, f'status={r.status_code}')

    print('== 7. 残留通知防御：手动造孤儿通知，通知页不 500 且顺手清理 ==')
    with app.app_context():
        b = User.query.filter_by(username='smoke_user_b').first()
        a = User.query.filter_by(username='smoke_user_a').first()
        # 造一条指向真实楼层的新通知，再绕过路由直接删该楼层 → 孤儿通知
        victim = Post(content='即将被删的楼层', thread_id=tid, user_id=b.id)
        db.session.add(victim)
        db.session.flush()
        orphan = Notification(recipient_id=a.id, actor_id=b.id,
                              thread_id=tid, post_id=victim.id)
        db.session.add(orphan)
        db.session.commit()
        orphan_id = orphan.id
        db.session.delete(victim)
        db.session.commit()
    r = ca.get('/forum/notifications')
    check('含孤儿通知的通知页 200', r.status_code == 200, f'status={r.status_code}')
    with app.app_context():
        check('孤儿通知被顺手删除', Notification.query.get(orphan_id) is None)

    print('== 8. 管理员删帖级联清理通知 ==')
    with app.app_context():
        before = Notification.query.filter_by(thread_id=tid).count()
    check('删帖前存在该帖通知', before > 0, f'count={before}')
    token = csrf(cadmin, '/admin/threads')
    r = cadmin.post(f'/admin/thread/{tid}/delete', data={'_csrf_token': token})
    check('管理员删帖 302', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('该帖通知已级联清理', Notification.query.filter_by(thread_id=tid).count() == 0)
        check('帖子已删除', Thread.query.get(tid) is None)
    r = ca.get('/forum/notifications')
    check('删帖后通知页不 500', r.status_code == 200, f'status={r.status_code}')

    print('== 9. 管理员删用户级联清理通知 ==')
    with app.app_context():
        # 再造一帖：A 发帖，B 回复 → 产生给 A 的通知（actor=B）
        a = User.query.filter_by(username='smoke_user_a').first()
        b = User.query.filter_by(username='smoke_user_b').first()
        t2 = Thread(title='第二个讨论帖', content='内容占位内容占位', board_id=1, user_id=a.id)
        db.session.add(t2)
        db.session.flush()
        p1 = Post(content='内容占位内容占位', thread_id=t2.id, user_id=a.id)
        p2 = Post(content='B 的回复内容占位', thread_id=t2.id, user_id=b.id)
        db.session.add_all([p1, p2])
        db.session.flush()
        n2 = Notification(recipient_id=a.id, actor_id=b.id, thread_id=t2.id, post_id=p2.id)
        db.session.add(n2)
        db.session.commit()
        t2_id, b_id, n2_id = t2.id, b.id, n2.id
    token = csrf(cadmin, '/admin/users')
    r = cadmin.post(f'/admin/user/{b_id}/delete', data={'_csrf_token': token})
    check('管理员删用户 302', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('被删用户触发的通知已清理', Notification.query.get(n2_id) is None)
        check('被删用户的回复已清理', Post.query.filter_by(user_id=b_id).count() == 0)
    r = ca.get('/forum/notifications')
    check('删用户后通知页不 500', r.status_code == 200, f'status={r.status_code}')

    print('== 10. bjtime 过滤器渲染（帖子页/板块页/管理页均含时间） ==')
    r = ca.get(f'/forum/thread/{t2_id}')
    check('帖子页 200（bjtime 渲染）', r.status_code == 200, f'status={r.status_code}')
    check('帖子页时间含年份', re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', r.get_data(as_text=True)) is not None)
    r = ca.get('/forum/board/general')
    check('板块页 200 且时间补上年份', r.status_code == 200 and
          re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', r.get_data(as_text=True)) is not None)
    r = cadmin.get('/admin/')
    check('管理面板 200', r.status_code == 200, f'status={r.status_code}')

    print('== 11. 找回密码：自助找回已下线，页面提示联系站长 ==')
    cf = app.test_client()
    r = cf.get('/auth/forgot')
    body = r.get_data(as_text=True)
    check('找回页 GET 200', r.status_code == 200, f'status={r.status_code}')
    check('找回页提示联系当前部署维护者', '当前部署的维护者' in body)
    check('找回页不再有自助表单', 'name="username"' not in body and 'name="email"' not in body)
    r = cf.get('/auth/login')
    check('登录页 GET 200（忘记密码入口仍在）', r.status_code == 200, f'status={r.status_code}')
    check('登录页仍有忘记密码链接', '/auth/forgot' in r.get_data(as_text=True))

    print('== 12. 重置路由已下线：/auth/reset/<token> 一律不可用 ==')
    r = cf.get('/auth/reset/whatever.token.here')
    check('重置路由 GET 404', r.status_code == 404, f'status={r.status_code}')
    r = cf.post('/auth/reset/whatever.token.here', data={})
    check('重置路由 POST 也不可用', r.status_code in (400, 403, 404), f'status={r.status_code}')

    print('== 13. 昵称防仿冒：撞他人昵称/账号名（含大小写变体）一律拒绝 ==')
    # B 在第 9 节已被管理员删除，另注册新用户 D 扮演「他人」
    cd = app.test_client()
    r = register(cd, 'smoke_user_d', 'smoke_d@example.com', PW)
    check('注册他人 D', r.status_code == 302, f'status={r.status_code}')
    # A 先占一个 ASCII 昵称（中文昵称无法用于测试用户名撞车，因为用户名规则只允许字母数字下划线）
    token = csrf(ca, '/auth/settings')
    r = ca.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': 'starlight7', 'avatar_url': '', 'signature': '',
    })
    check('A 设置昵称成功(302)', r.status_code == 302, f'status={r.status_code}')
    # D 撞 A 的昵称 → 拒绝
    token = csrf(cd, '/auth/settings')
    r = cd.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': 'starlight7', 'avatar_url': '', 'signature': '',
    })
    body = r.get_data(as_text=True)
    check('D 撞 A 昵称被拒(200)', r.status_code == 200, f'status={r.status_code}')
    check('撞昵称给出提示', '该昵称已被他人使用' in body)
    # D 用大小写变体撞 → 同样拒绝
    token = csrf(cd, '/auth/settings')
    r = cd.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': 'STARLIGHT7', 'avatar_url': '', 'signature': '',
    })
    check('大小写变体也被拒', '该昵称已被他人使用' in r.get_data(as_text=True))
    # D 用 A 的账号名做昵称 → 拒绝（A 没设昵称时展示的就是账号名）
    token = csrf(cd, '/auth/settings')
    r = cd.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': 'smoke_user_a', 'avatar_url': '', 'signature': '',
    })
    check('用他人账号名做昵称被拒', '该昵称已被他人使用' in r.get_data(as_text=True))
    # D 用主管理员账号名做昵称 → 拒绝（防冒充管理员）
    token = csrf(cd, '/auth/settings')
    r = cd.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': 'synthetic_admin', 'avatar_url': '', 'signature': '',
    })
    check('用管理员账号名做昵称被拒', '该昵称已被他人使用' in r.get_data(as_text=True))
    # A 保持自己的昵称不变 → 不算撞名，成功
    token = csrf(ca, '/auth/settings')
    r = ca.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': 'starlight7', 'avatar_url': '', 'signature': '',
    })
    check('A 保持自己昵称成功(302)', r.status_code == 302, f'status={r.status_code}')
    # 注册用户名撞 A 的昵称 → 拒绝（用户名会成为初始展示名）
    cnick = app.test_client()
    r = register(cnick, 'starlight7', 'starlight7@example.com', PW)
    check('注册用户名撞昵称被拒(200)', r.status_code == 200, f'status={r.status_code}')
    check('注册撞昵称给出提示', '该用户名与他人的昵称重复' in r.get_data(as_text=True))

    # ── 以下为论坛新功能冒烟（个人空间/主题编辑/搜索/@提及/引用/管理日志/排序） ──
    # 注册限速 5/min：127.0.0.1 的配额已被上面 5 次注册用满，新注册一律走独立 IP；
    # 发帖(10/h)/回复(30/h)同理用独立 IP，避免与上面 127.0.0.1 的存量计数叠加。

    print('== 18. 个人空间：资料 + 双列表分页 + 不存在用户 404 ==')
    c_h = app.test_client()
    r = register(c_h, 'smoke_user_h', 'smoke_h@example.com', PW, ip='10.40.1.1')
    check('注册用户 H', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_h, '/forum/board/general/new')
    r = c_h.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '空间验证主题ZUP', 'content': '空间验证正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.3'})
    check('H 发帖成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        # 直接插库凑页数：15 个填充主题 + 31 条回复（连同路由发的 1 帖共 16 主题）
        h = User.query.filter_by(username='smoke_user_h').first()
        h_id = h.id
        zup = Thread.query.filter_by(title='空间验证主题ZUP').first()
        for i in range(15):
            db.session.add(Thread(title=f'空间填充主题{i:02d}', content='空间填充正文内容',
                                  board_id=zup.board_id, user_id=h_id))
        for i in range(31):
            db.session.add(Post(content=f'空间填充回复{i:02d}',
                                thread_id=zup.id, user_id=h_id))
        db.session.commit()
    r = c_h.get(f'/forum/user/{h_id}')
    body = r.get_data(as_text=True)
    check('个人空间 200', r.status_code == 200, f'status={r.status_code}')
    check('空间页显示账号名', '@smoke_user_h' in body)
    # H 共 32 条 Post（1 首帖 + 31 回复）：回复列表排除首帖后应为 31
    check('空间主题总数 16', '（16）' in body)
    check('空间回复总数 31（已排除首帖）', '（31）' in body)
    # 双分页：主题 16 → 2 页，回复 31 → 3 页；最旧的主题（ZUP）落在主题第 2 页
    r = c_h.get(f'/forum/user/{h_id}?page_t=2&page_p=3')
    body = r.get_data(as_text=True)
    check('双分页请求 200', r.status_code == 200, f'status={r.status_code}')
    check('page_t=2 翻到主题第 2 页', '2 / 2' in body)
    check('page_p=3 翻到回复第 3 页', '3 / 3' in body)
    check('主题第 2 页含最旧主题', '空间验证主题ZUP' in body)
    r = c_h.get('/forum/user/999999')
    check('不存在的用户 404', r.status_code == 404, f'status={r.status_code}')

    print('== 19. 主题编辑：权限 + 表单回填 + 锁定限制 + CSRF ==')
    c_e = app.test_client()
    r = register(c_e, 'smoke_user_e', 'smoke_e@example.com', PW, ip='10.40.1.2')
    check('注册用户 E', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_e, '/forum/board/general/new')
    r = c_e.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '主题编辑测试帖ZED', 'content': '编辑前的正文内容ZEDB1',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.1'})
    check('E 发帖成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        te_id = Thread.query.filter_by(title='主题编辑测试帖ZED').first().id
    with app.test_client() as anon2:
        r = anon2.get(f'/forum/thread/{te_id}/edit')
        check('未登录编辑跳登录(302)', r.status_code == 302 and
              '/auth/login' in r.headers.get('Location', ''),
              f"status={r.status_code} Location={r.headers.get('Location')}")
    r = cd.get(f'/forum/thread/{te_id}/edit')
    check('非作者非管理员编辑 403', r.status_code == 403, f'status={r.status_code}')
    r = c_e.get(f'/forum/thread/{te_id}/edit')
    body = r.get_data(as_text=True)
    check('作者编辑页 200', r.status_code == 200, f'status={r.status_code}')
    check('编辑页回填原标题', 'value="主题编辑测试帖ZED"' in body)
    check('编辑页回填原内容', '编辑前的正文内容ZEDB1' in body)
    r = c_e.post(f'/forum/thread/{te_id}/edit', data={
        'title': '无令牌提交标题', 'content': '无令牌提交内容',
    })
    check('缺 CSRF 的编辑 POST 被拒(403)', r.status_code == 403, f'status={r.status_code}')
    token = csrf(c_e, f'/forum/thread/{te_id}/edit')
    r = c_e.post(f'/forum/thread/{te_id}/edit', data={
        '_csrf_token': token, 'title': '主题编辑测试帖ZED2', 'content': '编辑后的正文内容ZEDB2',
    })
    check('作者编辑成功(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        te = Thread.query.get(te_id)
        check('编辑后标题已落库', te.title == '主题编辑测试帖ZED2', f'title={te.title}')
        first = te.posts.order_by(Post.created_at).first()
        check('首帖正文同步更新', first.content == '编辑后的正文内容ZEDB2')
        check('首帖 edited_at 已置位', first.edited_at is not None)
    r = c_e.get(f'/forum/thread/{te_id}')
    body = r.get_data(as_text=True)
    check('帖子页显示新标题', '主题编辑测试帖ZED2' in body)
    check('帖子页显示「最后修改于」', '最后修改于' in body)
    token = csrf(cadmin, f'/forum/thread/{te_id}')
    r = cadmin.post(f'/forum/thread/{te_id}/lock', data={'_csrf_token': token})
    check('管理员锁定该帖(302)', r.status_code == 302, f'status={r.status_code}')
    r = c_e.get(f'/forum/thread/{te_id}/edit')
    check('锁定帖作者编辑被重定向', r.status_code == 302 and
          f'/forum/thread/{te_id}' in r.headers.get('Location', ''),
          f"status={r.status_code} Location={r.headers.get('Location')}")
    r = cadmin.get(f'/forum/thread/{te_id}/edit')
    check('锁定帖管理员可编辑(200)', r.status_code == 200, f'status={r.status_code}')
    token = csrf(cadmin, f'/forum/thread/{te_id}/edit')
    r = cadmin.post(f'/forum/thread/{te_id}/edit', data={
        '_csrf_token': token, 'title': '管理员修订标题ZED3', 'content': '管理员修订正文内容',
    })
    check('管理员编辑锁定帖(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('管理员改题已落库', Thread.query.get(te_id).title == '管理员修订标题ZED3')
    token = csrf(cadmin, f'/forum/thread/{te_id}')
    r = cadmin.post(f'/forum/thread/{te_id}/lock', data={'_csrf_token': token})
    check('管理员解锁该帖(302)', r.status_code == 302, f'status={r.status_code}')

    print('== 20. 论坛搜索：标题/回复命中徽标 + 空态 + 限速 ==')
    token = csrf(cd, '/forum/board/general/new')
    r = cd.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '搜索验证标题ZQTITLE', 'content': '搜索验证正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.2'})
    check('搜索验证帖发帖成功', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_e, f'/forum/thread/{te_id}')
    r = c_e.post(f'/forum/thread/{te_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '回复里的独占关键词ZQREPLY 占位',
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.1'})
    check('回复命中验证回复成功', r.status_code == 302, f'status={r.status_code}')
    cs = app.test_client()
    r = cs.get('/forum/search?q=x')
    check('关键词不足 2 字符重定向', r.status_code == 302 and
          '/forum' in r.headers.get('Location', ''),
          f"status={r.status_code} Location={r.headers.get('Location')}")
    r = cs.get('/forum/search?q=ZQTITLE')
    body = r.get_data(as_text=True)
    check('标题命中搜索 200', r.status_code == 200, f'status={r.status_code}')
    check('标题命中显示该主题', '搜索验证标题ZQTITLE' in body)
    check('标题命中徽标出现', '标题/正文命中' in body)
    r = cs.get('/forum/search?q=ZQREPLY')
    body = r.get_data(as_text=True)
    check('回复命中搜索 200', r.status_code == 200, f'status={r.status_code}')
    check('回复命中显示所在主题', '管理员修订标题ZED3' in body)
    check('回复命中徽标出现', '回复命中' in body)
    check('纯回复命中不带标题徽标', '标题/正文命中' not in body)
    r = cs.get('/forum/search?q=ZXNORESULT')
    body = r.get_data(as_text=True)
    check('无结果搜索 200', r.status_code == 200, f'status={r.status_code}')
    check('无结果空态文案', '没有找到' in body)
    # 搜索限速 30/min：独立 IP 瞬发 31 次（q<2 的重定向不计数，故用合法关键词）
    rl_statuses = [cs.get('/forum/search?q=smoke',
                          environ_overrides={'REMOTE_ADDR': '10.9.9.31'}).status_code
                   for _ in range(31)]
    check('搜索前 30 次未限速', rl_statuses[:30] == [200] * 30,
          f'statuses={rl_statuses}')
    check('搜索第 31 次 429', rl_statuses[30] == 429, f'statuses={rl_statuses}')

    print('== 21. @提及：通知生成 / 本人与不存在用户跳过 / 与回复通知去重 ==')
    c_f = app.test_client()
    r = register(c_f, 'smoke_user_f', 'smoke_f@example.com', PW, ip='10.40.1.3')
    check('注册用户 F', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_e, '/forum/board/general/new')
    r = c_e.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '提及引用测试帖ZMT', 'content': '提及测试正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.1'})
    check('提及测试帖发帖成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        tm_id = Thread.query.filter_by(title='提及引用测试帖ZMT').first().id
        f_id = User.query.filter_by(username='smoke_user_f').first().id
        d_id = User.query.filter_by(username='smoke_user_d').first().id
    token = csrf(c_f, f'/forum/thread/{tm_id}')
    r = c_f.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': 'F 的楼层内容QUOTEME',
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.2'})
    check('F 回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        f_post_id = Post.query.filter_by(thread_id=tm_id, user_id=f_id).first().id
    token = csrf(cd, f'/forum/thread/{tm_id}')
    r = cd.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '回复 @smoke_user_f 快来看ZMN1',
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.3'})
    check('@他人回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        mn1 = Post.query.filter(Post.thread_id == tm_id,
                                Post.content.like('%ZMN1%')).first()
        check('@他人产生 mention 通知',
              Notification.query.filter_by(recipient_id=f_id, post_id=mn1.id,
                                           kind='mention').first() is not None)
    token = csrf(cd, f'/forum/thread/{tm_id}')
    r = cd.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '@smoke_user_d 自己提自己ZMN2',
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.3'})
    check('@本人回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('@本人不产生 mention 通知',
              Notification.query.filter_by(recipient_id=d_id, kind='mention').count() == 0)
    with app.app_context():
        mention_total = Notification.query.filter_by(kind='mention').count()
    token = csrf(cd, f'/forum/thread/{tm_id}')
    r = cd.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '@ghost_user_zz 不存在的人ZMN3',
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.3'})
    check('@不存在用户回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('@不存在用户不产生 mention 通知',
              Notification.query.filter_by(kind='mention').count() == mention_total)
    token = csrf(cd, f'/forum/thread/{tm_id}')
    r = cd.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '回复并 @smoke_user_f 去重验证ZMN4',
        'parent_post_id': str(f_post_id),
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.3'})
    check('回复+@ 提交成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        mn4 = Post.query.filter(Post.thread_id == tm_id,
                                Post.content.like('%ZMN4%')).first()
        notifs = Notification.query.filter_by(recipient_id=f_id, post_id=mn4.id).all()
        check('回复+@ 同一楼层只产生一条通知', len(notifs) == 1, f'count={len(notifs)}')
        check('该通知为 reply 而非重复 mention', notifs[0].kind == 'reply',
              f'kind={notifs[0].kind}')
    r = c_f.get('/forum/notifications')
    body = r.get_data(as_text=True)
    check('F 的通知页 200', r.status_code == 200, f'status={r.status_code}')
    check('通知页显示「提到了你」', '提到了你' in body)

    print('== 22. 楼层引用：落库渲染 / 跨帖静默忽略 / 引用已删楼层 ==')
    token = csrf(c_e, f'/forum/thread/{tm_id}')
    r = c_e.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '引用回复内容ZQT1',
        'quote_post_id': str(f_post_id),
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.1'})
    check('带引用回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        qp1 = Post.query.filter(Post.thread_id == tm_id,
                                Post.content.like('%ZQT1%')).first()
        check('引用关系落库', qp1.quote_post_id == f_post_id,
              f'quote_post_id={qp1.quote_post_id}')
    r = c_e.get(f'/forum/thread/{tm_id}')
    body = r.get_data(as_text=True)
    check('帖子页渲染引用块', 'class="quote-block"' in body)
    check('引用块含被引内容', 'QUOTEME' in body)
    with app.app_context():
        # 另一个帖子的首帖（第 9 节遗留的 t2），用作跨帖引用对象
        other_first_id = Post.query.filter_by(thread_id=t2_id) \
                                   .order_by(Post.id).first().id
    token = csrf(c_e, f'/forum/thread/{tm_id}')
    r = c_e.post(f'/forum/thread/{tm_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '跨帖引用应被忽略ZQT2',
        'quote_post_id': str(other_first_id),
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.1'})
    check('跨帖引用回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        qp2 = Post.query.filter(Post.thread_id == tm_id,
                                Post.content.like('%ZQT2%')).first()
        check('跨帖引用被静默忽略(quote 为 None)', qp2.quote_post_id is None,
              f'quote_post_id={qp2.quote_post_id}')
    token = csrf(cadmin, f'/forum/thread/{tm_id}')
    r = cadmin.post(f'/forum/post/{f_post_id}/delete', data={'_csrf_token': token})
    check('管理员删除被引楼层(302)', r.status_code == 302, f'status={r.status_code}')
    r = c_e.get(f'/forum/thread/{tm_id}')
    check('引用已删楼层后帖子页仍 200', r.status_code == 200, f'status={r.status_code}')

    print('== 23. 管理日志：各动作落库 + 日志页访问与筛选 ==')
    token = csrf(cadmin, f'/forum/thread/{tm_id}')
    r = cadmin.post(f'/forum/thread/{tm_id}/pin', data={'_csrf_token': token})
    check('管理员置顶(302)', r.status_code == 302, f'status={r.status_code}')
    token = csrf(cadmin, f'/forum/thread/{tm_id}')
    r = cadmin.post(f'/forum/thread/{tm_id}/lock', data={'_csrf_token': token})
    check('管理员锁定(302)', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_e, '/forum/board/general/new')
    r = c_e.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '待删除验证帖ZDEL', 'content': '待删除帖正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.1'})
    check('待删除帖发帖成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        tdel_id = Thread.query.filter_by(title='待删除验证帖ZDEL').first().id
    token = csrf(cadmin, f'/forum/thread/{tdel_id}')
    r = cadmin.post(f'/forum/thread/{tdel_id}/delete', data={'_csrf_token': token})
    check('管理员论坛侧删整帖(302)', r.status_code == 302, f'status={r.status_code}')
    c_g = app.test_client()
    r = register(c_g, 'smoke_user_g', 'smoke_g@example.com', PW, ip='10.40.1.4')
    check('注册用户 G', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        g_id = User.query.filter_by(username='smoke_user_g').first().id
    token = csrf(cadmin, '/admin/users')
    r = cadmin.post(f'/admin/user/{g_id}/toggle-admin', data={'_csrf_token': token})
    check('授予管理(302)', r.status_code == 302, f'status={r.status_code}')
    token = csrf(cadmin, '/admin/users')
    r = cadmin.post(f'/admin/user/{g_id}/delete', data={'_csrf_token': token})
    check('删除用户(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        log = ModerationLog.query.filter_by(action='pin', target_type='thread',
                                            target_id=tm_id).first()
        check('pin 日志落库且摘要正确',
              log is not None and log.detail == '提及引用测试帖ZMT',
              f'detail={log.detail if log else None}')
        log = ModerationLog.query.filter_by(action='lock', target_type='thread',
                                            target_id=tm_id).first()
        check('lock 日志落库且摘要正确',
              log is not None and log.detail == '提及引用测试帖ZMT',
              f'detail={log.detail if log else None}')
        log = ModerationLog.query.filter_by(action='delete_post', target_type='post',
                                            target_id=f_post_id).first()
        check('delete_post 日志落库且摘要正确',
              log is not None and log.detail == 'F 的楼层内容QUOTEME',
              f'detail={log.detail if log else None}')
        log = ModerationLog.query.filter_by(action='delete_thread', target_type='thread',
                                            target_id=tdel_id).first()
        check('delete_thread 日志落库且摘要正确',
              log is not None and log.detail == '待删除验证帖ZDEL',
              f'detail={log.detail if log else None}')
        log = ModerationLog.query.filter_by(action='grant_admin', target_type='user',
                                            target_id=g_id).first()
        check('grant_admin 日志落库且摘要正确',
              log is not None and log.detail == 'smoke_user_g',
              f'detail={log.detail if log else None}')
        log = ModerationLog.query.filter_by(action='delete_user', target_type='user',
                                            target_id=g_id).first()
        check('delete_user 日志落库且摘要正确',
              log is not None and log.detail == 'smoke_user_g',
              f'detail={log.detail if log else None}')
    r = cadmin.get('/admin/logs')
    body = r.get_data(as_text=True)
    check('日志页管理员 200', r.status_code == 200, f'status={r.status_code}')
    check('日志页含操作记录', '待删除验证帖ZDEL' in body)
    r = c_e.get('/admin/logs')
    check('日志页非管理员被拦(403)', r.status_code == 403, f'status={r.status_code}')
    r = cadmin.get('/admin/logs?action=pin')
    body = r.get_data(as_text=True)
    check('action 筛选页 200', r.status_code == 200, f'status={r.status_code}')
    check('筛选后含 pin 记录', '提及引用测试帖ZMT' in body)
    check('筛选后不含删帖记录', '待删除验证帖ZDEL' not in body)
    r = cadmin.get('/admin/logs?action=hack')
    body = r.get_data(as_text=True)
    check('非法 action 被忽略(200)', r.status_code == 200, f'status={r.status_code}')
    check('非法 action 仍显示全部记录',
          '待删除验证帖ZDEL' in body and '提及引用测试帖ZMT' in body)

    print('== 24. 板块排序：4 种 sort + bump + 置顶优先 + 回复通知回归 ==')
    # 用尚无一帖的 deck 板块，排序断言不受其他小节数据干扰
    token = csrf(c_e, '/forum/board/deck/new')
    r = c_e.post('/forum/board/deck/new', data={
        '_csrf_token': token, 'title': '排序验证旧帖ZOLD', 'content': '排序旧帖正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.1'})
    check('旧帖发帖成功', r.status_code == 302, f'status={r.status_code}')
    token = csrf(cd, '/forum/board/deck/new')
    r = cd.post('/forum/board/deck/new', data={
        '_csrf_token': token, 'title': '排序验证新帖ZNEW', 'content': '排序新帖正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.2'})
    check('新帖发帖成功', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_e, '/forum/board/deck/new')
    r = c_e.post('/forum/board/deck/new', data={
        '_csrf_token': token, 'title': '排序验证置顶帖ZPIN', 'content': '排序置顶帖正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.1.1'})
    check('置顶候选帖发帖成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        t_old_id = Thread.query.filter_by(title='排序验证旧帖ZOLD').first().id
        t_pin_id = Thread.query.filter_by(title='排序验证置顶帖ZPIN').first().id
        e_id = User.query.filter_by(username='smoke_user_e').first().id
    token = csrf(cadmin, f'/forum/thread/{t_pin_id}')
    r = cadmin.post(f'/forum/thread/{t_pin_id}/pin', data={'_csrf_token': token})
    check('管理员置顶 ZPIN(302)', r.status_code == 302, f'status={r.status_code}')
    # D 回复旧帖 → 旧帖被顶起；同时回归验证楼主 E 收到原有 reply 通知
    token = csrf(cd, f'/forum/thread/{t_old_id}')
    r = cd.post(f'/forum/thread/{t_old_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '把旧帖顶起来ZBUMP',
    }, environ_overrides={'REMOTE_ADDR': '10.60.1.3'})
    check('bump 回复成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        bump = Post.query.filter(Post.thread_id == t_old_id,
                                 Post.content.like('%ZBUMP%')).first()
        check('回复通知回归：楼主收到 reply 通知',
              Notification.query.filter_by(recipient_id=e_id, post_id=bump.id,
                                           kind='reply').first() is not None)
    r = c_e.get('/forum/board/deck?sort=active')
    body = r.get_data(as_text=True)
    check('sort=active 200', r.status_code == 200, f'status={r.status_code}')
    check('active：新回复把旧帖顶到新帖前', body.index('ZOLD') < body.index('ZNEW'))
    check('active：置顶帖在最前', body.index('ZPIN') < body.index('ZOLD'))
    r = c_e.get('/forum/board/deck?sort=new')
    body = r.get_data(as_text=True)
    check('sort=new 200', r.status_code == 200, f'status={r.status_code}')
    check('new：新帖排在旧帖前', body.index('ZNEW') < body.index('ZOLD'))
    check('new：置顶帖在最前', body.index('ZPIN') < body.index('ZNEW'))
    for s in ('replies', 'views'):
        r = c_e.get(f'/forum/board/deck?sort={s}')
        body = r.get_data(as_text=True)
        check(f'sort={s} 200', r.status_code == 200, f'status={r.status_code}')
        check(f'sort={s}：置顶帖在最前',
              body.index('ZPIN') < body.index('ZOLD')
              and body.index('ZPIN') < body.index('ZNEW'))
    r = c_e.get('/forum/board/deck?sort=nonsense')
    body = r.get_data(as_text=True)
    check('非法 sort 回退不报错(200)', r.status_code == 200, f'status={r.status_code}')
    check('非法 sort 回退为 active 排序', body.index('ZOLD') < body.index('ZNEW'))

    print('== 25. 头像上传：权限/CSRF/格式校验/裁剪落盘/页面展示/限速/只增不减 ==')
    # 上传写到真实 uploads/avatars/（routes/main.py 的服务路径硬编码项目根 uploads/，
    # 且限速按 IP 计费无法绕开），本节末尾按「目录差集」清理全部测试产物
    _avatar_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'avatars')
    _avatar_dir_existed = os.path.isdir(_avatar_dir)
    _avatar_before = set(os.listdir(_avatar_dir)) if _avatar_dir_existed else set()
    _AVATAR_URL_RE = r'/uploads/avatars/[0-9a-f]{32}\.jpg'

    c_k = app.test_client()
    r = register(c_k, 'smoke_user_k', 'smoke_k@example.com', PW, ip='10.40.2.1')
    check('注册用户 K（头像用户）', r.status_code == 302, f'status={r.status_code}')
    c_l = app.test_client()
    r = register(c_l, 'smoke_user_l', 'smoke_l@example.com', PW, ip='10.40.2.2')
    check('注册用户 L（无头像对照）', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        k_id = User.query.filter_by(username='smoke_user_k').first().id

    # 1) 未登录：带合法 CSRF 的 POST 应被 login_required 302 到登录页
    with app.test_client() as anon3:
        token = csrf(anon3, '/auth/login')
        r = anon3.post('/auth/avatar', data={'_csrf_token': token},
                       content_type='multipart/form-data')
        check('未登录上传头像跳登录(302)', r.status_code == 302 and
              '/auth/login' in r.headers.get('Location', ''),
              f"status={r.status_code} Location={r.headers.get('Location')}")

    # 2) 缺 CSRF：已登录 POST 不带令牌 → 全局钩子 403
    r = c_k.post('/auth/avatar',
                 data={'avatar': (make_image_bytes('PNG'), 'no_csrf.png')},
                 content_type='multipart/form-data')
    check('缺 CSRF 的头像上传被拒(403)', r.status_code == 403, f'status={r.status_code}')

    # 3) 正路径：1024×768 PNG → 302，落库 /uploads/avatars/*.jpg，产物 256×256 JPEG
    r = upload_avatar(c_k, (make_image_bytes('PNG', size=(1024, 768)), 'photo.png'))
    check('上传 1024×768 PNG 302 回设置页', r.status_code == 302 and
          '/auth/settings' in r.headers.get('Location', ''),
          f"status={r.status_code} Location={r.headers.get('Location')}")
    with app.app_context():
        url1 = User.query.get(k_id).avatar_url
    check('avatar_url 形如 /uploads/avatars/<32hex>.jpg',
          re.fullmatch(_AVATAR_URL_RE, url1 or '') is not None,
          f'avatar_url={url1}')
    path1 = os.path.join(_avatar_dir, url1.rsplit('/', 1)[-1])
    check('头像产物文件已落盘', os.path.isfile(path1), f'path={path1}')
    with Image.open(path1) as im:
        im.load()
        check('产物尺寸 256×256', im.size == (256, 256), f'size={im.size}')
        check('产物格式 JPEG', im.format == 'JPEG', f'format={im.format}')
    r = c_k.get(url1)
    check('头像 URL 可直接 GET(200)', r.status_code == 200, f'status={r.status_code}')

    # 4) RGBA PNG（带透明区）→ 白底合成后转 JPEG 不炸
    r = upload_avatar(c_k, (make_image_bytes('PNG', size=(300, 300), mode='RGBA',
                                             color=(255, 0, 0, 0)), 'alpha.png'))
    check('上传 RGBA PNG 302', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        url2 = User.query.get(k_id).avatar_url
    check('RGBA 上传后 avatar_url 已更新',
          re.fullmatch(_AVATAR_URL_RE, url2 or '') is not None and url2 != url1,
          f'avatar_url={url2}')
    path2 = os.path.join(_avatar_dir, url2.rsplit('/', 1)[-1])
    with Image.open(path2) as im:
        im.load()
        check('RGBA 产物正常解码为 256×256 JPEG',
              im.format == 'JPEG' and im.size == (256, 256),
              f'format={im.format} size={im.size}')

    # 5) txt 字节冒充 .png → flash 拒绝且 avatar_url 不变（跟随重定向看提示）
    token = csrf(c_k, '/auth/settings')
    r = c_k.post('/auth/avatar', data={
        '_csrf_token': token,
        'avatar': (io.BytesIO(b'this is plain text, not an image'), 'fake.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    body = r.get_data(as_text=True)
    check('txt 冒充 png 重渲染设置页(200)', r.status_code == 200,
          f'status={r.status_code}')
    check('txt 冒充 png 给出拒绝提示', '请上传有效的图片文件' in body)
    with app.app_context():
        check('拒绝后 avatar_url 未变', User.query.get(k_id).avatar_url == url2,
              f"avatar_url={User.query.get(k_id).avatar_url}")

    # 6) WebP 输入 → 接受
    r = upload_avatar(c_k, (make_image_bytes('WEBP', size=(400, 200)), 'pic.webp'))
    check('上传 WebP 302', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        url3 = User.query.get(k_id).avatar_url
    check('WebP 上传后 avatar_url 已更新',
          re.fullmatch(_AVATAR_URL_RE, url3 or '') is not None and url3 != url2,
          f'avatar_url={url3}')
    path3 = os.path.join(_avatar_dir, url3.rsplit('/', 1)[-1])
    with Image.open(path3) as im:
        im.load()
        check('WebP 产物为 256×256 JPEG',
              im.format == 'JPEG' and im.size == (256, 256),
              f'format={im.format} size={im.size}')

    # 7) 只增不减：连续上传换新文件名，旧文件仍在盘上
    check('二次上传换新文件名', url3 != url1)
    check('旧头像文件仍在盘上（只增不减）', os.path.isfile(path1))

    # 7.5) 保存个人设置（改名/签名）不再触碰 avatar_url（头像 URL 栏已随上传功能下线）
    token = csrf(c_k, '/auth/settings')
    r = c_k.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': '头像侠K', 'signature': '改签名不动头像',
    })
    check('保存设置 302', r.status_code == 302, f'status={r.status_code}')
    check('保存设置后跳转论坛首页', '/forum/' in r.headers.get('Location', ''),
          f"Location={r.headers.get('Location', '')}")
    with app.app_context():
        check('保存设置后 avatar_url 未被清空',
              User.query.get(k_id).avatar_url == url3,
              f"avatar_url={User.query.get(k_id).avatar_url}")
    # 恢复昵称，避免影响后续断言
    token = csrf(c_k, '/auth/settings')
    c_k.post('/auth/settings', data={'_csrf_token': token, 'nickname': '', 'signature': ''})

    # 8) 页面展示：设置页 / 帖子楼层 / 个人空间 / 顶栏，无头像用户仍是首字符圈
    token = csrf(c_k, '/forum/board/general/new')
    r = c_k.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '头像展示验证帖ZAV', 'content': '头像展示验证正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.50.2.1'})
    check('K 发帖成功', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        tav_id = Thread.query.filter_by(title='头像展示验证帖ZAV').first().id
    token = csrf(c_l, f'/forum/thread/{tav_id}')
    r = c_l.post(f'/forum/thread/{tav_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '无头像用户的回复占位内容',
    }, environ_overrides={'REMOTE_ADDR': '10.60.2.1'})
    check('L 回复成功', r.status_code == 302, f'status={r.status_code}')

    r = c_k.get('/auth/settings')
    body = r.get_data(as_text=True)
    check('设置页 96px 头像预览', 'class="avatar-img avatar-img-xl"' in body and
          f'src="{url3}"' in body)
    r = c_k.get(f'/forum/thread/{tav_id}')
    body = r.get_data(as_text=True)
    # 楼层 img 标签是 <img class="avatar-img" src="…">（顶栏/设置页类名带 -xs/-xl 后缀，不会误匹配）
    check('帖子页 K 楼层显示头像 img', f'<img class="avatar-img" src="{url3}"' in body)
    _floor_img_count = body.count('<img class="avatar-img" src="')
    check('帖子页仅 K 一层有头像 img（L 无头像）', _floor_img_count == 1,
          f'count={_floor_img_count}')
    check('L 楼层仍是首字符圈', '<div class="avatar" style="background:' in body)
    r = c_k.get(f'/forum/user/{k_id}')
    body = r.get_data(as_text=True)
    check('个人空间显示 96px 头像 img', 'avatar-img-xl' in body and
          f'src="{url3}"' in body)
    r = c_k.get('/forum/')
    body = r.get_data(as_text=True)
    # 顶栏 img 标签判定必须带 <img 前缀：base.html 内嵌样式表里有同名 CSS 选择器
    check('顶栏显示 28px 头像 img',
          f'<img class="avatar-img avatar-img-xs" src="{url3}"' in body)
    r = c_l.get('/forum/')
    check('无头像用户顶栏不渲染头像 img',
          '<img class="avatar-img avatar-img-xs"' not in r.get_data(as_text=True))

    # 9) 限速：avatar_upload 桶 10 次/小时，独立 IP 瞬发第 11 次 → 429
    rl_statuses = []
    for i in range(11):
        resp = upload_avatar(c_k, (make_image_bytes('PNG', size=(32, 32)),
                                   f'rl{i}.png'), ip='10.70.9.9')
        rl_statuses.append(resp.status_code)
    check('头像上传前 10 次未限速', rl_statuses[:10] == [302] * 10,
          f'statuses={rl_statuses}')
    check('头像上传第 11 次 429', rl_statuses[10] == 429,
          f'statuses={rl_statuses}')

    # 清理头像测试产物：只删本节新增的文件；avatars 目录若是本次新建则一并删除
    if os.path.isdir(_avatar_dir):
        for fn in os.listdir(_avatar_dir):
            if fn not in _avatar_before:
                try:
                    os.remove(os.path.join(_avatar_dir, fn))
                except OSError:
                    pass
        if not _avatar_dir_existed:
            try:
                os.rmdir(_avatar_dir)
            except OSError:
                pass

if True:
    print('== 24. 注册可选昵称（中文）+ B 站式昵称规则 ==')
    cn = app.test_client()
    r = register(cn, 'smoke_user_n', 'smoke_n@example.com', PW,
                 ip='10.80.1.1', nickname='双子☆酱')
    check('注册时填中文昵称成功(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        u = User.query.filter_by(username='smoke_user_n').first()
        check('中文昵称落库', u.nickname == '双子☆酱', f'nickname={u.nickname!r}')
        check('display_name 用中文昵称', u.display_name == '双子☆酱')
    # 昵称撞车：别人注册时填已存在的昵称 → 拒绝
    cn2 = app.test_client()
    r = register(cn2, 'smoke_user_n2', 'smoke_n2@example.com', PW,
                 ip='10.80.1.2', nickname='双子☆酱')
    check('注册撞他人中文昵称被拒(200)', r.status_code == 200, f'status={r.status_code}')
    check('撞昵称给出提示', '该昵称已被他人使用' in r.get_data(as_text=True))
    # B 站式规则：带符号昵称可设；带空格被拒；单汉字（加权=2）可设
    token = csrf(cn, '/auth/settings')
    r = cn.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': '姬丝☆基勒~', 'signature': '',
    })
    check('带符号昵称设置成功(302)', r.status_code == 302, f'status={r.status_code}')
    token = csrf(cn, '/auth/settings')
    r = cn.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': '中 间', 'signature': '',
    })
    check('带空格昵称被拒', '昵称不能包含空格' in r.get_data(as_text=True))
    token = csrf(cn, '/auth/settings')
    r = cn.post('/auth/settings', data={
        '_csrf_token': token, 'nickname': '单', 'signature': '',
    })
    check('单汉字昵称（加权=2）可设(302)', r.status_code == 302, f'status={r.status_code}')

if True:
    print('== 25. 站内公告：发布 / 首页弹窗 / 历史页 / 权限 / 日志 ==')
    # 非管理员访问公告管理 → 403
    r = ca.get('/admin/announcements')
    check('非管理员访问公告管理 403', r.status_code == 403, f'status={r.status_code}')
    # 管理员发布第一条
    token = csrf(cadmin, '/admin/announcements')
    r = cadmin.post('/admin/announcements', data={
        '_csrf_token': token, 'title': '欢迎来到双子小站', 'content': '第一条公告内容\n请绿色上网',
    })
    check('管理员发布公告(302)', r.status_code == 302, f'status={r.status_code}')
    # 首页含弹窗与公告内容
    r = ca.get('/forum/')
    body = r.get_data(as_text=True)
    check('论坛首页渲染公告弹窗', 'ann-modal' in body and '欢迎来到双子小站' in body)
    check('弹窗有「我已知晓」按钮', 'ann-ack' in body and '我已知晓' in body)
    # 历史公告页（匿名也可访问）
    anon = app.test_client()
    r = anon.get('/forum/announcements')
    body = r.get_data(as_text=True)
    check('匿名访问历史公告页 200', r.status_code == 200, f'status={r.status_code}')
    check('历史页含公告标题', '欢迎来到双子小站' in body)
    # 再发一条 → 首页只弹最新一条
    token = csrf(cadmin, '/admin/announcements')
    r = cadmin.post('/admin/announcements', data={
        '_csrf_token': token, 'title': '第二条公告', 'content': '新内容会再次弹窗',
    })
    check('发布第二条公告(302)', r.status_code == 302, f'status={r.status_code}')
    r = ca.get('/forum/')
    body = r.get_data(as_text=True)
    check('首页弹窗更新为最新公告',
          '第二条公告' in body and '欢迎来到双子小站' not in body)
    # 历史页两条都在
    r = anon.get('/forum/announcements')
    body = r.get_data(as_text=True)
    check('历史页含两条公告', '欢迎来到双子小站' in body and '第二条公告' in body)
    # 空标题被拒
    token = csrf(cadmin, '/admin/announcements')
    r = cadmin.post('/admin/announcements', follow_redirects=True, data={
        '_csrf_token': token, 'title': '', 'content': 'x',
    })
    check('空标题公告被拒', '都不能为空' in r.get_data(as_text=True))
    # 操作日志留痕 + 数量核对
    with app.app_context():
        log = ModerationLog.query.filter_by(action='publish_announcement') \
                                 .order_by(ModerationLog.id.desc()).first()
        check('发布公告写入操作日志', log is not None and log.detail == '第二条公告')
        check('公告共两条', Announcement.query.count() == 2,
              f'count={Announcement.query.count()}')
    # 删除公告：非管理员 403；管理员删除后弹窗回退到上一条，日志留痕
    with app.app_context():
        ann2_id = Announcement.query.filter_by(title='第二条公告').first().id
    r = ca.post(f'/admin/announcements/{ann2_id}/delete', data={})
    check('非管理员删除公告 403', r.status_code == 403, f'status={r.status_code}')
    token = csrf(cadmin, '/admin/announcements')
    r = cadmin.post(f'/admin/announcements/{ann2_id}/delete',
                    data={'_csrf_token': token})
    check('管理员删除公告(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('公告剩一条', Announcement.query.count() == 1,
              f'count={Announcement.query.count()}')
        log = ModerationLog.query.filter_by(action='delete_announcement').first()
        check('删除公告写入操作日志', log is not None and log.detail == '第二条公告')
    r = ca.get('/forum/')
    body = r.get_data(as_text=True)
    check('删除最新后弹窗回退到上一条',
          '欢迎来到双子小站' in body and '第二条公告' not in body)

if True:
    print('== 26. 涉政/违禁词红线硬拦截：标题/正文/回复/注册昵称/空格拆解/命中日志 ==')
    import wordfilter
    # 临时词库：注释行与空行应被忽略，仅「测试违禁甲」一词生效
    _words_file = os.path.join(tempfile.gettempdir(), 'ygo_smoke_sensitive_words.txt')
    with open(_words_file, 'w', encoding='utf-8') as f:
        f.write('# 冒烟测试专用临时词库\n\n测试违禁甲\n')
    _prev_words_env = os.environ.get('SENSITIVE_WORDS_FILE')
    os.environ['SENSITIVE_WORDS_FILE'] = _words_file
    _n = wordfilter.load_words()
    check('临时词库加载返回 1 词', _n == 1, f'n={_n}')
    check('find_sensitive 命中返回词库原词',
          wordfilter.find_sensitive('前缀测试违禁甲后缀') == '测试违禁甲')
    check('find_sensitive 正常内容不命中',
          wordfilter.find_sensitive('完全正常的讨论内容') is None)

    # 命中日志基线：只核对本节新增的行，结束后把日志恢复原样（不污染真实日志）
    _hit_log = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                            '..', 'instance', 'sensitive_hits.log'))
    _log_before = os.path.getsize(_hit_log) if os.path.exists(_hit_log) else None

    c_w = app.test_client()
    r = register(c_w, 'smoke_user_w', 'smoke_w@example.com', PW, ip='10.90.1.1')
    check('注册用户 W', r.status_code == 302, f'status={r.status_code}')

    # ⑥ 正常内容照常成功（后续回复用例依赖这个正常帖）
    token = csrf(c_w, '/forum/board/general/new')
    r = c_w.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '红线验证正常帖ZOK', 'content': '红线验证正常正文内容',
    }, environ_overrides={'REMOTE_ADDR': '10.90.2.1'})
    check('正常标题正文发帖成功(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        _tz = Thread.query.filter_by(title='红线验证正常帖ZOK').first()
        assert _tz, '正常帖未落库'
        tz_id = _tz.id

    # ① 标题含违禁词 → 硬拦截重渲染，统一提示，回填原文，不落库
    token = csrf(c_w, '/forum/board/general/new')
    r = c_w.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '聊聊测试违禁甲的事', 'content': '正常正文内容占位',
    }, environ_overrides={'REMOTE_ADDR': '10.90.2.2'})
    body = r.get_data(as_text=True)
    check('违禁标题重渲染(200)', r.status_code == 200, f'status={r.status_code}')
    check('违禁标题统一提示', '内容包含违规信息，无法发布' in body)
    check('违禁标题回填原文', 'value="聊聊测试违禁甲的事"' in body)
    with app.app_context():
        check('违禁标题未落库',
              Thread.query.filter(Thread.title.like('%聊聊%')).first() is None)

    # ② 正文含违禁词 → 同样硬拦截
    token = csrf(c_w, '/forum/board/general/new')
    r = c_w.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '正常标题占位ZBAD', 'content': '正文里夹带测试违禁甲内容',
    }, environ_overrides={'REMOTE_ADDR': '10.90.2.3'})
    body = r.get_data(as_text=True)
    check('违禁正文重渲染(200)', r.status_code == 200, f'status={r.status_code}')
    check('违禁正文统一提示', '内容包含违规信息，无法发布' in body)
    check('违禁正文回填原文', '正文里夹带测试违禁甲内容' in body)
    with app.app_context():
        check('违禁正文帖未落库',
              Thread.query.filter_by(title='正常标题占位ZBAD').first() is None)

    # ⑤ 标题里词被空格拆开 → 归一化剔除空白后照样命中
    token = csrf(c_w, '/forum/board/general/new')
    r = c_w.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '测 试 违 禁 甲', 'content': '正常正文内容占位',
    }, environ_overrides={'REMOTE_ADDR': '10.90.2.4'})
    body = r.get_data(as_text=True)
    check('空格拆解的违禁标题也被拦(200)', r.status_code == 200, f'status={r.status_code}')
    check('空格拆解同样统一提示', '内容包含违规信息，无法发布' in body)
    with app.app_context():
        check('空格拆解标题未落库',
              Thread.query.filter(Thread.title.like('%测 试%')).first() is None)

    # ③ 回复含违禁词 → 硬拦截重渲染，不落库
    token = csrf(c_w, f'/forum/thread/{tz_id}')
    r = c_w.post(f'/forum/thread/{tz_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '回复里夹带测试违禁甲',
    }, environ_overrides={'REMOTE_ADDR': '10.90.3.1'})
    body = r.get_data(as_text=True)
    check('违禁回复重渲染(200)', r.status_code == 200, f'status={r.status_code}')
    check('违禁回复统一提示', '内容包含违规信息，无法发布' in body)
    check('违禁回复回填原文', '回复里夹带测试违禁甲' in body)
    with app.app_context():
        check('违禁回复未落库',
              Post.query.filter(Post.content.like('%夹带测试违禁甲%')).count() == 0)

    # ⑥ 正常回复照常成功
    token = csrf(c_w, f'/forum/thread/{tz_id}')
    r = c_w.post(f'/forum/thread/{tz_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '正常回复内容占位',
    }, environ_overrides={'REMOTE_ADDR': '10.90.3.2'})
    check('正常回复成功(302)', r.status_code == 302, f'status={r.status_code}')

    # ④ 注册昵称含违禁词 → 拒绝注册，不落库（提示同样不回显命中词）
    c_bad = app.test_client()
    r = register(c_bad, 'smoke_user_bad', 'smoke_bad@example.com', PW,
                 ip='10.90.1.2', nickname='我是测试违禁甲')
    body = r.get_data(as_text=True)
    check('违禁昵称注册被拒(200)', r.status_code == 200, f'status={r.status_code}')
    check('违禁昵称给出统一提示', '该昵称包含违规信息' in body)
    with app.app_context():
        check('违禁昵称用户未落库',
              User.query.filter_by(username='smoke_user_bad').first() is None)

    # ⑥ 正常昵称注册照常成功
    c_x = app.test_client()
    r = register(c_x, 'smoke_user_x', 'smoke_x@example.com', PW,
                 ip='10.90.1.3', nickname='正常昵称酱')
    check('正常昵称注册成功(302)', r.status_code == 302, f'status={r.status_code}')

    # ⑦ 命中日志：本节 5 次拦截各留一行（thread×3 / reply×1 / nickname×1），且都含命中词
    check('命中日志文件已生成', os.path.exists(_hit_log))
    with open(_hit_log, 'r', encoding='utf-8') as f:
        if _log_before:
            f.seek(_log_before)
        _new_lines = [ln for ln in f.read().splitlines() if ln.strip()]
    check('命中日志新增 5 行', len(_new_lines) == 5, f'lines={len(_new_lines)}')
    check('命中日志每行都含命中词', all('测试违禁甲' in ln for ln in _new_lines))
    check('命中日志类型分布 thread×3/reply×1/nickname×1',
          sum('\tthread\t' in ln for ln in _new_lines) == 3 and
          sum('\treply\t' in ln for ln in _new_lines) == 1 and
          sum('\tnickname\t' in ln for ln in _new_lines) == 1,
          f'lines={_new_lines}')

    # 清理：还原环境变量、删除临时词库、命中日志回滚到基线
    if _prev_words_env is None:
        os.environ.pop('SENSITIVE_WORDS_FILE', None)
    else:
        os.environ['SENSITIVE_WORDS_FILE'] = _prev_words_env
    try:
        os.remove(_words_file)
    except OSError:
        pass
    try:
        if _log_before is None:
            os.remove(_hit_log)
        else:
            with open(_hit_log, 'r+b') as f:
                f.truncate(_log_before)
    except OSError:
        pass

if True:
    print('== 27. 表情包：清单加载/渲染过滤/XSS 转义/上限/编辑器入口/静态资源 ==')
    import emotes

    # ① 清单加载（真实 img/emotes/emotes.json，占位示例包）
    _packs, _by_id = emotes.load_emotes(force=True)
    check('清单加载出示例包', any(p['id'] == 'sample' for p in _packs),
          f'packs={[p["id"] for p in _packs]}')
    check('清单含动态表情 twirl', _by_id.get('twirl', {}).get('file') == 'sample/twirl.gif')

    # ② 渲染单元：白名单替换 + 先转义（XSS 不进输出）
    out = str(emotes.render_content('<script>alert(1)</script>[em:star]'))
    check('渲染出表情 img', 'class="emote"' in out and '/img/emotes/sample/star.png' in out, out)
    check('用户 HTML 被转义', '&lt;script&gt;' in out and '<script>' not in out, out)
    check('未知表情 id 保留原文', str(emotes.render_content('[em:nosuchid]')) == '[em:nosuchid]')
    check('非法语法不匹配', str(emotes.render_content('[em:BAD]')) == '[em:BAD]')

    # ③ 每楼上限 10 个：11 个占位符只渲染 10 个，第 11 个保留原文
    out = str(emotes.render_content('[em:star]' * 11))
    check('超上限只渲染 10 个', out.count('<img ') == 10 and '[em:star]' in out,
          f'imgs={out.count("<img ")}')

    # ④ HTTP：发帖带表情 → 帖子页渲染 <img>
    c_e = app.test_client()
    r = register(c_e, 'smoke_user_em', 'smoke_em@example.com', PW, ip='10.91.1.1')
    check('注册用户 EM', r.status_code == 302, f'status={r.status_code}')
    token = csrf(c_e, '/forum/board/general/new')
    r = c_e.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '表情包验证帖ZEM', 'content': '带个表情[em:twirl]结束',
    }, environ_overrides={'REMOTE_ADDR': '10.91.2.1'})
    check('带表情发帖成功(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        _te = Thread.query.filter_by(title='表情包验证帖ZEM').first()
        assert _te, '表情帖未落库'
        te_id = _te.id
    r = c_e.get(f'/forum/thread/{te_id}')
    body = r.get_data(as_text=True)
    check('帖子页渲染动态表情', '/img/emotes/sample/twirl.gif' in body and 'class="emote"' in body)

    # ⑤ 回复带表情 → 同样渲染
    token = csrf(c_e, f'/forum/thread/{te_id}')
    r = c_e.post(f'/forum/thread/{te_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '回复也带[em:heart]表情',
    }, environ_overrides={'REMOTE_ADDR': '10.91.3.1'})
    check('带表情回复成功(302)', r.status_code == 302, f'status={r.status_code}')
    r = c_e.get(f'/forum/thread/{te_id}')
    check('回复楼层渲染表情', '/img/emotes/sample/heart.png' in r.get_data(as_text=True))

    # ⑥ 三个编辑器都有表情按钮/弹层/脚本
    r = c_e.get('/forum/board/general/new')
    body = r.get_data(as_text=True)
    check('发帖页有表情入口', 'class="emote-btn"' in body and 'data-target="nt-content"' in body
          and 'emote-modal' in body and '/js/emotes.js' in body)
    r = c_e.get(f'/forum/thread/{te_id}')
    body = r.get_data(as_text=True)
    check('回帖框有表情入口', 'data-target="reply-content"' in body and 'emote-modal' in body)
    r = c_e.get(f'/forum/thread/{te_id}/edit')
    body = r.get_data(as_text=True)
    check('编辑页有表情入口', r.status_code == 200 and 'data-target="et-content"' in body)

    # ⑦ 静态资源可达：清单 / 选择器脚本 / 动态表情本体
    r = c_e.get('/img/emotes/emotes.json')
    check('清单 JSON 200', r.status_code == 200 and b'"packs"' in r.data, f'status={r.status_code}')
    r = c_e.get('/js/emotes.js')
    check('选择器脚本 200', r.status_code == 200, f'status={r.status_code}')
    r = c_e.get('/img/emotes/sample/twirl.gif')
    check('动态表情 200 且为 GIF', r.status_code == 200 and r.content_type == 'image/gif',
          f'status={r.status_code} type={r.content_type}')

if True:
    print('== 28. 删除自己的回帖：作者可删/他人 403/首帖拒绝/通知清理/日志边界 ==')
    c_dt = app.test_client()
    r = register(c_dt, 'smoke_user_dt', 'smoke_dt@example.com', PW, ip='10.92.1.1')
    check('注册用户 DT', r.status_code == 302, f'status={r.status_code}')
    c_dd = app.test_client()
    r = register(c_dd, 'smoke_user_dd', 'smoke_dd@example.com', PW, ip='10.92.1.2')
    check('注册用户 DD', r.status_code == 302, f'status={r.status_code}')

    # 楼主 DT 发帖，DD 回帖
    token = csrf(c_dt, '/forum/board/general/new')
    r = c_dt.post('/forum/board/general/new', data={
        '_csrf_token': token, 'title': '自删回帖验证帖ZOD', 'content': '楼主正文，欢迎回复',
    }, environ_overrides={'REMOTE_ADDR': '10.92.2.1'})
    check('DT 发帖成功(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        _tod = Thread.query.filter_by(title='自删回帖验证帖ZOD').first()
        assert _tod, '自删验证帖未落库'
        tod_id = _tod.id
        first_od_id = Post.query.filter_by(thread_id=tod_id).order_by(Post.id).first().id

    token = csrf(c_dd, f'/forum/thread/{tod_id}')
    r = c_dd.post(f'/forum/thread/{tod_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '这条回帖说错话了ZWRONG',
    }, environ_overrides={'REMOTE_ADDR': '10.92.3.1'})
    check('DD 回帖成功(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        _wrong = Post.query.filter(Post.thread_id == tod_id,
                                   Post.content.like('%ZWRONG%')).first()
        assert _wrong, 'DD 回帖未落库'
        wrong_id = _wrong.id
        # 楼主应收到该回帖的通知（kind=reply, post_id=wrong_id）
        check('回帖产生通知', Notification.query.filter_by(
            post_id=wrong_id, kind='reply').count() >= 1)
        _logs_before = ModerationLog.query.filter_by(action='delete_post').count()

    # ① 作者本人删除自己的回帖 → 成功，楼层/通知清理，且不算管理操作（无日志）
    token = csrf(c_dd, f'/forum/thread/{tod_id}')
    r = c_dd.post(f'/forum/post/{wrong_id}/delete', data={'_csrf_token': token},
                  environ_overrides={'REMOTE_ADDR': '10.92.3.2'})
    check('作者删除自己回帖(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('回帖已落库删除', Post.query.get(wrong_id) is None)
        check('指向该楼的通知已清理',
              Notification.query.filter_by(post_id=wrong_id).count() == 0)
        check('自删不写管理日志',
              ModerationLog.query.filter_by(action='delete_post').count() == _logs_before)
    r = c_dd.get(f'/forum/thread/{tod_id}')
    check('帖子页不再显示已删内容', 'ZWRONG' not in r.get_data(as_text=True))

    # ② 非作者非管理员删他人回帖 → 403
    token = csrf(c_dd, f'/forum/thread/{tod_id}')
    r = c_dd.post(f'/forum/thread/{tod_id}/reply?page=1', data={
        '_csrf_token': token, 'content': '第二条正常回帖ZOK2',
    }, environ_overrides={'REMOTE_ADDR': '10.92.3.3'})
    check('DD 再回帖(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        ok2_id = Post.query.filter(Post.thread_id == tod_id,
                                   Post.content.like('%ZOK2%')).first().id
    token = csrf(c_dt, f'/forum/thread/{tod_id}')
    r = c_dt.post(f'/forum/post/{ok2_id}/delete', data={'_csrf_token': token},
                  environ_overrides={'REMOTE_ADDR': '10.92.2.2'})
    check('非作者删他人回帖被拒(403)', r.status_code == 403, f'status={r.status_code}')
    with app.app_context():
        check('他人回帖未被误删', Post.query.get(ok2_id) is not None)

    # ③ 首帖（=主题正文）不能走单楼删除
    token = csrf(c_dt, f'/forum/thread/{tod_id}')
    r = c_dt.post(f'/forum/post/{first_od_id}/delete', data={'_csrf_token': token},
                  environ_overrides={'REMOTE_ADDR': '10.92.2.3'})
    check('首帖单删被拒(302 回跳)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('首帖与主题仍在', Post.query.get(first_od_id) is not None
              and Thread.query.get(tod_id) is not None)

    # ④ 未登录删除：带合法 CSRF 令牌时被 login_required 重定向到登录页；
    #    无令牌则先被全局 CSRF 拦截（403），两种都到不了删除逻辑
    c_anon = app.test_client()
    token = csrf(c_anon, '/auth/login')
    r = c_anon.post(f'/forum/post/{ok2_id}/delete', data={'_csrf_token': token})
    check('未登录删除重定向登录(302)', r.status_code == 302
          and '/auth/login' in (r.headers.get('Location') or ''), f'status={r.status_code}')
    r = c_anon.post(f'/forum/post/{ok2_id}/delete', data={'_csrf_token': 'x'})
    check('无 CSRF 令牌的删除被拦(403)', r.status_code == 403, f'status={r.status_code}')
    with app.app_context():
        check('匿名尝试后回帖仍在', Post.query.get(ok2_id) is not None)

    # ⑤ 页面按钮：作者看到自己的删除入口，其他普通用户看不到
    r = c_dd.get(f'/forum/thread/{tod_id}')
    check('作者页面有自删按钮', f'/forum/post/{ok2_id}/delete' in r.get_data(as_text=True))
    r = c_dt.get(f'/forum/thread/{tod_id}')
    check('非作者页面无该楼删除按钮',
          f'/forum/post/{ok2_id}/delete' not in r.get_data(as_text=True))

    # ⑥ 管理员删他人回帖 → 成功且写管理日志（原有行为回归）
    with app.app_context():
        _logs_before = ModerationLog.query.filter_by(action='delete_post').count()
    token = csrf(cadmin, f'/forum/thread/{tod_id}')
    r = cadmin.post(f'/forum/post/{ok2_id}/delete', data={'_csrf_token': token})
    check('管理员删他人回帖(302)', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        check('管理员删他人写日志',
              ModerationLog.query.filter_by(action='delete_post').count() == _logs_before + 1)

print()
print(f'结果：{len(_passed)} 通过，{len(_failed)} 失败')
if _failed:
    print('失败项：' + '、'.join(_failed))
    sys.exit(1)

# 清理临时数据库文件：Windows 下 SQLite 连接未释放时 os.remove 会撞 WinError 32，先释放引擎
try:
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
except Exception:
    pass
try:
    os.remove(_tmp_db)
except OSError:
    pass
print('冒烟测试全部通过，临时数据库已清理。')
