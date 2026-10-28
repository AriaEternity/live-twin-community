"""站内搜卡：百鸽 API（中英文模糊搜索）+ SQLite 查询缓存（7 天 TTL）
条件筛选：本地卡库 data/cards_db.json（mtime 热重载，scripts/fetch_card_db.py 抓取）
+ /api/cards/filter 纯筛选浏览；/api/cards/search 支持与文字搜索组合（按 ygopro
数值编码过滤）。"""
import json
import math
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import CardQueryCache, db
from routes.decks import (MAX_CARD_ID, canonical_card_id, get_banlist,
                          get_banlist_aliases, get_banlist_status)
from security import rate_limit

cards_bp = Blueprint('cards', __name__)

CACHE_TTL = timedelta(days=7)
YGCDB_API = 'https://ygocdb.com/api/v0/?search='
MAX_RESULTS = 30
MAX_QUERY_LEN = 64
FILTER_PAGE_SIZE = 30     # 筛选端点每页条数


# ── 翻译表（简体中文；key 同时是筛选参数的合法取值）────────
# 属性（YGOPRODeck attribute 显示串归一化后的 key）
ATTRIBUTE_CN = {
    'earth': '地', 'water': '水', 'fire': '炎', 'wind': '风',
    'light': '光', 'dark': '暗', 'divine': '神',
}

# 怪兽 26 族（race 显示串归一化后的 key）
RACE_CN = {
    'warrior': '战士族', 'spellcaster': '魔法师族', 'fairy': '天使族',
    'fiend': '恶魔族', 'zombie': '不死族', 'machine': '机械族',
    'aqua': '水族', 'pyro': '炎族', 'rock': '岩石族',
    'winged_beast': '鸟兽族', 'plant': '植物族', 'insect': '昆虫族',
    'thunder': '雷族', 'dragon': '龙族', 'beast': '兽族',
    'beast_warrior': '兽战士族', 'dinosaur': '恐龙族', 'fish': '鱼族',
    'sea_serpent': '海龙族', 'reptile': '爬虫类族', 'psychic': '念动力族',
    'divine_beast': '幻神兽族', 'creator_god': '创造神族', 'wyrm': '幻龙族',
    'cyberse': '电子界族', 'illusion': '幻想魔族',
}

# 怪兽子类（从 YGOPRODeck type 字符串关键词派生，值为中文名）
MONSTER_SUBTYPE_CN = {
    'fusion': '融合', 'ritual': '仪式', 'synchro': '同调', 'xyz': '超量',
    'link': '连接', 'pendulum': '灵摆', 'tuner': '调整', 'flip': '翻转',
    'union': '同盟', 'gemini': '二重', 'spirit': '灵魂', 'toon': '卡通',
    'effect': '效果', 'normal': '通常',
}
# types 显示串的拼接顺序（通常/效果打头，其余按常见习惯排序）
_MONSTER_SUBTYPE_ORDER = [
    'normal', 'effect', 'fusion', 'ritual', 'synchro', 'xyz', 'link',
    'pendulum', 'tuner', 'flip', 'union', 'gemini', 'spirit', 'toon',
]

# 魔法/陷阱子类（race 字段派生；值为完整中文名）
SPELL_SUBTYPE_CN = {
    's_normal': '通常魔法', 's_quick': '速攻魔法', 's_continuous': '永续魔法',
    's_equip': '装备魔法', 's_field': '场地魔法', 's_ritual': '仪式魔法',
}
TRAP_SUBTYPE_CN = {
    't_normal': '通常陷阱', 't_continuous': '永续陷阱', 't_counter': '反击陷阱',
}
# types 显示串用的短名（「魔法卡 速攻」「陷阱卡 反击」）
_SPELL_SUBTYPE_SHORT = {k: v[:-2] for k, v in SPELL_SUBTYPE_CN.items()}
_TRAP_SUBTYPE_SHORT = {k: v[:-2] for k, v in TRAP_SUBTYPE_CN.items()}

