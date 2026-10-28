# -*- coding: utf-8 -*-
"""卡组构筑器端到端冒烟测试（临时库，不碰 instance/ygo.db）。

覆盖：未登录拦截 → 建组落库 → 表单校验全分支拒绝 → 红线词拦截 →
编辑权限（作者/非作者/未登录）→ 删除权限 → 详情页权限按钮 →
个人空间卡组区块 → Deck 模型解析 → 删用户级联清卡组 → 清理无残留。

用法：FLASK_DEBUG=1 DATABASE_URL="sqlite:///<临时库路径>" python -X utf8 scripts/smoke_decks.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import app
from models import db, User, Deck

c = app.test_client()
passed = failed = 0

PW = 'passw0rd1'  # 满足强度规则（字母+数字）且不含用户名/邮箱前缀
TITLE_1 = '双子冒烟卡组'
TITLE_1_EDITED = '双子冒烟卡组·改'
TITLE_2 = '卡组二号'


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
                  data={'username': username, 'password': PW, '_csrf_token': tok})


# ── 卡组 JSON 帮助函数 ──────────────────────────────

def _card(cid):
    return {'id': cid, 'name': f'测试卡{cid}', 'types': '怪兽卡 效果'}


def make_main(count=40):
    """count 张主卡组：21 种 id 各 3 张切片（不触同 id ≤3 规则）"""
    cards = [_card(cid) for cid in range(1001, 1022) for _ in range(3)]
    return cards[:count]


def make_extra(count=5):
    return [_card(2000 + i) for i in range(count)]


def make_side(count=3):
    return [_card(3000 + i) for i in range(count)]


def J(cards):
    return json.dumps(cards, ensure_ascii=False)


def deck_form(**over):
    """一份合法卡组表单，over 可覆盖任意字段制造非法输入"""
    data = {'title': TITLE_1, 'description': '冒烟测试卡组简介',
            'main_json': J(make_main()), 'extra_json': J(make_extra()),
            'side_json': J(make_side())}
    data.update(over)
    return data


# ── 造测试用户（甲=卡组作者 / 乙=他人 / 丙=无卡组用户）────────
with app.app_context():
    ua = User(username='decktest_a', email='da@t.com', nickname='卡组甲')
    ua.set_password(PW)
    ub = User(username='decktest_b', email='db@t.com', nickname='卡组乙')
    ub.set_password(PW)
    uc = User(username='decktest_c', email='dc@t.com', nickname='卡组丙')
    uc.set_password(PW)
    db.session.add_all([ua, ub, uc])
    db.session.commit()
    id_a, id_b, id_c = ua.id, ub.id, uc.id

# ── (1) 未登录拦截 ──────────────────────────────────
print('== 1. 未登录：广场可见、新建拦截、CSRF 先于登录 ==')
r = c.get('/decks/')
check('未登录 GET /decks/ 200', r.status_code == 200)
r = c.get('/decks/new')
check('未登录 GET /decks/new 302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
r = c.post('/decks/new', data=deck_form())
check('未登录 POST /decks/new 无令牌 403（CSRF 先于 login_required）',
      r.status_code == 403)

# ── (2) 建组：落库 / 详情 / 广场 / 顶栏导航 ────────────
print('== 2. 甲建合法卡组 ==')
login('decktest_a')
r = c.get('/decks/new')
check('甲 GET /decks/new 200', r.status_code == 200)
tok_a = csrf_from(r.get_data(as_text=True))

data = deck_form()
data['_csrf_token'] = tok_a
r = c.post('/decks/new', data=data)
with app.app_context():
    d1 = Deck.query.filter_by(user_id=id_a).first()
    deck1 = d1.id if d1 else None
check('合法卡组 POST 302 跳详情页',
      r.status_code == 302 and deck1 is not None
      and f'/decks/{deck1}' in r.headers['Location'])
with app.app_context():
    d1 = Deck.query.get(deck1)
    check('卡组落库 1 套', Deck.query.filter_by(user_id=id_a).count() == 1)
    check('counts() == (40, 5, 3)', d1.counts() == (40, 5, 3))
    check('deck.author 属性别名指向作者',
          d1.author is not None and d1.author.id == id_a and d1.author_id == id_a)

r = c.get(f'/decks/{deck1}')
html = r.get_data(as_text=True)
check('详情页 200 且含卡组标题', r.status_code == 200 and TITLE_1 in html)
r = c.get('/decks/')
html = r.get_data(as_text=True)
check('广场 200 且含卡组标题', r.status_code == 200 and TITLE_1 in html)
check('顶栏导航含「卡组」链接 /decks/', 'href="/decks/"' in html)

# ── (3) 校验拒绝：全部 200 不入库 ─────────────────────
print('== 3. 表单校验全分支拒绝 ==')
# 每个用例都过 _rate_limited（30/h），本脚本 deck_save 总命中 ~16 次，不超限
r = c.post('/decks/new', data=dict(deck_form(main_json=J(make_main(39))), _csrf_token=tok_a))
check('主卡组 39 张拒绝（200 且提示张数区间）',
      r.status_code == 200 and '主卡组须为 40-60' in r.get_data(as_text=True))
r = c.post('/decks/new', data=dict(deck_form(main_json=J(make_main(61))), _csrf_token=tok_a))
check('主卡组 61 张拒绝', r.status_code == 200)
r = c.post('/decks/new', data=dict(deck_form(extra_json=J(make_extra(16))), _csrf_token=tok_a))
check('额外 16 张拒绝', r.status_code == 200)
r = c.post('/decks/new', data=dict(deck_form(side_json=J(make_side(16))), _csrf_token=tok_a))
check('副卡组 16 张拒绝', r.status_code == 200)
same4 = make_main() + [_card(1001)]  # 41 张合法区间，但 id 1001 出现 4 次
r = c.post('/decks/new', data=dict(deck_form(main_json=J(same4)), _csrf_token=tok_a))
check('同 id 4 张拒绝（200 且提示最多 3 张）',
      r.status_code == 200 and '最多放 3 张' in r.get_data(as_text=True))
r = c.post('/decks/new', data=dict(deck_form(title=''), _csrf_token=tok_a))
check('空标题拒绝', r.status_code == 200)
r = c.post('/decks/new', data=dict(deck_form(title='标' * 61), _csrf_token=tok_a))
check('标题 61 字拒绝', r.status_code == 200)
r = c.post('/decks/new', data=dict(deck_form(description='简' * 2001), _csrf_token=tok_a))
check('简介 2001 字拒绝', r.status_code == 200)
r = c.post('/decks/new', data=dict(deck_form(main_json='这不是JSON'), _csrf_token=tok_a))
check('main_json 非 JSON 拒绝（200 且提示格式有误）',
      r.status_code == 200 and '数据格式有误' in r.get_data(as_text=True))
noid = make_main()
noid[0] = {'name': '无id卡', 'types': '怪兽卡'}
r = c.post('/decks/new', data=dict(deck_form(main_json=J(noid)), _csrf_token=tok_a))
check('条目缺 id 拒绝（200 且提示无效条目）',
      r.status_code == 200 and '无效的卡片条目' in r.get_data(as_text=True))
badid = make_main()
badid[0] = {'id': 'abc', 'name': '坏id卡', 'types': '怪兽卡'}
r = c.post('/decks/new', data=dict(deck_form(main_json=J(badid)), _csrf_token=tok_a))
check('条目 id 非数字拒绝', r.status_code == 200)
with app.app_context():
    check('全部非法输入均未落库（甲仍只有 1 套）',
          Deck.query.filter_by(user_id=id_a).count() == 1)

# 红线词标题：词库取第一个非注释词，无词库则跳过
try:
    with open(os.path.join(ROOT, 'instance/sensitive_words_red.txt'), encoding='utf-8') as f:
        red_word = next((ln.strip() for ln in f if ln.strip() and not ln.startswith('#')), None)
except FileNotFoundError:
    red_word = None
if red_word:
    r = c.post('/decks/new', data=dict(deck_form(title=f'这是{red_word}的卡组'), _csrf_token=tok_a))
    html = r.get_data(as_text=True)
    check('红线词标题拒绝（200 且提示不适宜词汇）',
          r.status_code == 200 and '不适宜发布的词汇' in html)
    check('响应不回显命中词', red_word not in html)
    with app.app_context():
        check('红线词卡组未落库', Deck.query.filter_by(user_id=id_a).count() == 1)
else:
    print('  SKIP 红线词验证（无本地词库）')

# ── (3.5) OCG 禁限卡表校验与 API ────────────────────
print('== 3.5 禁限卡表：禁止/限制/准限制分档拒绝 + API ==')
# 从数据文件动态挑卡（不写死 id，禁限表每季度会变）；无文件则跳过
try:
    with open(os.path.join(ROOT, 'data', 'banlist_ocg.json'), encoding='utf-8') as f:
        banlist_file = json.load(f).get('cards', {})
except (FileNotFoundError, ValueError):
    banlist_file = {}

def _pick(tier):
    """挑一张指定档位（0 禁止 / 1 限制 / 2 准限制）的卡 id（int）"""
    return next((int(cid) for cid, v in banlist_file.items() if v == tier), None)

if banlist_file:
    forbidden_id = _pick(0)
    limited_id = _pick(1)
    semi_id = _pick(2)

    # 禁止卡：放 1 张也拒
    main_f = make_main()
    main_f[0] = {'id': forbidden_id, 'name': f'测试禁止卡{forbidden_id}', 'types': '怪兽卡 效果'}
    r = c.post('/decks/new', data=dict(deck_form(main_json=J(main_f)), _csrf_token=tok_a))
    check('禁止卡 1 张拒绝（200 且提示禁止卡）',
          r.status_code == 200 and '禁止卡' in r.get_data(as_text=True))
    with app.app_context():
        check('禁止卡卡组未落库', Deck.query.filter_by(user_id=id_a).count() == 1)

    # 限制卡：放 2 张拒
    main_l2 = make_main()
    main_l2[0] = main_l2[1] = {'id': limited_id, 'name': f'测试限制卡{limited_id}', 'types': '怪兽卡 效果'}
    r = c.post('/decks/new', data=dict(deck_form(main_json=J(main_l2)), _csrf_token=tok_a))
    check('限制卡 2 张拒绝（200 且提示限制卡）',
          r.status_code == 200 and '限制卡' in r.get_data(as_text=True))
    with app.app_context():
        check('限制卡卡组未落库', Deck.query.filter_by(user_id=id_a).count() == 1)

    # 准限制卡：放 3 张拒（若有 2 档卡）
    if semi_id is not None:
        main_s3 = make_main()
        for i in range(3):
            main_s3[i] = {'id': semi_id, 'name': f'测试准限制卡{semi_id}', 'types': '怪兽卡 效果'}
        r = c.post('/decks/new', data=dict(deck_form(main_json=J(main_s3)), _csrf_token=tok_a))
        check('准限制卡 3 张拒绝（200 且提示准限制卡）',
              r.status_code == 200 and '准限制卡' in r.get_data(as_text=True))
    else:
        print('  SKIP 准限制卡验证（本季表无 2 档卡）')

    # 限制卡放 1 张合法：替换一张凑数卡保持 40 张 → 通过后删掉，不影响后续计数断言
    main_l1 = make_main()
    main_l1[0] = {'id': limited_id, 'name': f'测试限制卡{limited_id}', 'types': '怪兽卡 效果'}
    r = c.post('/decks/new', data=dict(deck_form(title='禁限表冒烟·限1', main_json=J(main_l1)), _csrf_token=tok_a))
    with app.app_context():
        dl = Deck.query.filter_by(user_id=id_a, title='禁限表冒烟·限1').first()
        deck_l = dl.id if dl else None
    check('限制卡 1 张通过（302 落库）', r.status_code == 302 and deck_l is not None)
    r = c.get(f'/decks/{deck_l}')
    tok_l = csrf_from(r.get_data(as_text=True))
    r = c.post(f'/decks/{deck_l}/delete', data={'_csrf_token': tok_l})
    with app.app_context():
        check('限1 卡组删除复位（甲仍只有 1 套）',
              r.status_code == 302 and Deck.query.filter_by(user_id=id_a).count() == 1)

    # 禁限表 API：条数与数据文件一致
    r = c.get('/decks/api/banlist')
    check('GET /decks/api/banlist 200', r.status_code == 200)
    api_cards = r.get_json().get('cards', {})
    check('API 条数与数据文件一致', len(api_cards) == len(banlist_file))
    check('API 内容与数据文件一致（抽查禁止/限制卡档位）',
          api_cards.get(str(forbidden_id)) == 0 and api_cards.get(str(limited_id)) == 1)
else:
    print('  SKIP 禁限卡表验证（无 data/banlist_ocg.json）')

# ── (4) 编辑：作者可改、非作者/未登录拦截 ───────────────
print('== 4. 编辑权限 ==')
r = c.get(f'/decks/{deck1}/edit')
check('作者 GET edit 200 且回显标题',
      r.status_code == 200 and TITLE_1 in r.get_data(as_text=True))
data = deck_form(title=TITLE_1_EDITED, main_json=J(make_main(45)))
data['_csrf_token'] = tok_a
r = c.post(f'/decks/{deck1}/edit', data=data)
check('作者 POST edit 302 回详情页',
      r.status_code == 302 and f'/decks/{deck1}' in r.headers['Location'])
with app.app_context():
    d1 = Deck.query.get(deck1)
    check('编辑落库（标题更新 + counts 变为 (45,5,3)）',
          d1.title == TITLE_1_EDITED and d1.counts() == (45, 5, 3))

login('decktest_b')
r = c.get(f'/decks/{deck1}/edit')
check('非作者 GET edit 403', r.status_code == 403)
r = c.get('/decks/new')
tok_b = csrf_from(r.get_data(as_text=True))
data = deck_form(title='乙的篡改')
data['_csrf_token'] = tok_b
r = c.post(f'/decks/{deck1}/edit', data=data)
check('非作者 POST edit 403', r.status_code == 403)

c.get('/auth/logout')
r = c.get(f'/decks/{deck1}/edit')
check('未登录 GET edit 302 到登录页',
      r.status_code == 302 and '/auth/login' in r.headers['Location'])
r = c.post(f'/decks/{deck1}/edit', data=deck_form())
check('未登录 POST edit 无令牌 403', r.status_code == 403)

# ── (5) 删除：非作者 403、作者 302 且 DB 清零 ────────────
print('== 5. 删除权限 ==')
# 每次换登录态后令牌随 session 更换，重新取
login('decktest_a')
r = c.get('/decks/new')
tok = csrf_from(r.get_data(as_text=True))
data = deck_form(title=TITLE_2)
data['_csrf_token'] = tok
r = c.post('/decks/new', data=data)
with app.app_context():
    d2 = Deck.query.filter_by(user_id=id_a, title=TITLE_2).first()
    deck2 = d2.id if d2 else None
check('甲建第二套卡组 302', r.status_code == 302 and deck2 is not None)

login('decktest_b')
r = c.get(f'/decks/{deck1}')
tok_b2 = csrf_from(r.get_data(as_text=True))
r = c.post(f'/decks/{deck1}/delete', data={'_csrf_token': tok_b2})
check('非作者（非管理）POST delete 403', r.status_code == 403)
with app.app_context():
    check('403 后卡组仍在', Deck.query.get(deck1) is not None)

login('decktest_a')
r = c.get(f'/decks/{deck2}')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/decks/{deck2}/delete', data={'_csrf_token': tok})
check('作者 POST delete 302 回广场',
      r.status_code == 302 and '/decks/' in r.headers['Location'])
with app.app_context():
    check('删除后 DB 中该卡组清零',
          Deck.query.get(deck2) is None
          and Deck.query.filter_by(user_id=id_a).count() == 1)

# ── (6) 详情页权限按钮：作者见编辑、他人不见 ─────────────
print('== 6. 详情页权限按钮 ==')
r = c.get(f'/decks/{deck1}')
check('作者看详情含「✎ 编辑」', '✎ 编辑' in r.get_data(as_text=True))
login('decktest_b')
r = c.get(f'/decks/{deck1}')
check('他人看详情不含「✎ 编辑」', '✎ 编辑' not in r.get_data(as_text=True))

# ── (7) 个人空间卡组区块 ────────────────────────────
print('== 7. 个人空间「TA 的卡组」 ==')
r = c.get(f'/forum/user/{id_a}')
html = r.get_data(as_text=True)
check('有卡组用户空间含「TA 的卡组」区块', 'TA 的卡组' in html)
check('空间含卡组标题', TITLE_1_EDITED in html)
r = c.get(f'/forum/user/{id_c}')
check('无卡组用户空间含「还没有卡组」', '还没有卡组' in r.get_data(as_text=True))

# ── (8) 模型：损坏 JSON 不炸、counts 与 zones 一致 ────────
print('== 8. Deck 模型解析 ==')
with app.app_context():
    broken = Deck(user_id=id_b, title='模型冒烟·损坏', description='',
                  main_cards='{这不是合法JSON', extra_cards='"一个字符串"',
                  side_cards='[]')
    ok = Deck(user_id=id_b, title='模型冒烟·正常', description='',
              main_cards=J(make_main(42)), extra_cards=J(make_extra(2)),
              side_cards=J(make_side(1)))
    db.session.add_all([broken, ok])
    db.session.commit()
    bid, oid = broken.id, ok.id
    check('损坏 JSON main() 返回 []', broken.main() == [])
    check('非数组 JSON extra() 返回 []', broken.extra() == [])
    m, e, s = ok.counts()
    check('counts() 与三区长度一致',
          (m, e, s) == (42, 2, 1)
          == (len(ok.main()), len(ok.extra()), len(ok.side())))
    Deck.query.filter(Deck.id.in_([bid, oid])).delete(synchronize_session=False)
    db.session.commit()
    check('模型用例数据已自清', Deck.query.filter_by(user_id=id_b).count() == 0)

# ── (9) 级联：注册主管理员删作者 → 其卡组清零 ─────────────
print('== 9. 删用户级联清卡组 ==')
admin_name = 'synthetic_admin'
c.get('/auth/logout')
r = c.get('/auth/register')
tok = csrf_from(r.get_data(as_text=True))
r = c.post('/auth/register', data={
    'username': admin_name, 'email': 'decksm_admin@t.com', 'nickname': '',
    'password': PW, 'confirm': PW, '_csrf_token': tok})
check('注册主管理员账号 302', r.status_code == 302)
with app.app_context():
    adm = User.query.filter_by(username=admin_name).first()
    check('公开注册默认普通用户', adm is not None and not adm.is_admin)
    adm.is_admin = True
    db.session.commit()
    id_admin = adm.id

login(admin_name)
r = c.get('/admin/users')
tok = csrf_from(r.get_data(as_text=True))
r = c.post(f'/admin/user/{id_a}/delete', data={'_csrf_token': tok})
check('管理员删卡组作者 302', r.status_code == 302)
with app.app_context():
    check('作者的卡组已级联清零', Deck.query.filter_by(user_id=id_a).count() == 0)
    check('全库卡组无残留（deck1 随级联清除）', Deck.query.count() == 0)
    check('其他用户不受影响（乙/丙仍在）',
          User.query.get(id_b) is not None and User.query.get(id_c) is not None)
    db.session.remove()

# ── (10) 清理测试数据并删临时库 ───────────────────────
print('== 10. 清理 ==')
with app.app_context():
    Deck.query.filter(Deck.user_id.in_([id_a, id_b, id_c, id_admin])) \
              .delete(synchronize_session=False)
    User.query.filter(User.id.in_([id_a, id_b, id_c, id_admin])) \
              .delete(synchronize_session=False)
    db.session.commit()
    check('清理后测试用户/卡组均无残留',
          User.query.filter(User.username.like('decktest_%')).count() == 0
          and User.query.filter_by(username=admin_name).count() == 0
          and Deck.query.count() == 0)
    # 踩坑 #28：删库文件前先 remove + dispose，避免 WinError 32
    db.session.remove()
    db.engine.dispose()

uri = app.config['SQLALCHEMY_DATABASE_URI']
if uri.startswith('sqlite:///'):
    db_path = uri[len('sqlite:///'):]
    for suffix in ('', '-wal', '-shm'):
        p = db_path + suffix
        if os.path.isfile(p):
            os.remove(p)
    print(f'  已删除临时库 {db_path}')

print(f'\n结果：{passed} 通过 / {failed} 失败')
sys.exit(1 if failed else 0)
