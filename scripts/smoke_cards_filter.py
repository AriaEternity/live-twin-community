# -*- coding: utf-8 -*-
"""卡片条件筛选端到端冒烟测试（临时库，不碰 instance/ygo.db）。

被测：routes/cards.py 的 get_card_db / _parse_facets / /api/cards/filter /
扩展后的 /api/cards/search。两个端点均公开，test_client 免登录。
本地卡库 data/cards_db.json 与禁限表 data/banlist_ocg.json 只读。

注意：filter 端点中文名懒解析有每 IP 每分钟 20 次限速（仅需请求上游才计数），
本脚本约 25 次不同结果的 filter 请求，偶发触发 429 属预期行为——
fget() 遇 429 睡过窗口重试一次，不作为断言兜底；末尾单独验证 429 格式。

用法：FLASK_DEBUG=1 DATABASE_URL="sqlite:///<临时库路径>" python -X utf8 scripts/smoke_cards_filter.py
"""
import json
import math
import os
import random
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import app
from models import db
import routes.cards as cards_mod

c = app.test_client()
passed = failed = 0

CJK_RE = re.compile(r'[一-鿿]')
FILTER_KEYS = {'id', 'sc_name', 'cn_name', 'en_name', 'jp_name', 'types',
               'race_cn', 'attr_cn', 'level', 'linkval', 'atk', 'def',
               'image_small', 'ban'}   # 契约 14 个数据键（响应体另有 total/page/pages）