# 魔法/陷阱 race 显示串归一化 → 子类 key
_SPELL_RACE_KEY = {
    'normal': 's_normal', 'quick_play': 's_quick', 'continuous': 's_continuous',
    'equip': 's_equip', 'field': 's_field', 'ritual': 's_ritual',
}
_TRAP_RACE_KEY = {
    'normal': 't_normal', 'continuous': 't_continuous', 'counter': 't_counter',
}

# 禁限筛选参数 → get_banlist() 状态值（3=未收录）
_BAN_FACET = {'forbidden': 0, 'limited': 1, 'semi': 2, 'none': 3}

# ── ygopro ocgcore 数值编码（百鸽 data 字段；已实测验证）────────
# 实测：增殖的Z type=33(0x1|0x20) race=0x800(昆虫) attr=0x01(地)；
# 青眼白龙 type=17(0x1|0x10) race=0x2000(龙) attr=0x10(光)；
# 爱恶作剧的双子恶魔 type=2(魔法)；星尘龙 type=8225(含0x2000同调)；
# 编号102 type=8388641(含0x800000超量)；速攻魔法 type=65538(0x2|0x10000)；
# 幻想魔族融合怪 race=0x2000000（存疑常量已实测坐实）。
YG_ATTRIBUTE = {
    'earth': 0x01, 'water': 0x02, 'fire': 0x04, 'wind': 0x08,
    'light': 0x10, 'dark': 0x20, 'divine': 0x40,
}
YG_RACE = {
    'warrior': 0x1, 'spellcaster': 0x2, 'fairy': 0x4, 'fiend': 0x8,
    'zombie': 0x10, 'machine': 0x20, 'aqua': 0x40, 'pyro': 0x80,
    'rock': 0x100, 'winged_beast': 0x200, 'plant': 0x400, 'insect': 0x800,
    'thunder': 0x1000, 'dragon': 0x2000, 'beast': 0x4000,
    'beast_warrior': 0x8000, 'dinosaur': 0x10000, 'fish': 0x20000,
    'sea_serpent': 0x40000, 'reptile': 0x80000, 'psychic': 0x100000,
    'divine_beast': 0x200000, 'creator_god': 0x400000, 'wyrm': 0x800000,
    'cyberse': 0x1000000, 'illusion': 0x2000000,
}
YG_TYPE_MONSTER = {
    'normal': 0x10, 'effect': 0x20, 'fusion': 0x40, 'ritual': 0x80,
    'spirit': 0x200, 'union': 0x400, 'gemini': 0x800, 'tuner': 0x1000,
    'synchro': 0x2000, 'flip': 0x200000, 'toon': 0x400000,
    'xyz': 0x800000, 'pendulum': 0x1000000, 'link': 0x4000000,
}
YG_TYPE_SPELL = {
    's_ritual': 0x80, 's_quick': 0x10000, 's_continuous': 0x20000,
    's_equip': 0x40000, 's_field': 0x80000,
}
YG_TYPE_TRAP = {'t_continuous': 0x20000, 't_counter': 0x100000}
_YG_SPELL_SUB_MASK = 0x80 | 0x10000 | 0x20000 | 0x40000 | 0x80000
_YG_TRAP_SUB_MASK = 0x20000 | 0x100000


def _normalize(card):
    """把百鸽返回的原始卡片 dict 裁剪成前端需要的字段。
    data 为 ygopro 数值编码原样透传（筛选用）；ban 为 OCG 禁限档位（3=未收录）。"""
    text = card.get('text') or {}
    cid = card.get('id')
    return {
        'id': cid,
        'sc_name': card.get('sc_name') or card.get('cn_name') or '',
        'cn_name': card.get('cn_name') or '',
        'en_name': card.get('en_name') or '',
        'jp_name': card.get('jp_name') or '',
        'types': text.get('types', ''),
        'pdesc': text.get('pdesc', ''),
        'desc': text.get('desc', ''),
        'image_small': f'https://images.ygoprodeck.com/images/cards_small/{cid}.jpg',
        'data': card.get('data') or {},
        'ban': get_banlist().get(str(canonical_card_id(cid)), 3),
    }


