"""卡组构筑器：卡组广场 / 新建 / 详情 / 编辑 / 删除
卡条目以 JSON 快照入库（{id, name, types}），详情页零外部 API 依赖。
同 id 上限按 OCG 禁限卡表（data/banlist_ocg.json，mtime 热重载）。"""
import json
import os
from datetime import date
from threading import RLock
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, current_app, jsonify)
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from models import db, Deck
from security import rate_limit
from wordfilter import find_sensitive, log_hit

decks_bp = Blueprint('decks', __name__, url_prefix='/decks')

# 广场视图函数按契约命名 list，会遮蔽内建 list；校验处用此别名
_list_type = list

# 红线词拦截统一提示：绝不回显具体命中词
_REDLINE_MSG = '内容包含不适宜发布的词汇，请修改后再试'

# 卡组字段限制
MAX_TITLE_LEN = 60
MAX_DESC_LEN = 2000
MAX_NAME_LEN = 100      # 单卡卡名截断
MAX_TYPES_LEN = 50      # 单卡卡种字符串截断
# 与浏览器 JSON 的整数精度边界一致；本轮不限制卡库存在性或发行状态。
MAX_CARD_ID = 2 ** 53 - 1
MAIN_MIN, MAIN_MAX = 40, 60
EXTRA_MAX, SIDE_MAX = 15, 15
SAME_ID_MAX = 3         # 同一卡 id 三处合计上限（未被禁限表收录时的通用上限）
TOTAL_MAX = 90          # 三处条目总数上限

# 限速：保存/更新卡组每小时 30 次
_RATE_SCOPE = 'deck_save'

# ── OCG 禁限卡表（data/banlist_ocg.json，mtime 热重载）────────
# 数据由 scripts/fetch_banlist.py 抓取（YGOPRODeck API）：
# {"cards": {"<卡id>": 0|1|2}}，0=禁止 1=限制 2=准限制
_BANLIST_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'data', 'banlist_ocg.json')
_banlist_cache = {'signature': None, 'checked': False, 'data': {},
                  'aliases': {}, 'available': False, 'stale': False,
                  'effective_date': None, 'fetched': None}
_banlist_lock = RLock()
_BANLIST_UNAVAILABLE_MSG = '禁限规则暂不可用，当前卡组已保留，请重试加载规则后再保存'


def _valid_card_id_key(value):
    """JSON 映射键只允许浏览器可无损处理的正整数字符串。"""
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and not value.startswith('0') and len(value) <= 16
            and int(value) <= MAX_CARD_ID)


def _safe_rule_date(value):
    """仅公开规范日期，不透传数据源中的任意元信息。"""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            pass
    return None


