# -*- coding: utf-8 -*-
"""公开源更新的失败保护与中文/图号语义回归，不联网、不读业务库。"""
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from refresh_card_data import artwork_aliases, build_banlist, build_cards, chinese_catalog


class RefreshTests(unittest.TestCase):
    def row(self, identifier=10, **changes):
        return {'id': identifier, 'name': 'Example', 'type': 'Spell Card', **changes}

    def test_chinese_priority_and_previous_fallback(self):
        raw = {'data': [self.row(10), self.row(20), self.row(30)]}
        cn = {10: {'sc_name': '官方示例', 'cn_name': '社区示例'}, 20: {'cn_name': '社区二'}}
        old = {'cards': [{'id': 10, 'cn': '旧一'}, {'id': 20, 'cn': '旧二'}, {'id': 30, 'cn': '保留名'}]}
        result = build_cards(raw, cn, old, '2026-10-27')
        self.assertEqual([card['cn'] for card in result['cards']], ['官方示例', '社区二', '保留名'])
        self.assertEqual(result['meta']['cn_count'], 3)

    def test_artwork_canonical_uses_original_password_chinese(self):
        raw = {'data': [self.row(11, card_images=[{'id': 11}, {'id': 10}])]}
        self.assertEqual(artwork_aliases(raw['data']), {'10': 11})
        result = build_cards(raw, {10: {'sc_name': '原始密码名', 'jp_name': '原名'}}, {}, '2026-10-27')
        self.assertEqual(result['cards'][0]['cn'], '原始密码名')
        self.assertEqual(result['cards'][0]['jp'], '原名')
        self.assertEqual(result['cards'][0]['atk'], -1)

    def test_duplicate_or_partial_catalog_is_rejected(self):
        for raw in [{'data': [self.row(), self.row()]},
                    {'data': [self.row()], 'meta': {'rows_remaining': 1}},
                    {'data': []}, {'data': [self.row(True)]}]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                build_cards(raw, {}, {}, '2026-10-27')

    def test_unexpected_shrink_is_rejected(self):
        with self.assertRaises(ValueError):
            build_cards({'data': [self.row()]}, {}, {'cards': [self.row(i) for i in range(20)]}, '2026-10-27')

    def test_digest_checks_uncompressed_json(self):
        raw = json.dumps({'42': {'id': 10}, '43': {'id': 0}}).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('cards.json', raw)
        package = stream.getvalue()
        _, by_id, _ = chinese_catalog(package, ('"' + hashlib.md5(raw).hexdigest() + '"').encode())
        self.assertEqual(set(by_id), {10})
        with self.assertRaises(ValueError):
            chinese_catalog(package, hashlib.md5(package).hexdigest().encode())

    def fixture(self):
        limits = {'ja': {'date': '2026-10-01', 'forbidden': {'1': '禁止例'},
                         'limited': {'2': '制限例'}, 'semi_limited': {'3': '準制限例'}}}
        records = {'1': {'id': 10, 'jp_name': '禁止例'}, '2': {'id': 20, 'jp_name': '制限例'},
                   '3': {'id': 30, 'jp_name': '準制限例'}}
        html = '<h1>2026年10月1日適用</h1>'
        for section, title, name in [('forbidden', '禁止カード', '禁止例'), ('semilimited', '制限カード', '制限例'), ('limited', '準制限カード', '準制限例')]:
            html += f'<section id="{section}"><h2>{title}（1枚）</h2><table><tr class="new"><td>{name}</td><td>EXAMPLE</td><td>追加</td></tr></table></section>'
        return limits, records, html

    def test_official_sections_and_new_rows_are_verified(self):
        result = build_banlist(*self.fixture(), '2026-10-27')
        self.assertEqual(result['cards'], {'10': 0, '20': 1, '30': 2})
        self.assertTrue(result['meta']['verified_against_official'])

    def test_mismatched_or_future_rules_are_rejected(self):
        limits, records, html = self.fixture()
        with self.assertRaises(ValueError):
            build_banlist(limits, records, html.replace('禁止例', '错误例'), '2026-10-27')
        with self.assertRaises(ValueError):
            build_banlist(limits, records, html, '2026-09-30')


if __name__ == '__main__':
    unittest.main()
