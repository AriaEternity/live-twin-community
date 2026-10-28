# -*- coding: utf-8 -*-
"""主题资料刷新离线回归：不读取业务库，不发起网络请求。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from fetch_cn_text import extract_theme
from update_cn_desc import main as update_file, property_string, update_text


def sample(quote="'"):
    return ('const CARDS = [\n  {\n    id: 123, cn: ' + quote + '旧名' + quote
            + ', en: ' + quote + 'Old name' + quote + ',\n    desc: ' + quote + '旧正文' + quote
            + ', tag: "保留标签",\n  },\n];\n')


def record():
    return {'id': 123, 'sc_name': '新名"与反引号`', 'cn_name': '社区名',
            'en_name': 'New ${notCode} \\ card', 'text': {'types': '[怪兽|效果]',
            'pdesc': '灵摆：测试\r\n第二行', 'desc': '正文含cn: \'误匹配\'\r\n末行`与${不执行}'}}


class ThemeRefreshTests(unittest.TestCase):
    def test_quotes_escape_and_idempotence(self):
        for quote in ("'", '"', '`'):
            with self.subTest(quote=quote):
                refreshed = update_text(sample(quote), {'123': record()})
                self.assertIn('tag: "保留标签"', refreshed)
                for key, expected in [('cn', record()['sc_name']), ('en', record()['en_name']),
                                      ('desc', '[怪兽|效果]\n灵摆：测试\n第二行\n正文含cn: \'误匹配\'\n末行`与${不执行}')]:
                    begin, end = property_string(refreshed, key)
                    self.assertEqual(json.loads(refreshed[begin:end]), expected)
                self.assertEqual(update_text(refreshed, {'123': record()}), refreshed)

    def test_missing_or_mismatched_record_never_overwrites(self):
        incomplete = record()
        incomplete['text'].pop('desc')
        wrong_id = record()
        wrong_id['id'] = 456
        for records in ({}, {'123': incomplete}, {'123': wrong_id}):
            with self.subTest(records=records), tempfile.TemporaryDirectory() as directory:
                target, source = Path(directory) / 'data.js', Path(directory) / 'theme.json'
                target.write_bytes(sample().encode('utf-8'))
                source.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
                with self.assertRaises(ValueError):
                    update_file(['--input', str(source), '--target', str(target)])
                self.assertEqual(target.read_bytes(), sample().encode('utf-8'))

    def test_duplicate_target_field_is_rejected(self):
        with self.assertRaises(ValueError):
            update_text(sample().replace('tag:', 'cn: "重复字段", tag:'), {'123': record()})

    def test_extraction_preserves_raw_record_and_requires_every_card(self):
        original = record()
        original['extra_public_field'] = {'source': '合成来源'}
        self.assertEqual(extract_theme({123: original}, [123]), {'123': original})
        with self.assertRaises(ValueError):
            extract_theme({123: original}, [123, 456])
        mismatched = copy.deepcopy(original)
        mismatched['id'] = 456
        with self.assertRaises(ValueError):
            extract_theme({123: mismatched}, [123])


if __name__ == '__main__':
    unittest.main()