def _fetch_remote(q):
    url = YGCDB_API + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={'User-Agent': 'LiveTwin-FanSite/1.0'})
    # 超时压到 5s：线上 gunicorn 仅 2 个 worker，慢上游下 2 个 15s 并发请求
    # 就能打满全部 worker 拖垮整站；urllib 只支持单值超时（连接+读取共用）
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.load(resp)
    return [_normalize(c) for c in (data.get('result') or [])][:MAX_RESULTS]


def _fresh(cache_row):
    created = cache_row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created < CACHE_TTL


# ── 本地卡库（data/cards_db.json，mtime 热重载）────────────
_CARD_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'data', 'cards_db.json')
_card_db_cache = {'mtime': None, 'data': None}


def get_card_db():
    """取本地全量卡库（压缩卡片列表），mtime 变化时热重载。
    文件缺失/损坏时降级为 None（筛选端点返回 503，不影响既有搜卡）。
    """
    try:
        mtime = os.path.getmtime(_CARD_DB_PATH)
    except OSError:
        mtime = None
    if mtime is not None and mtime == _card_db_cache['mtime']:
        return _card_db_cache['data']
    data = None
    if mtime is not None:
        try:
            with open(_CARD_DB_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            data = raw['cards']
        except (ValueError, OSError, KeyError, TypeError) as e:
            print(f'[cards] 警告：卡库 {_CARD_DB_PATH} 损坏（{e}），筛选功能降级')
            data = None
    else:
        print(f'[cards] 警告：卡库 {_CARD_DB_PATH} 不存在，筛选功能降级')
    _card_db_cache['mtime'] = mtime
    _card_db_cache['data'] = data
    return data


# ── 上游故障时的本地文本快照（只读，不写入远端查询缓存）─────
_CARD_TEXTS_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'data', 'card_texts.json')
_card_text_cache = {'signature': None, 'checked': False, 'cards': [], 'fetched': None}
_TEXT_NAME_FIELDS = ('sc_name', 'cn_name', 'jp_name', 'en_name')


