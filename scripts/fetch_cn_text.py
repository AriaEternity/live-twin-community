# -*- coding: utf-8 -*-
"""从经过校验的百鸽全量包提取首页主题卡资料，不接触业务数据库。"""
import argparse
from datetime import datetime
from pathlib import Path
import zipfile

from refresh_card_data import ROOT, JST, SOURCES, chinese_catalog, source, theme_ids, write_json


def extract_theme(catalog, identifiers):
    """保留原始记录，且只在所有主题卡齐全时交付结果。"""
    result = {}
    for identifier in identifiers:
        record = catalog.get(identifier)
        if not isinstance(record, dict) or type(record.get('id')) is not int or record['id'] != identifier:
            raise ValueError('主题卡资料缺失或密码不符：' + str(identifier))
        result[str(identifier)] = record
    return result


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--cache-dir', type=Path, help='保存公开源的目录')
    cli.add_argument('--use-cache', action='store_true', help='仅使用缓存，不发起网络请求')
    cli.add_argument('--output', type=Path, default=ROOT / 'research' / 'cards_cn_text.json')
    args = cli.parse_args(argv)
    stamp = datetime.now(JST).date().strftime('%Y%m%d')
    cache = args.cache_dir or ROOT / 'research' / ('maintenance-' + stamp) / 'sources'
    identifiers = theme_ids((ROOT / 'js' / 'data.js').read_text(encoding='utf-8'))
    package = source(cache, 'cards.zip', SOURCES['cards.zip'], args.use_cache)
    digest = source(cache, 'cards.md5', SOURCES['cards.md5'], args.use_cache)
    _, catalog, _ = chinese_catalog(package, digest)
    result = extract_theme(catalog, identifiers)
    write_json(args.output, result)
    print('已校验并提取主题卡：' + str(len(result)) + ' 张；输出：' + str(args.output))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit('提取未完成，保留原主题资料：' + str(error))
