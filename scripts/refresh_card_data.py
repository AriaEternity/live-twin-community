# -*- coding: utf-8 -*-
"""校验公开来源后更新卡库与OCG禁限表；不接触业务数据库。"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
SOURCES = {'ygoprodeck.json': 'https://db.ygoprodeck.com/api/v7/cardinfo.php',
           'cards.zip': 'https://ygocdb.com/api/v0/cards.zip',
           'cards.md5': 'https://ygocdb.com/api/v0/cards.zip.md5',
           'limits.json': 'https://ygocdb.com/api/v0/limits.json'}


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))


def source(cache, filename, url, use_cache=False):
    target = Path(cache) / filename
    if use_cache:
        return target.read_bytes()
    for attempt in range(3):
        try:
            req = Request(url, headers={'User-Agent': 'LiveTwin-ContentMaintenance/2.0'})
            with urlopen(req, timeout=60) as response:
                raw = response.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024:
                raise ValueError('公开源超过64MiB大小预算')
            atomic_write(target, raw)
            return raw
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def chinese_catalog(package, digest):
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        entries = [item for item in archive.infolist() if item.filename == 'cards.json']
        if len(entries) != 1 or entries[0].file_size > 64 * 1024 * 1024:
            raise ValueError('中文全量包结构或大小异常')
        raw = archive.read(entries[0])
    # 维护方提供的MD5针对解压后的JSON，响应值可能带JSON双引号。
    expected = digest.decode('ascii').strip().strip('"').lower()
    if not re.fullmatch(r'[0-9a-f]{32}', expected) or hashlib.md5(raw).hexdigest() != expected:
        raise ValueError('百鸽cards.json校验失败')
    records = json.loads(raw)
    if not isinstance(records, dict):
        raise ValueError('中文资料必须按官方CID索引')
    by_id = {}
    for card in records.values():
        identifier = card.get('id')
        if type(identifier) is not int or identifier <= 0:
            continue  # 无密码奖品卡不进入密码索引。
        if identifier in by_id:
            raise ValueError('中文资料存在重复正密码')
        by_id[identifier] = card
    return records, by_id, hashlib.sha256(raw).hexdigest()


def integer(value, default):
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return default


def artwork_aliases(rows):
    parents = {}
    for card in rows:
        for image in card.get('card_images', []):
            identifier = image.get('id')
            if type(identifier) is not int or identifier <= 0:
                continue
            previous = parents.setdefault(identifier, card['id'])
            if previous != card['id']:
                raise ValueError('异画密码关联多个主卡：' + str(identifier))
    aliases = {str(key): value for key, value in parents.items() if key != value}
    if any(str(value) in aliases for value in aliases.values()):
        raise ValueError('异画归一出现链式映射')
    return aliases


def build_cards(payload, chinese, previous, fetched):
    rows = payload.get('data')
    old = {card['id']: card for card in previous.get('cards', [])}
    if not isinstance(rows, list) or not rows or payload.get('meta', {}).get('rows_remaining', 0):
        raise ValueError('未取得全量卡库，拒绝将分页结果落盘')
    if old and len(rows) < len(old) * 0.9:
        raise ValueError('卡库缩减超过10%，需要人工核对')
    result, identifiers = [], set()
    origins = {'sc_name': 0, 'cn_name': 0, 'previous': 0}
    for row in rows:
        identifier = row.get('id')
        if type(identifier) is not int or not 0 < identifier <= 9007199254740991 or identifier in identifiers:
            raise ValueError('属性库卡片密码无效或重复')
        if not row.get('name') or not row.get('type'):
            raise ValueError('属性库卡片必要字段缺失')
        identifiers.add(identifier)
        cn = chinese.get(identifier) or next((chinese[image['id']] for image in row.get('card_images', []) if image.get('id') in chinese), {})
        before = old.get(identifier, {})
        card = {'id': identifier, 'en': row['name'], 'jp': cn.get('jp_name') or before.get('jp', ''),
                'ty': row['type'], 'ra': row.get('race') or '', 'at': row.get('attribute') or '',
                'lv': integer(row.get('level'), 0), 'lnk': integer(row.get('linkval'), 0),
                'sc': integer(row.get('scale'), -1), 'atk': integer(row.get('atk'), -1), 'def': integer(row.get('def'), -1)}
        for key, value in [('sc_name', cn.get('sc_name')), ('cn_name', cn.get('cn_name')), ('previous', before.get('cn'))]:
            if isinstance(value, str) and value.strip():
                card['cn'] = value.strip()
                origins[key] += 1
                break
        result.append(card)
    return {'meta': {'source': 'YGOPRODeck全量属性 + 百鸽中文/日文资料', 'source_url': SOURCES['ygoprodeck.json'],
                     'fetched': fetched, 'count': len(result), 'cn_source': 'sc_name优先，cn_name与既有译名兜底',
                     'cn_source_url': 'https://ygocdb.com/api', 'cn_count': sum(origins.values()), 'cn_source_counts': origins,
                     'scope': 'API收录记录，含先行/临时密码；不等于已发售OCG数量'}, 'cards': result}


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


class OfficialList(HTMLParser):
    # 官方页面的历史section英文名不对应直觉含义，另核验正文标题。
    SECTIONS = {'forbidden': (0, '禁止カード'), 'semilimited': (1, '制限カード'), 'limited': (2, '準制限カード')}

    def __init__(self):
        super().__init__()
        self.sections, self.cells, self.cell, self.heading = [], [], None, None
        self.rows, self.headings = {0: [], 1: [], 2: []}, {}

    def handle_starttag(self, tag, attrs):
        if tag == 'section':
            self.sections.append(dict(attrs).get('id'))
        if self.sections and self.sections[-1] in self.SECTIONS:
            if tag == 'tr':
                self.cells = []
            elif tag == 'td':
                self.cell = ''
            elif tag == 'h2':
                self.heading = ''

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data
        if self.heading is not None:
            self.heading += data

    def handle_endtag(self, tag):
        if self.sections and self.sections[-1] in self.SECTIONS:
            level, title = self.SECTIONS[self.sections[-1]]
            if tag == 'td' and self.cell is not None:
                self.cells.append(self.cell.strip())
                self.cell = None
            elif tag == 'tr' and len(self.cells) >= 2:
                self.rows[level].append(self.cells[:2])
                self.cells = []
            elif tag == 'h2' and self.heading is not None:
                if not self.heading.strip().startswith(title):
                    raise ValueError('官方分档标题与解析契约不符')
                self.headings[level], self.heading = self.heading, None
        if tag == 'section' and self.sections:
            self.sections.pop()


def build_banlist(limits, by_cid, html, fetched):
    effective = limits.get('ja', {}).get('date', '')
    if date.fromisoformat(effective) > date.fromisoformat(fetched):
        raise ValueError('禁限表尚未生效')
    if f'{effective[:4]}年{int(effective[5:7])}月{int(effective[8:10])}日適用' not in html:
        raise ValueError('官方正文与机器源生效日期不一致')
    parser = OfficialList()
    parser.feed(html)
    result, counts = {}, {}
    for key, level in [('forbidden', 0), ('limited', 1), ('semi_limited', 2)]:
        group = limits['ja'].get(key)
        if not isinstance(group, dict) or not group:
            raise ValueError('机器源分档为空或结构异常')
        names = set()
        for cid, jp_name in group.items():
            card = by_cid.get(str(cid), {})
            identifier = card.get('id')
            if type(identifier) is not int or identifier <= 0 or str(identifier) in result:
                raise ValueError('禁限CID无法唯一映射到密码：' + str(cid))
            if normalized(card.get('jp_name', '')) != normalized(jp_name):
                raise ValueError('禁限源与卡库日文名不符：' + str(cid))
            names.add(normalized(jp_name))
            result[str(identifier)] = level
        expected = {normalized(row[0]) for row in parser.rows[level]}
        heading_count = re.search(r'[（(](\d+)枚[）)]', parser.headings.get(level, ''))
        if names != expected or len(group) != len(parser.rows[level]) or not heading_count or int(heading_count[1]) != len(group):
            raise ValueError('官方完整名单或数量核验不通过：' + key)
        counts[key] = len(group)
    url = 'https://www.yugioh-card.com/japan/event/limitregulation/index.php?list=' + effective[:7].replace('-', '')
    return {'meta': {'source': '百鸽OCG机器源，经Konami完整名单逐张核验', 'source_url': url,
                     'machine_source_url': SOURCES['limits.json'], 'fetched': fetched, 'effective_date': effective,
                     'count': len(result), 'counts': counts, 'verified_against_official': True},
            'cards': dict(sorted(result.items(), key=lambda item: int(item[0])))}


def theme_ids(js):
    match = re.search(r'const CARDS\s*=\s*\[([\s\S]*?)\n\];', js)
    if not match:
        raise ValueError('找不到首页CARDS数组')
    ids = [int(value) for value in re.findall(r'^\s*id:\s*(\d+)\s*,', match[1], re.M)]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('首页卡片密码为空或重复')
    return ids


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--as-of', default=datetime.now(JST).date().isoformat())
    cli.add_argument('--cache-dir', type=Path)
    cli.add_argument('--use-cache', action='store_true', help='仅使用保存的公开源离线复验')
    cli.add_argument('--output-dir', type=Path, default=ROOT / 'data')
    cli.add_argument('--cards-only', action='store_true')
    cli.add_argument('--banlist-only', action='store_true')
    args = cli.parse_args(argv)
    date.fromisoformat(args.as_of)
    if args.cards_only and args.banlist_only:
        cli.error('不能同时指定两个单项更新模式')
    cache = args.cache_dir or ROOT / 'research' / ('maintenance-' + args.as_of.replace('-', '')) / 'sources'
    raw = {}
    for filename, url in SOURCES.items():
        raw[filename] = source(cache, filename, url, args.use_cache)
        print('已读取公开源：' + filename, flush=True)
    by_cid, by_id, chinese_sha = chinese_catalog(raw['cards.zip'], raw['cards.md5'])
    limits = json.loads(raw['limits.json'])
    effective = limits['ja']['date']
    url = 'https://www.yugioh-card.com/japan/event/limitregulation/index.php?list=' + effective[:7].replace('-', '')
    official = source(cache, 'konami-' + effective[:7].replace('-', '') + '.html', url, args.use_cache)
    banlist = build_banlist(limits, by_cid, official.decode('utf-8'), args.as_of)
    attributes = json.loads(raw['ygoprodeck.json'])
    aliases = artwork_aliases(attributes['data'])
    canonical = {}
    for identifier, limit in banlist['cards'].items():
        main_id = str(aliases.get(identifier, int(identifier)))
        if main_id in canonical and canonical[main_id] != limit:
            raise ValueError('同卡不同图号的官方禁限冲突')
        canonical[main_id] = limit
    banlist['official_cards'] = banlist['cards']
    banlist['cards'] = dict(sorted(canonical.items(), key=lambda item: int(item[0])))
    banlist['aliases'] = aliases
    banlist['meta']['alias_count'] = len(aliases)
    previous = json.loads((ROOT / 'data' / 'cards_db.json').read_text(encoding='utf-8'))
    cards = None if args.banlist_only else build_cards(attributes, by_id, previous, args.as_of)
    evidence = {'checked_at': datetime.now(JST).isoformat(timespec='seconds'), 'as_of': args.as_of,
                'sources': {name: {'url': SOURCES[name], 'sha256': hashlib.sha256(value).hexdigest()} for name, value in raw.items()},
                'official': {'url': url, 'sha256': hashlib.sha256(official).hexdigest()}, 'cards_json_sha256': chinese_sha,
                'banlist': banlist['meta']}
    if cards:
        new_ids = {card['id'] for card in cards['cards']}
        old_ids = {card['id'] for card in previous['cards']}
        evidence.update(cards=cards['meta'], added_ids=sorted(new_ids-old_ids), retired_ids=sorted(old_ids-new_ids))
        missing = set(map(int, banlist['cards'])) - new_ids
        if missing:
            raise ValueError('属性库未覆盖所有禁限密码：' + str(sorted(missing)))
    texts = []
    attribute_ids = {row['id'] for row in attributes['data']}
    for identifier, record in by_id.items():
        detail = record.get('text')
        if aliases.get(str(identifier), identifier) not in attribute_ids or not isinstance(detail, dict) or not detail.get('desc'):
            continue
        texts.append({key: record.get(key, {} if key in ('text', 'data') else '')
                      for key in ('id', 'cn_name', 'sc_name', 'jp_name', 'en_name', 'text', 'data')})
    if not texts:
        raise ValueError('没有取得可核验的本地效果文本')
    text_snapshot = {'meta': {'fetched': args.as_of, 'count': len(texts), 'source_url': 'https://ygocdb.com/api',
                              'scope': '百鸽公开原文快照；缺失的效果不生成、不推测'},
                     'cards': sorted(texts, key=lambda card: card['id'])}
    evidence['local_texts'] = text_snapshot['meta']
    selected = theme_ids((ROOT / 'js' / 'data.js').read_text(encoding='utf-8'))
    if any(identifier not in by_id for identifier in selected):
        raise ValueError('主题卡缺少中文资料，保留原数据')
    # 所有来源与契约先核验，再逐文件原子替换；没有全量抓取的分页歧义。
    if cards:
        write_json(args.output_dir / 'cards_db.json', cards)
        write_json(args.output_dir / 'card_texts.json', text_snapshot)
    if not args.cards_only:
        write_json(args.output_dir / 'banlist_ocg.json', banlist)
    write_json(cache.parent / 'theme-cards.json', {str(identifier): by_id[identifier] for identifier in selected})
    write_json(cache.parent / 'source-verification.json', evidence)
    print(json.dumps({'cards': cards['meta'] if cards else None, 'banlist': banlist['meta'],
                      'output': str(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit('更新未完成，请核对原因：' + str(error))