def get_banlist():
    """载入有效规则；文件更新失败时保留上次有效表，首次失败由调用方明确拒绝。"""
    with _banlist_lock:
        try:
            stat = os.stat(_BANLIST_PATH)
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if (_banlist_cache.get('checked')
                and signature == _banlist_cache.get('signature')):
            return _banlist_cache.get('data', {})
        try:
            with open(_BANLIST_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            cards = raw.get('cards') if isinstance(raw, dict) else None
            aliases = raw.get('aliases', {}) if isinstance(raw, dict) else None
            if not isinstance(cards, dict) or not cards:
                raise ValueError('禁限表缺少有效卡片映射')
            if any(not _valid_card_id_key(k) or type(v) is not int or v not in (0, 1, 2)
                   for k, v in cards.items()):
                raise ValueError('禁限表包含无效卡号或档位')
            if not isinstance(aliases, dict) or any(k in aliases for k in cards) or any(
                    not _valid_card_id_key(k) or type(v) is not int
                    or not 1 <= v <= MAX_CARD_ID or str(v) in aliases
                    for k, v in aliases.items()):
                raise ValueError('异画映射必须直接指向有效主卡号')
            meta = raw.get('meta')
            meta = meta if isinstance(meta, dict) else {}
            _banlist_cache.update({
                'data': dict(cards), 'aliases': dict(aliases), 'available': True,
                'stale': False, 'effective_date': _safe_rule_date(meta.get('effective_date')),
                'fetched': _safe_rule_date(meta.get('fetched')),
            })
        except (ValueError, OSError, TypeError):
            _banlist_cache['stale'] = bool(_banlist_cache.get('available'))
            print('[decks] 禁限规则读取失败，保留上次有效规则；无有效规则时暂停校验')
        _banlist_cache.update({'checked': True, 'signature': signature})
        return _banlist_cache.get('data', {})


def get_banlist_status():
    """公开规则可用性与日期；最后有效规则仍可校验，但必须标明更新待核验。"""
    get_banlist()
    with _banlist_lock:
        return {'available': bool(_banlist_cache.get('available')),
                'effective_date': _banlist_cache.get('effective_date'),
                'fetched': _banlist_cache.get('fetched'),
                'count': len(_banlist_cache.get('data', {})),
                'stale': bool(_banlist_cache.get('stale'))}


def get_banlist_aliases():
    get_banlist()
    return dict(_banlist_cache.get('aliases', {}))


def canonical_card_id(card_id):
    """仅供同卡限额归一；卡组存档和卡图仍保留用户选择的原始图号。"""
    get_banlist()
    return _banlist_cache.get('aliases', {}).get(str(card_id), card_id)


# 禁限档位提示文案（值=有效上限）
_BAN_LABEL = {0: '禁止卡', 1: '限制卡', 2: '准限制卡'}


def _check_redline(kind, fields):
    """红线词硬拦截：依次对 fields（{字段名: 原文}）做敏感词子串匹配。
    命中则写日志并返回 True，未命中返回 False。
    与 routes/forum.py 的同名函数保持一致语义。
    """
    for text in fields.values():
        word = find_sensitive(text)
        if word is not None:
            log_hit(kind, word, text, current_user.username,
                    request.remote_addr or '')
            return True
    return False


def _parse_zone(raw, zone_label):
    """解析一个分区的 JSON 字段，返回 (条目列表, 错误信息)。
    条目规范化为 {'id': int, 'name': str, 'types': str}；
    校验失败返回 (None, 中文错误信息)。
    """
    try:
        data = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return None, f'{zone_label}数据格式有误，请重新选择卡片后再提交'
    if not isinstance(data, _list_type):
        return None, f'{zone_label}数据格式有误，请重新选择卡片后再提交'
    cards = []
    for item in data:
        if not isinstance(item, dict):
            return None, f'{zone_label}包含无效的卡片条目'
        cid = item.get('id')
        # bool 是 int 的子类，须显式排除；不转换字符串、小数或无穷大。
        if (isinstance(cid, bool) or not isinstance(cid, int)
                or not 1 <= cid <= MAX_CARD_ID):
            return None, f'{zone_label}包含无效的卡片条目'
        name = str(item.get('name', ''))[:MAX_NAME_LEN]
        types = str(item.get('types', ''))[:MAX_TYPES_LEN]
        cards.append({'id': cid, 'name': name, 'types': types})
    return cards, None


def _validate_form():
    """从当前请求表单解析并校验卡组数据。
    返回 (title, description, main, extra, side, 错误信息)；
    任一失败时前五个返回值不可信，错误信息非 None。
    """
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()

    if not 1 <= len(title) <= MAX_TITLE_LEN:
        return None, None, None, None, None, '卡组标题须为 1-60 个字符'
    if len(description) > MAX_DESC_LEN:
        return None, None, None, None, None, '卡组简介最多 2000 个字符'

    main, err = _parse_zone(request.form.get('main_json', ''), '主卡组')
    if err:
        return None, None, None, None, None, err
    extra, err = _parse_zone(request.form.get('extra_json', ''), '额外卡组')
    if err:
        return None, None, None, None, None, err
    side, err = _parse_zone(request.form.get('side_json', ''), '副卡组')
    if err:
        return None, None, None, None, None, err

    if not MAIN_MIN <= len(main) <= MAIN_MAX:
        return None, None, None, None, None, \
            f'主卡组须为 {MAIN_MIN}-{MAIN_MAX} 张（当前 {len(main)} 张）'
    if len(extra) > EXTRA_MAX:
        return None, None, None, None, None, \
            f'额外卡组最多 {EXTRA_MAX} 张（当前 {len(extra)} 张）'
    if len(side) > SIDE_MAX:
        return None, None, None, None, None, \
            f'副卡组最多 {SIDE_MAX} 张（当前 {len(side)} 张）'
    if len(main) + len(extra) + len(side) > TOTAL_MAX:
        return None, None, None, None, None, \
            f'卡组总张数超过 {TOTAL_MAX} 张上限'

    # 同一卡 id 三处合计上限：禁限表收录的卡按档位（0 禁止 / 1 限制 / 2 准限制），
    # 未收录的卡沿用通用上限 3 张
    banlist = get_banlist()
    if not get_banlist_status()['available']:
        return None, None, None, None, None, _BANLIST_UNAVAILABLE_MSG
    id_counts = {}
    for card in main + extra + side:
        cid = canonical_card_id(card['id'])
        id_counts[cid] = id_counts.get(cid, 0) + 1
        label = card['name'] or cid
        limit = banlist.get(str(cid))
        if limit is None:
            # 未收录：沿用通用 ≤3 逻辑与文案
            if id_counts[cid] > SAME_ID_MAX:
                return None, None, None, None, None, \
                    f'同一张卡「{label}」在卡组中最多放 {SAME_ID_MAX} 张'
        elif id_counts[cid] > limit:
            if limit == 0:
                return None, None, None, None, None, \
                    f'「{label}」是禁止卡，不能投入卡组'
            return None, None, None, None, None, \
                f'「{label}」是{_BAN_LABEL[limit]}，最多投入 {limit} 张'

    # 红线词硬拦截：标题 + 简介（卡名为官方原文不检查）
    if _check_redline('deck', {'标题': title, '简介': description}):
        return None, None, None, None, None, _REDLINE_MSG

    return title, description, main, extra, side, None


def _edit_context(deck, title, description, main, extra, side):
    """渲染编辑页的公共上下文；deck=None 表示新建。
    校验失败重渲染时回显用户已填的内容。"""
    return dict(deck=deck,
                form={'title': title, 'description': description},
                initial={'main': main or [], 'extra': extra or [],
                         'side': side or []})


def _rate_limited():
    """保存限速：超限返回 True（每小时 30 次）"""
    return rate_limit(_RATE_SCOPE, 30, 3600)


# ── 禁限卡表 API ──────────────────────────────────

@decks_bp.route('/api/banlist')
def banlist_api():
    """规则、同卡映射及安全元信息；无有效规则时明确返回 503。"""
    cards = get_banlist()
    meta = get_banlist_status()
    payload = {'cards': cards, 'aliases': get_banlist_aliases(), 'meta': meta}
    if not meta['available']:
        payload['error'] = _BANLIST_UNAVAILABLE_MSG
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'no-store'
    return response, 200 if meta['available'] else 503


# ── 卡组广场 ──────────────────────────────────────

@decks_bp.route('/')
def list():
    """卡组广场：全部卡组按更新时间倒序分页展示"""
    page = request.args.get('page', 1, type=int)
    pagination = (Deck.query
                  .order_by(Deck.updated_at.desc())
                  .paginate(page=page, per_page=15))
    rows = []
    for deck in pagination.items:
        main_count, extra_count, side_count = deck.counts()
        rows.append({'deck': deck, 'main_count': main_count,
                     'extra_count': extra_count, 'side_count': side_count})
    return render_template('decks/list.html', rows=rows, pagination=pagination)


# ── 新建卡组 ──────────────────────────────────────

@decks_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if request.method == 'GET':
        return render_template('decks/edit.html', deck=None,
                               initial={'main': [], 'extra': [], 'side': []})

    # POST：限速（超限 flash 提示跳回新建页）
    if _rate_limited():
        flash('操作太频繁，请稍后再试', 'error')
        return redirect(url_for('decks.new'))

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    raw_main = request.form.get('main_json', '')
    raw_extra = request.form.get('extra_json', '')
    raw_side = request.form.get('side_json', '')

    t, d, main, extra, side, err = _validate_form()
    if err:
        flash(err, 'error')
        # 回显用户已填内容（JSON 原样回传，解析失败时给空分区）；
        # 红线词拒绝不回显标题/简介——防止借回显把命中词带进响应探测词库
        def _echo(raw):
            try:
                data = json.loads(raw or '[]')
            except (ValueError, TypeError):
                return []
            return data if isinstance(data, _list_type) else []
        echo_title, echo_desc = ('', '') if err == _REDLINE_MSG else (title, description)
        return render_template(
            'decks/edit.html',
            **_edit_context(None, echo_title, echo_desc,
                            _echo(raw_main), _echo(raw_extra), _echo(raw_side))), \
            503 if err == _BANLIST_UNAVAILABLE_MSG else 200

    deck = Deck(user_id=current_user.id, title=t, description=d,
                main_cards=json.dumps(main, ensure_ascii=False),
                extra_cards=json.dumps(extra, ensure_ascii=False),
                side_cards=json.dumps(side, ensure_ascii=False))
    db.session.add(deck)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()   # 踩坑 #23：先查后插并发兜底
        flash('保存失败，请稍后再试', 'error')
        return render_template(
            'decks/edit.html',
            **_edit_context(None, title, description, main, extra, side))
    flash('卡组保存成功！', 'success')
    return redirect(url_for('decks.detail', deck_id=deck.id))


# ── 卡组详情 ──────────────────────────────────────

@decks_bp.route('/<int:deck_id>')
def detail(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    zones = [('主卡组', deck.main()),
             ('额外卡组', deck.extra()),
             ('副卡组', deck.side())]
    can_edit = (current_user.is_authenticated and
                current_user.id == deck.user_id)
    can_delete = (current_user.is_authenticated and
                  (current_user.id == deck.user_id or current_user.is_admin))

    # OG 分享标签：摘要=简介（无简介用三区计数兜底），图沿用全站默认的璃拉甜心，
    # 与论坛帖子同套模式，QQ/微信贴链接时解析成卡片
    og_desc = ' '.join((deck.description or '').split())
    if not og_desc:
        og_desc = (f"主{len(zones[0][1])} · 额{len(zones[1][1])} · "
                   f"副{len(zones[2][1])} 的构筑，来抄作业☆")
    if len(og_desc) > 40:
        og_desc = og_desc[:40] + '…'

    return render_template('decks/detail.html', deck=deck, zones=zones,
                           can_edit=can_edit, can_delete=can_delete,
                           og_desc=og_desc)


# ── 编辑卡组 ──────────────────────────────────────

@decks_bp.route('/<int:deck_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != current_user.id:
        abort(403)   # 仅作者可编辑，统一走全站错误页

    if request.method == 'GET':
        return render_template(
            'decks/edit.html', deck=deck,
            initial={'main': deck.main(), 'extra': deck.extra(),
                     'side': deck.side()})

    # POST：限速（超限 flash 提示跳回编辑页）
    if _rate_limited():
        flash('操作太频繁，请稍后再试', 'error')
        return redirect(url_for('decks.edit', deck_id=deck.id))

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    raw_main = request.form.get('main_json', '')
    raw_extra = request.form.get('extra_json', '')
    raw_side = request.form.get('side_json', '')

    t, d, main, extra, side, err = _validate_form()
    if err:
        flash(err, 'error')
        def _echo(raw):
            try:
                data = json.loads(raw or '[]')
            except (ValueError, TypeError):
                return []
            return data if isinstance(data, _list_type) else []
        # 红线词拒绝不回显标题/简介（同 new 的注释）；其他失败正常回显
        echo_title, echo_desc = ('', '') if err == _REDLINE_MSG else (title, description)
        return render_template(
            'decks/edit.html',
            **_edit_context(deck, echo_title, echo_desc,
                            _echo(raw_main), _echo(raw_extra), _echo(raw_side))), \
            503 if err == _BANLIST_UNAVAILABLE_MSG else 200

    deck.title = t
    deck.description = d
    deck.main_cards = json.dumps(main, ensure_ascii=False)
    deck.extra_cards = json.dumps(extra, ensure_ascii=False)
    deck.side_cards = json.dumps(side, ensure_ascii=False)
    try:
        db.session.commit()   # updated_at 由列的 onupdate 自动刷新
    except IntegrityError:
        db.session.rollback()
        flash('保存失败，请稍后再试', 'error')
        return render_template(
            'decks/edit.html',
            **_edit_context(deck, title, description, main, extra, side))
    flash('卡组已更新！', 'success')
    return redirect(url_for('decks.detail', deck_id=deck.id))


# ── 删除卡组 ──────────────────────────────────────

@decks_bp.route('/<int:deck_id>/delete', methods=['POST'])
@login_required
def delete(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != current_user.id and not current_user.is_admin:
        abort(403)   # 作者或管理员可删，统一走全站错误页
    db.session.delete(deck)
    db.session.commit()
    flash('卡组已删除', 'success')
    return redirect(url_for('decks.list'))