def get_card_text_snapshot():
    """读取有原文的快照记录；缺失或损坏时返回空快照，不影响既有缓存。"""
    try:
        stat = os.stat(_CARD_TEXTS_PATH)
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    if (_card_text_cache.get('checked')
            and signature == _card_text_cache.get('signature')):
        return _card_text_cache.get('cards', []), _card_text_cache.get('fetched')
    cards, fetched = [], None
    try:
        with open(_CARD_TEXTS_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        rows = raw.get('cards') if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            raise ValueError('文本快照格式有误')
        for card in rows:
            if not isinstance(card, dict):
                continue
            cid = card.get('id')
            text, data = card.get('text'), card.get('data')
            if (type(cid) is not int or not 1 <= cid <= MAX_CARD_ID
                    or not isinstance(text, dict) or not isinstance(data, dict)):
                continue
            if any(not isinstance(card.get(field, ''), str) for field in _TEXT_NAME_FIELDS):
                continue
            if any(not isinstance(text.get(field, ''), str)
                   for field in ('types', 'pdesc', 'desc')):
                continue
            if any(field in data and type(data[field]) is not int
                   for field in ('type', 'race', 'attribute', 'level', 'atk', 'def')):
                continue
            cards.append(card)
        meta = raw.get('meta')
        value = meta.get('fetched') if isinstance(meta, dict) else None
        if isinstance(value, str):
            try:
                fetched = datetime.strptime(value[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
            except ValueError:
                pass
    except (ValueError, OSError, TypeError):
        cards, fetched = [], None
    _card_text_cache.update({'signature': signature, 'checked': True,
                             'cards': cards, 'fetched': fetched})
    return cards, fetched


def _search_local_texts(q):
    """按密码（含异画）或四种卡名匹配；仅返回快照中实际存在的原始记录。"""
    cards, fetched = get_card_text_snapshot()
    aliases = get_banlist_aliases()
    qid = int(q) if q.isascii() and q.isdigit() and len(q) <= 16 else None
    canonical_qid = aliases.get(str(qid), qid) if qid is not None else None
    folded = q.casefold()
    by_id, by_name = [], []
    for card in cards:
        if (canonical_qid is not None
                and aliases.get(str(card['id']), card['id']) == canonical_qid):
            by_id.append(card)
        elif any(folded in card.get(field, '').casefold() for field in _TEXT_NAME_FIELDS):
            by_name.append(card)
    by_id.sort(key=lambda card: card['id'] != qid)
    return [_normalize(card) for card in (by_id + by_name)[:MAX_RESULTS]], fetched


# ── 筛选参数解析与匹配 ─────────────────────────────

def _norm_token(s):
    """显示字符串归一化：小写、连字符/空格转下划线（"Beast-Warrior"→beast_warrior）"""
    return (s or '').strip().lower().replace('-', ' ').replace(' ', '_')


def _to_int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _parse_facets(args):
    """解析 /api/cards/filter 与 /api/cards/search 共用的筛选参数。
    非法值忽略该项；返回只含有效项的 dict。
    """
    f = {}
    kind = (args.get('kind') or '').strip().lower()
    if kind in ('monster', 'spell', 'trap'):
        f['kind'] = kind
    subs = {s.strip().lower() for s in (args.get('subtype') or '').split(',')
            if s.strip()}
    subs &= (set(MONSTER_SUBTYPE_CN) | set(SPELL_SUBTYPE_CN)
             | set(TRAP_SUBTYPE_CN))
    if subs:
        f['subtype'] = subs
    attr = _norm_token(args.get('attribute'))
    if attr in ATTRIBUTE_CN:
        f['attribute'] = attr
    race = _norm_token(args.get('race'))
    if race in RACE_CN:
        f['race'] = race
    for name, lo, hi in (('level', 1, 12), ('link', 1, 6), ('scale', 0, 13)):
        v = _to_int(args.get(name))
        if v is not None and lo <= v <= hi:
            f[name] = v
    for name in ('atk_min', 'atk_max', 'def_min', 'def_max'):
        v = _to_int(args.get(name))
        if v is not None:
            f[name] = v
    ban = (args.get('ban') or '').strip().lower()
    if ban in _BAN_FACET:
        f['ban'] = ban
    return f


def _db_kind(card):
    """本地卡库卡片的种类：monster/spell/trap/other（Token 等归 other）"""
    ty = card['ty'].lower()
    if 'spell card' in ty:
        return 'spell'
    if 'trap card' in ty:
        return 'trap'
    if 'monster' in ty:
        return 'monster'
    return 'other'


def _db_subtypes(card, kind):
    """本地卡库卡片的子类 key 集合（怪兽取 type 关键词，魔法/陷阱取 race）"""
    if kind == 'monster':
        ty = card['ty'].lower()
        return {k for k in MONSTER_SUBTYPE_CN if k in ty}
    if kind == 'spell':
        key = _SPELL_RACE_KEY.get(_norm_token(card['ra']))
        return {key} if key else set()
    if kind == 'trap':
        key = _TRAP_RACE_KEY.get(_norm_token(card['ra']))
        return {key} if key else set()
    return set()


def _match_db(card, f, banlist):
    """本地卡库卡片是否满足筛选条件"""
    kind = _db_kind(card)
    if kind == 'other':
        return False
    if f.get('kind') and kind != f['kind']:
        return False
    if f.get('subtype') and not (_db_subtypes(card, kind) & f['subtype']):
        return False
    if f.get('attribute') and _norm_token(card['at']) != f['attribute']:
        return False
    if f.get('race'):
        if kind != 'monster' or _norm_token(card['ra']) != f['race']:
            return False
    # 星级/阶级直接比对；连接怪兽 lv=0 天然不匹配 1-12
    if 'level' in f and card['lv'] != f['level']:
        return False
    if 'link' in f and card['lnk'] != f['link']:
        return False
    if 'scale' in f and card['sc'] != f['scale']:
        return False
    for lo_key, hi_key, val in (('atk_min', 'atk_max', card['atk']),
                                ('def_min', 'def_max', card['def'])):
        if lo_key in f or hi_key in f:
            if val < 0:   # 未知值（? 攻击力/连接怪兽防御）不满足任何区间
                return False
            if lo_key in f and val < f[lo_key]:
                return False
            if hi_key in f and val > f[hi_key]:
                return False
    if f.get('ban'):
        if banlist.get(str(canonical_card_id(card['id'])), 3) != _BAN_FACET[f['ban']]:
            return False
    return True


def _match_remote_data(d, f, banlist, cid):
    """百鸽 _normalize 结果的 data 数值编码是否满足筛选条件。
    注意：ygopro data 无连接数/灵摆刻度字段，link/scale 筛选在本端点不生效。
    """
    t = d.get('type') or 0
    is_monster = bool(t & 0x1)
    is_spell = bool(t & 0x2)
    is_trap = bool(t & 0x4)
    kind = ('monster' if is_monster else 'spell' if is_spell
            else 'trap' if is_trap else 'other')
    if kind == 'other':
        return False
    if f.get('kind') and kind != f['kind']:
        return False
    if f.get('subtype'):
        hit = False
        for s in f['subtype']:
            if s in YG_TYPE_MONSTER:
                hit = is_monster and bool(t & YG_TYPE_MONSTER[s])
            elif s in YG_TYPE_SPELL:
                hit = is_spell and bool(t & YG_TYPE_SPELL[s])
            elif s == 's_normal':
                hit = is_spell and not (t & _YG_SPELL_SUB_MASK)
            elif s in YG_TYPE_TRAP:
                hit = is_trap and bool(t & YG_TYPE_TRAP[s])
            elif s == 't_normal':
                hit = is_trap and not (t & _YG_TRAP_SUB_MASK)
            if hit:
                break
        if not hit:
            return False
    if f.get('attribute'):
        if not (d.get('attribute') or 0) & YG_ATTRIBUTE[f['attribute']]:
            return False
    if f.get('race'):
        if not (d.get('race') or 0) & YG_RACE[f['race']]:
            return False
    if 'level' in f and (d.get('level') or 0) != f['level']:
        return False
    for lo_key, hi_key, val in (('atk_min', 'atk_max', d.get('atk')),
                                ('def_min', 'def_max', d.get('def'))):
        if lo_key in f or hi_key in f:
            if val is None or val < 0:   # 未知值不满足任何区间
                return False
            if lo_key in f and val < f[lo_key]:
                return False
            if hi_key in f and val > f[hi_key]:
                return False
    if f.get('ban'):
        if banlist.get(str(canonical_card_id(cid)), 3) != _BAN_FACET[f['ban']]:
            return False
    return True


def _filter_search_results(cards, facets):
    """对搜卡结果按筛选条件过滤（缓存仍按裸 q 存未过滤结果）"""
    banlist = get_banlist()
    return [c for c in cards
            if _match_remote_data(c.get('data') or {}, facets, banlist,
                                  c.get('id'))]


def _with_current_ban_status(cards):
    """按当前禁限表生成响应副本，避免查询缓存保留旧档位或被原地改写。"""
    banlist = get_banlist()
    return [dict(card, ban=banlist.get(str(canonical_card_id(card.get('id'))), 3))
            for card in cards]


# ── 搜卡页面 ────────────────────────────────────────

@cards_bp.route('/cards')
def search_page():
    return render_template('cards/search.html')


# ── 搜卡 JSON API ───────────────────────────────────

@cards_bp.route('/api/cards/search')
def search_api():
    q = (request.args.get('q') or '').strip()[:MAX_QUERY_LEN]
    if not q:
        return jsonify({'error': '查询词不能为空', 'cards': []}), 400
    if not get_banlist_status()['available']:
        return jsonify({'error': '禁限规则暂不可用，请稍后重试', 'cards': []}), 503

    facets = _parse_facets(request.args)

    # 先查未过期缓存：命中直接返回，不消耗限速额度
    cached = CardQueryCache.query.filter_by(keyword=q).first()
    if cached and _fresh(cached):
        cards = json.loads(cached.result_json)
        # 旧缓存（本功能上线前写入）没有 data 字段，带筛选时落到下方重新拉取
        if facets and not all('data' in c for c in cards):
            pass
        else:
            if facets:
                cards = _filter_search_results(cards, facets)
            return jsonify({'cards': _with_current_ban_status(cards),
                            'cached': True})

    # 搜索限速：每 IP 每分钟 30 次（仅确实要请求上游百鸽 API 时计数）
    if rate_limit('search', 30, 60):
        return jsonify({'error': '查询过于频繁，请稍后再试', 'cards': []}), 429

    try:
        cards = _fetch_remote(q)
    except Exception:
        # 上游不可用：有旧缓存则降级返回旧数据
        if cached:
            stale = json.loads(cached.result_json)
            if facets and all('data' in c for c in stale):
                stale = _filter_search_results(stale, facets)
            elif facets:
                # 旧缓存无 data 无法筛选，宁可报错也不返回未过滤结果误导用户
                return jsonify({'error': '卡片数据库暂时不可用，请稍后再试',
                                'cards': []}), 502
            return jsonify({'cards': _with_current_ban_status(stale),
                            'cached': True, 'stale': True})
        local_cards, snapshot_date = _search_local_texts(q)
        if local_cards:
            if facets:
                local_cards = _filter_search_results(local_cards, facets)
            label = snapshot_date or '日期未记录'
            return jsonify({
                'cards': _with_current_ban_status(local_cards), 'cached': False,
                'local_snapshot': True, 'snapshot_date': snapshot_date,
                'notice': f'上游查询暂不可用，当前显示本地文本快照（抓取日期：{label}），结果可能缺少之后的更新。',
            })
        return jsonify({'error': '卡片数据库暂时不可用，请稍后再试', 'cards': []}), 502

    payload = json.dumps(cards, ensure_ascii=False)
    if cached:
        cached.result_json = payload
        cached.created_at = datetime.now(timezone.utc)
    else:
        db.session.add(CardQueryCache(keyword=q, result_json=payload))
    try:
        db.session.commit()
    except IntegrityError:
        # 并发同词查询撞 keyword 唯一约束：回滚后按已有缓存处理
        db.session.rollback()
    except SQLAlchemyError:
        # 其他写库异常（如 database is locked）：回滚，写缓存失败不影响返回结果
        db.session.rollback()
    if facets:
        cards = _filter_search_results(cards, facets)
    return jsonify({'cards': _with_current_ban_status(cards), 'cached': False})


# ── 条件筛选 JSON API ───────────────────────────────

def _resolve_cn_names(page_ids):
    """cdb 未收录卡（TCG 独有等）的中文名兜底：懒解析——先查缓存，
    未命中并发补拉上游并写缓存。卡库 cn 字段（萌卡 cards.cdb 合并）覆盖
    约 98%，本函数只处理剩余缺失 id。
    返回 {id: 规范化卡片 dict 或 None}；None 表示上游失败（调用方退回英文名）。
    仅在确有未命中缓存的 id 时才计限速（filter：每 IP 每分钟 20 次）。
    """
    key = [str(i) for i in page_ids]
    rows = CardQueryCache.query.filter(
        CardQueryCache.keyword.in_(key)).all() if key else []
    row_map = {r.keyword: r for r in rows}

    names = {}
    unresolved = []
    for i in page_ids:
        row = row_map.get(str(i))
        card = None
        if row and _fresh(row):
            try:
                for c in json.loads(row.result_json):
                    if c.get('id') == i:
                        card = c
                        break
            except ValueError:
                card = None
        if card is not None:
            names[i] = card
        else:
            unresolved.append(i)

    if not unresolved:
        return names, None

    # 需要请求上游才计限速；超限直接返回 None 让端点回 429
    if rate_limit('filter', 20, 60):
        return names, 'rate_limited'

    def _fetch_one(i):
        try:
            return i, _fetch_remote(str(i))
        except Exception:
            return i, None

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_fetch_one, i) for i in unresolved]
        for fut in as_completed(futures):
            i, cards = fut.result()
            if not cards:
                names[i] = None
                continue
            hit = next((c for c in cards if c.get('id') == i), None)
            names[i] = hit
            # 写缓存（照搜卡逻辑：keyword=str(id)，7 天 TTL，撞唯一约束回滚）
            row = row_map.get(str(i))
            payload = json.dumps(cards, ensure_ascii=False)
            if row:
                row.result_json = payload
                row.created_at = datetime.now(timezone.utc)
            else:
                db.session.add(CardQueryCache(keyword=str(i),
                                              result_json=payload))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            except SQLAlchemyError:
                db.session.rollback()
    return names, None


def _types_label(card, kind):
    """组成中文 types 显示串：「怪兽卡 效果·调整」「魔法卡 速攻」「陷阱卡 反击」"""
    subs = _db_subtypes(card, kind)
    if kind == 'monster':
        parts = [MONSTER_SUBTYPE_CN[k] for k in _MONSTER_SUBTYPE_ORDER
                 if k in subs]
        return '怪兽卡' + (' ' + '·'.join(parts) if parts else '')
    if kind == 'spell':
        short = next((_SPELL_SUBTYPE_SHORT[k] for k in subs), '')
        return '魔法卡' + (' ' + short if short else '')
    short = next((_TRAP_SUBTYPE_SHORT[k] for k in subs), '')
    return '陷阱卡' + (' ' + short if short else '')


@cards_bp.route('/api/cards/filter')
def filter_api():
    if not get_banlist_status()['available']:
        return jsonify({'error': '禁限规则暂不可用，请稍后重试',
                        'total': 0, 'page': 1, 'pages': 0, 'cards': []}), 503
    card_db = get_card_db()
    if card_db is None:
        return jsonify({'error': '卡库数据暂不可用，请稍后再试',
                        'total': 0, 'page': 1, 'pages': 0, 'cards': []}), 503

    q = (request.args.get('q') or '').strip()[:MAX_QUERY_LEN]
    facets = _parse_facets(request.args)
    banlist = get_banlist()

    ql = q.lower()
    qid = int(q) if q.isdigit() else None

    def _match_q(c):
        if not q:
            return True
        if qid is not None and canonical_card_id(c['id']) == canonical_card_id(qid):
            return True
        # 中文名（卡库 cn 字段）、英文名、日文名 OR 匹配
        return (ql in c['en'].lower()
                or (c['jp'] and ql in c['jp'].lower())
                or (c.get('cn') and ql in c['cn'].lower()))

    matched = [c for c in card_db
               if _match_db(c, facets, banlist) and _match_q(c)]

    total = len(matched)
    pages = max(1, math.ceil(total / FILTER_PAGE_SIZE))
    page = _to_int(request.args.get('page')) or 1
    page = min(max(page, 1), pages)
    page_cards = matched[(page - 1) * FILTER_PAGE_SIZE: page * FILTER_PAGE_SIZE]

    # 中文名优先取卡库 cn 字段（百鸽公开资料合并，覆盖约 98%）；
    # 只有缺 cn 的 id 才走懒解析（缓存+并发上游）兜底
    no_cn_ids = [c['id'] for c in page_cards if not c.get('cn')]
    if no_cn_ids:
        names, err = _resolve_cn_names(no_cn_ids)
        if err == 'rate_limited':
            return jsonify({'error': '查询过于频繁，请稍后再试',
                            'total': total, 'page': page, 'pages': pages,
                            'cards': []}), 429
    else:
        names = {}

    out = []
    for c in page_cards:
        kind = _db_kind(c)
        cn = names.get(c['id']) or {}
        db_cn = c.get('cn') or ''
        out.append({
            'id': c['id'],
            # sc_name 选择顺序：卡库 cn → 懒解析缓存 → 英文名（上游失败时 cn_name 留空）
            'sc_name': db_cn or cn.get('sc_name') or c['en'],
            'cn_name': db_cn or cn.get('cn_name') or '',
            'en_name': c['en'],
            'jp_name': cn.get('jp_name') or c['jp'],
            'types': _types_label(c, kind),
            'race_cn': (RACE_CN.get(_norm_token(c['ra']), '')
                        if kind == 'monster' else ''),
            'attr_cn': (ATTRIBUTE_CN.get(_norm_token(c['at']), '')
                        if kind == 'monster' else ''),
            'level': c['lv'],
            'linkval': c['lnk'],
            'atk': c['atk'],
            'def': c['def'],
            'image_small': ('https://images.ygoprodeck.com/images/'
                            f'cards_small/{c["id"]}.jpg'),
            'ban': banlist.get(str(canonical_card_id(c['id'])), 3),
        })
    return jsonify({'total': total, 'page': page, 'pages': pages, 'cards': out})