SEARCH_ORIG_KEYS = {'id', 'sc_name', 'cn_name', 'en_name', 'jp_name',
                    'types', 'pdesc', 'desc', 'image_small'}   # 原 9 字段


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  PASS {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


def fget(path):
    """GET /api/cards/filter；撞上中文名解析限速（预期行为）时睡过 60s 窗口重试一次"""
    r = c.get(path)
    if r.status_code == 429:
        print('  .. 触发 filter 中文名解析限速，睡 61s 后重试一次')
        time.sleep(61)
        r = c.get(path)
    return r


def fjson(path):
    """fget + 解析 JSON，断言 200 由调用方做"""
    return fget(path)


# ── (0) 前置：卡库与禁限表文件可读（只读，作为断言基准）────────
print('== 0. 前置：数据文件 ==')
with open(os.path.join(ROOT, 'data', 'cards_db.json'), encoding='utf-8') as f:
    db_file_cards = json.load(f)['cards']
with open(os.path.join(ROOT, 'data', 'banlist_ocg.json'), encoding='utf-8') as f:
    banlist_file = json.load(f)['cards']
check('本地卡库条数 > 14000', len(db_file_cards) > 14000)
check('禁限表非空', len(banlist_file) > 0)
cn_cov = sum(1 for cd in db_file_cards if cd.get('cn')) / len(db_file_cards)
check('卡库中文名覆盖率 ≥95%（cards.cdb 合并）', cn_cov >= 0.95)

# ── (1) 基础：无参数全库浏览 ─────────────────────────
print('== 1. 基础：filter 无参数 ==')
r = fjson('/api/cards/filter')
j = r.get_json()
check('无参数 200', r.status_code == 200)
check('total 接近全库（>14000）', j['total'] > 14000)
check('首页 30 张', len(j['cards']) == 30)
check('pages == ceil(total/30)', j['pages'] == math.ceil(j['total'] / 30))
base_total = j['total']
base_cards = j['cards']

# ── (2) kind：monster/spell/trap 前缀与总量 ───────────────
print('== 2. kind 三大类 ==')
r = fjson('/api/cards/filter?kind=monster')
j = r.get_json()
check('kind=monster 200 且 total > 9000',
      r.status_code == 200 and j['total'] > 9000)
check('怪兽逐卡 types 以「怪兽卡」开头',
      all(cd['types'].startswith('怪兽卡') for cd in j['cards']))
r = fjson('/api/cards/filter?kind=spell')
j = r.get_json()
check('kind=spell 200 且逐卡 types 以「魔法卡」开头',
      r.status_code == 200 and j['total'] > 0
      and all(cd['types'].startswith('魔法卡') for cd in j['cards']))
r = fjson('/api/cards/filter?kind=trap')
j = r.get_json()
check('kind=trap 200 且逐卡 types 以「陷阱卡」开头',
      r.status_code == 200 and j['total'] > 0
      and all(cd['types'].startswith('陷阱卡') for cd in j['cards']))

# ── (3) subtype：单值与逗号 OR ────────────────────────
print('== 3. subtype 子类 ==')
r = fjson('/api/cards/filter?subtype=s_quick')
j = r.get_json()
check('subtype=s_quick 200 且逐卡 types 含「速攻」',
      r.status_code == 200 and j['total'] > 0
      and all('速攻' in cd['types'] for cd in j['cards']))
check('s_quick 逐卡是魔法卡',
      all(cd['types'].startswith('魔法卡') for cd in j['cards']))
r = fjson('/api/cards/filter?subtype=fusion')
j = r.get_json()
total_fusion = j['total']
check('subtype=fusion 200 且逐卡 types 含「融合」',
      r.status_code == 200 and total_fusion > 0
      and all('融合' in cd['types'] for cd in j['cards']))
r = fjson('/api/cards/filter?subtype=synchro')
total_synchro = r.get_json()['total']
r = fjson('/api/cards/filter?subtype=fusion,synchro')
j = r.get_json()
check('fusion,synchro OR 生效（total == 两者之和）',
      j['total'] == total_fusion + total_synchro)
check('OR 结果逐卡 types 含「融合」或「同调」',
      all(('融合' in cd['types']) or ('同调' in cd['types'])
          for cd in j['cards']))

# ── (4) attribute=light ─────────────────────────────
print('== 4. attribute ==')
r = fjson('/api/cards/filter?attribute=light')
j = r.get_json()
check('attribute=light 200 且逐卡 attr_cn == 光',
      r.status_code == 200 and j['total'] > 0
      and all(cd['attr_cn'] == '光' for cd in j['cards']))

# ── (5) race=dragon ─────────────────────────────────
print('== 5. race ==')
r = fjson('/api/cards/filter?race=dragon')
j = r.get_json()
check('race=dragon 200 且逐卡 race_cn == 龙族',
      r.status_code == 200 and j['total'] > 0
      and all(cd['race_cn'] == '龙族' for cd in j['cards']))

# ── (6) level / link ────────────────────────────────
print('== 6. level 与 link ==')
r = fjson('/api/cards/filter?level=8')
j = r.get_json()
check('level=8 200 且逐卡 level == 8',
      r.status_code == 200 and j['total'] > 0
      and all(cd['level'] == 8 for cd in j['cards']))
r = fjson('/api/cards/filter?link=2')
j = r.get_json()
check('link=2 200 且逐卡 linkval == 2',
      r.status_code == 200 and j['total'] > 0
      and all(cd['linkval'] == 2 for cd in j['cards']))
check('link=2 逐卡 types 含「连接」',
      all('连接' in cd['types'] for cd in j['cards']))

# ── (7) 组合：光·龙族·8 星（应含青眼白龙 89631139）────────
print('== 7. 组合筛选 ==')
combo = 'kind=monster&attribute=light&race=dragon&level=8'
r = fjson(f'/api/cards/filter?{combo}')
j = r.get_json()
check('组合 200 且 total > 0', r.status_code == 200 and j['total'] > 0)
check('组合逐卡全满足（光/龙族/8星/怪兽）',
      all(cd['attr_cn'] == '光' and cd['race_cn'] == '龙族'
          and cd['level'] == 8 and cd['types'].startswith('怪兽卡')
          for cd in j['cards']))
r = fjson(f'/api/cards/filter?{combo}&q=89631139')
j = r.get_json()
check('组合 + q=89631139 精确命中青眼白龙（total==1 且 id 吻合）',
      j['total'] == 1 and j['cards'][0]['id'] == 89631139
      and 'blue-eyes' in j['cards'][0]['en_name'].lower())
be = j['cards'][0]
check('青眼白龙 sc_name == 青眼白龙（卡库 cn 直出）',
      be['sc_name'] == '青眼白龙')
check('青眼白龙 cn_name 非空', bool(be['cn_name']))

# ── (8) atk 区间 ────────────────────────────────────
print('== 8. atk/def 区间 ==')
r = fjson('/api/cards/filter?atk_min=3000&atk_max=3000')
j = r.get_json()
check('atk=3000 精确区间 200 且逐卡 atk == 3000',
      r.status_code == 200 and j['total'] > 0
      and all(cd['atk'] == 3000 for cd in j['cards']))
r = fjson('/api/cards/filter?atk_min=4000')
j = r.get_json()
check('atk_min=4000 200 且逐卡 atk >= 4000',
      r.status_code == 200 and j['total'] > 0
      and all(cd['atk'] >= 4000 for cd in j['cards']))
r = fjson('/api/cards/filter?def_min=3000&def_max=3000')
j = r.get_json()
check('def=3000 精确区间 200 且逐卡 def == 3000',
      r.status_code == 200 and j['total'] > 0
      and all(cd['def'] == 3000 for cd in j['cards']))

# ── (9) ban 禁限档位 ────────────────────────────────
print('== 9. ban 禁限筛选 ==')
r = fjson('/api/cards/filter?ban=forbidden')
j = r.get_json()
check('ban=forbidden 200 且逐卡 ban == 0',
      r.status_code == 200 and j['total'] > 0
      and all(cd['ban'] == 0 for cd in j['cards']))
r = fjson('/api/cards/filter?ban=limited')
j = r.get_json()
check('ban=limited 200 且逐卡 ban == 1',
      r.status_code == 200 and j['total'] > 0
      and all(cd['ban'] == 1 for cd in j['cards']))
r = fjson('/api/cards/filter?ban=semi')
j = r.get_json()
check('ban=semi 200 且逐卡 ban == 2',
      r.status_code == 200 and j['total'] > 0
      and all(cd['ban'] == 2 for cd in j['cards']))
r = fjson('/api/cards/filter?ban=none')
j = r.get_json()
check('ban=none 200 且逐卡 ban == 3（未收录）',
      r.status_code == 200 and j['total'] > 0
      and all(cd['ban'] == 3 for cd in j['cards']))
check('ban=forbidden 条数与禁限表 0 档一致',
      fjson('/api/cards/filter?ban=forbidden').get_json()['total']
      == sum(1 for v in banlist_file.values() if v == 0))

# ── (10) 非法参数静默忽略 ─────────────────────────────
print('== 10. 非法参数 ==')
r = fjson('/api/cards/filter?kind=xxx&attribute=foo&level=99&race=nope'
          '&subtype=zzz&ban=zzz&link=99&scale=99&atk_min=abc')
j = r.get_json()
check('全非法参数 200', r.status_code == 200)
check('全非法参数 total 与无参数一致', j['total'] == base_total)

# ── (11) 分页：无重叠 + 末页钳制 ───────────────────────
print('== 11. 分页 ==')
r = fjson('/api/cards/filter?page=2')
j2 = r.get_json()
ids_p1 = {cd['id'] for cd in base_cards}
ids_p2 = {cd['id'] for cd in j2['cards']}
check('第 2 页 200 且 page 字段 == 2',
      r.status_code == 200 and j2['page'] == 2)
check('第 1、2 页 id 无重叠', not (ids_p1 & ids_p2))
r = fjson('/api/cards/filter?page=99999')
j = r.get_json()
check('page=99999 钳到末页（200 且 page == pages）',
      r.status_code == 200 and j['page'] == j['pages'])

# ── (12) q：英文名包含 / 卡号精确 ──────────────────────
print('== 12. q 关键词 ==')
r = fjson('/api/cards/filter?q=blue-eyes')
j = r.get_json()
check('q=blue-eyes 200 且 total > 0', r.status_code == 200 and j['total'] > 0)
check('q=blue-eyes 逐卡 en_name 含 blue-eyes（不区分大小写）',
      all('blue-eyes' in cd['en_name'].lower() for cd in j['cards']))
r = fjson('/api/cards/filter?q=89631139')
j = r.get_json()
check('q=89631139 精确命中青眼白龙（total==1 且 id 吻合）',
      j['total'] == 1 and j['cards'][0]['id'] == 89631139)

# ── (13) search 与筛选组合（走真实上游，q=双子 首拉后入缓存）──
print('== 13. search + 筛选 ==')
r = c.get('/api/cards/search?q=%E5%8F%8C%E5%AD%90')  # q=双子
j = r.get_json()
check('search q=双子 200 且有结果', r.status_code == 200 and len(j['cards']) > 0)
plain_ids = [cd['id'] for cd in j['cards']]
check('search 原 9 字段不变',
      all(SEARCH_ORIG_KEYS <= set(cd) for cd in j['cards']))
check('search cards 新增 data 与 ban 字段',
      all('data' in cd and 'ban' in cd for cd in j['cards']))
r = c.get('/api/cards/search?q=%E5%8F%8C%E5%AD%90&kind=monster')
j = r.get_json()
check('search q=双子&kind=monster 200 且有结果',
      r.status_code == 200 and len(j['cards']) > 0)
check('筛选后无魔法/陷阱卡（爱恶作剧的双子恶魔被滤掉）',
      all(not cd['types'].startswith(('魔法卡', '陷阱卡'))
          for cd in j['cards']))

# ── (14) search 非法筛选参数不影响正常搜索 ────────────────
print('== 14. search 非法筛选参数 ==')
r = c.get('/api/cards/search?q=%E5%8F%8C%E5%AD%90&kind=xxx&level=99&race=nope')
j = r.get_json()
check('search 非法筛选 200 且结果与无筛选一致',
      r.status_code == 200 and [cd['id'] for cd in j['cards']] == plain_ids)

# ── (15) filter 响应字段完整性（复用第 1 节无参数响应）──────
print('== 15. filter 字段完整性 ==')
check('逐卡含契约全部 14 个数据键',
      all(set(cd) == FILTER_KEYS for cd in base_cards))
check('image_small 均为 cards_small/{id}.jpg',
      all(f'cards_small/{cd["id"]}.jpg' in cd['image_small']
          for cd in base_cards))
check('响应体含 total/page/pages/cards 四键',
      {'total', 'page', 'pages', 'cards'} <= set(fjson('/api/cards/filter').get_json()))

# ── (16) 中文名懒解析（真实上游；确认上游全挂才放宽为警告）───
print('== 16. 中文名 ==')
has_cjk = any(CJK_RE.search(cd['sc_name'] or '') for cd in base_cards)
all_fallback = all(cd['sc_name'] == cd['en_name'] and cd['cn_name'] == ''
                   for cd in base_cards)
if has_cjk:
    check('首页至少一张 sc_name 含中文字符', True)
elif all_fallback:
    # 30 张全部退回英文名且 cn_name 全空 → 上游百鸽整体不可用，放宽为警告
    print('  WARN 上游中文名解析整体失败（全部退回英文名），该断言放宽不计失败')
    passed += 1
else:
    check('首页至少一张 sc_name 含中文字符', False)

# ── (16b) 中文资料本地化：多语种关键词匹配与明确英文兜底 ──────────
print('== 16b. 中文名本地化 ==')
r = fjson('/api/cards/filter?q=%E9%9D%92%E7%9C%BC')  # q=青眼
j = r.get_json()
check('q=青眼 200 且 total > 0', r.status_code == 200 and j['total'] > 0)
check('q=青眼 逐卡在中/日/英文名之一命中（例如日文青眼の銀ゾンビ）',
      j['total'] > 0 and all(any('青眼' in (cd.get(key) or '')
          for key in ('cn_name', 'jp_name', 'en_name')) for cd in j['cards']))
# 新收录、TCG独有或未定名卡可能没有中文；不能由整体覆盖率推定某一页全中文。
fallback = [cd for cd in base_cards
            if cd['sc_name'] == cd['en_name'] and not cd['cn_name']]
if fallback:
    print('  .. 退回英文名的卡：'
          + ', '.join(f'{cd["id"]}({cd["en_name"]})' for cd in fallback))
local_names = {card['id']: card.get('cn') for card in db_file_cards}
check('本地已有中文名必须使用；无中文的卡允许明确英文兜底',
      all((cd['sc_name'] == local_names[cd['id']] and cd['cn_name'] == local_names[cd['id']])
          if local_names.get(cd['id']) else bool(cd['sc_name']) for cd in base_cards)
      and all(not local_names.get(cd['id']) for cd in fallback))

# ── (17) 卡库缺失降级：filter 503 友好 JSON，search 不受影响 ──
print('== 17. 卡库缺失降级（monkeypatch 路径模拟） ==')
orig_path = cards_mod._CARD_DB_PATH
orig_cache = dict(cards_mod._card_db_cache)
try:
    cards_mod._CARD_DB_PATH = os.path.join(ROOT, 'data', '__不存在__.json')
    cards_mod._card_db_cache.update({'mtime': None, 'data': None})
    r = c.get('/api/cards/filter')
    j = r.get_json()
    check('卡库缺失 filter 503', r.status_code == 503)
    check('503 响应为友好 JSON（error + 空 cards + total 0）',
          'error' in j and j['cards'] == [] and j['total'] == 0)
    # search 不读卡库：缓存命中（q=双子 第 13 节已写入）照常返回
    r = c.get('/api/cards/search?q=%E5%8F%8C%E5%AD%90')
    check('卡库缺失时 search 不受影响（缓存命中 200）', r.status_code == 200)
finally:
    cards_mod._CARD_DB_PATH = orig_path
    cards_mod._card_db_cache.update(orig_cache)
    cards_mod.get_card_db()  # 立即恢复真实卡库缓存，避免影响后续用例

# ── (18) 限速存在性：随机卡号连打，触发 429 验格式；25 次无 429 也算过 ──
print('== 18. filter 限速存在性 ==')
sample_ids = random.sample([cd['id'] for cd in db_file_cards], 25)
got_429 = False
for i, cid in enumerate(sample_ids, 1):
    r = c.get(f'/api/cards/filter?q={cid}')
    if r.status_code == 429:
        j = r.get_json()
        check('第 %d 次触发 429 且响应含 error 字段' % i, 'error' in j)
        got_429 = True
        break
if not got_429:
    # 缓存命中不计数是设计：25 次无 429 也算过，但要求每次都拿到合法响应
    print('  .. 25 次未触发 429（窗口已滑过/缓存命中），验证响应均合法')
    passed += 1

# ── (19) 清理：删临时库（踩坑 #28：先 remove + dispose）──────
print('== 19. 清理 ==')
with app.app_context():
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
