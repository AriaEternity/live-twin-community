# -*- coding: utf-8 -*-
"""完整校验主题资料后，原子更新首页卡名与效果，保留标签及其他展示配置。"""
import argparse
import json
from pathlib import Path
import re

from refresh_card_data import ROOT, atomic_write, theme_ids


CARD_ARRAY = re.compile(r'const CARDS\s*=\s*\[([\s\S]*?)\n\];')
CARD_ID = re.compile(r'^\s*id:\s*(\d+)\s*,', re.M)
IDENTIFIER = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')


def string_end(text, start):
    """扫描现有JS字符串，兼容单引号、双引号与无插值模板字符串。"""
    quote, index = text[start], start + 1
    while index < len(text):
        if text[index] == '\\':
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    raise ValueError('主题卡存在未闭合的JS字符串')


def property_string(text, name):
    """只识别字符串与注释之外的字段，避免效果正文中的同名文字误匹配。"""
    found, index = [], 0
    while index < len(text):
        if text.startswith('//', index):
            end = text.find('\n', index + 2)
            index = len(text) if end == -1 else end + 1
            continue
        if text.startswith('/*', index):
            end = text.find('*/', index + 2)
            if end == -1:
                raise ValueError('主题卡存在未闭合的JS注释')
            index = end + 2
            continue
        if text[index] in "'\"`":
            index = string_end(text, index)
            continue
        token = IDENTIFIER.match(text, index)
        if token:
            after = token.end()
            while after < len(text) and text[after].isspace():
                after += 1
            if token[0] == name and after < len(text) and text[after] == ':':
                after += 1
                while after < len(text) and text[after].isspace():
                    after += 1
                if after >= len(text) or text[after] not in "'\"`":
                    raise ValueError('主题卡字段必须是静态字符串：' + name)
                end = string_end(text, after)
                found.append((after, end))
                index = end
                continue
            index = token.end()
        else:
            index += 1
    if len(found) != 1:
        raise ValueError('主题卡字段缺失或不唯一：' + name)
    return found[0]


def nonempty(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('主题卡必要字段为空：' + key)
    return value.strip()


def fields(record):
    chinese = record.get('sc_name')
    chinese = chinese.strip() if isinstance(chinese, str) else ''
    if not chinese:
        chinese = nonempty(record, 'cn_name')
    text = record.get('text')
    if not isinstance(text, dict):
        raise ValueError('主题卡效果资料结构异常')
    parts = []
    for key in ('types', 'pdesc', 'desc'):
        value = text.get(key, '')
        if not isinstance(value, str) or (key in ('types', 'desc') and not value.strip()):
            raise ValueError('主题卡效果必要字段缺失：' + key)
        value = value.replace('\r\n', '\n').replace('\r', '\n').strip()
        if value:
            parts.append(value)
    return {'cn': chinese, 'en': nonempty(record, 'en_name'), 'desc': '\n'.join(parts)}


def js_string(value):
    # JSON字符串也是合法JS字符串；正文中的反引号、${...}和引号不能变成代码。
    return json.dumps(value, ensure_ascii=False).replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def update_text(js, records):
    if not isinstance(records, dict):
        raise ValueError('主题资料必须按卡片密码索引')
    identifiers = theme_ids(js)
    match = CARD_ARRAY.search(js)
    body = match[1]
    starts = list(CARD_ID.finditer(body))
    edits = []
    for offset, marker in enumerate(starts):
        identifier = int(marker[1])
        record = records.get(str(identifier))
        if not isinstance(record, dict) or type(record.get('id')) is not int or record['id'] != identifier:
            raise ValueError('主题卡资料缺失或密码不符：' + str(identifier))
        values = fields(record)
        stop = starts[offset + 1].start() if offset + 1 < len(starts) else len(body)
        chunk = body[marker.start():stop]
        for key, value in values.items():
            begin, end = property_string(chunk, key)
            origin = match.start(1) + marker.start()
            edits.append((origin + begin, origin + end, js_string(value)))
    if len(starts) != len(identifiers):
        raise ValueError('主题卡定位结果与密码列表不一致')
    for start, end, replacement in sorted(edits, reverse=True):
        js = js[:start] + replacement + js[end:]
    return js


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--input', type=Path, default=ROOT / 'research' / 'cards_cn_text.json')
    cli.add_argument('--target', type=Path, default=ROOT / 'js' / 'data.js')
    args = cli.parse_args(argv)
    original = args.target.read_text(encoding='utf-8')
    records = json.loads(args.input.read_text(encoding='utf-8'))
    updated = update_text(original, records)
    # 全部校验先完成；任一卡片不齐全时不会覆盖原文件。
    if updated != original:
        atomic_write(args.target, updated.encode('utf-8'))
    print('主题卡资料已更新：' + str(len(theme_ids(updated))) + ' 张；' + ('内容未变化' if updated == original else str(args.target)))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('更新未完成，保留原首页资料：' + str(error))
