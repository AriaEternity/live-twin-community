"""内容更新的离线回归：内存数据库、临时上传目录，不导入 app.py。

运行：python -B -X utf8 -m unittest discover -s tests -p test_content_regressions.py -v
"""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ContentRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ygo-content-regressions-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.upload_dir = Path(cls.temp.name) / 'uploads'
        cls.upload_dir.mkdir()
        cls.env_patch = mock.patch.dict(os.environ, {
            'SECRET_KEY': 'synthetic-offline-test-key',
            'FLASK_DEBUG': '1',
            'DATABASE_URL': 'sqlite:///:memory:',
            'UPLOAD_FOLDER': str(cls.upload_dir),
        })
        cls.env_patch.start()
        cls.addClassCleanup(cls.env_patch.stop)

        # 仅导入数据模型与蓝图；应用入口会自动建目录和种子数据，故不使用它。
        from flask import Flask
        from config import Config
        from models import CardQueryCache, db
        cls.cards_mod = importlib.import_module('routes.cards')
        cls.decks_mod = importlib.import_module('routes.decks')
        main_mod = importlib.import_module('routes.main')
        if (Config.SQLALCHEMY_DATABASE_URI != 'sqlite:///:memory:'
                or Path(Config.UPLOAD_FOLDER) != cls.upload_dir):
            raise RuntimeError('本回归需在独立 Python 进程运行，禁止复用已有业务配置')
        cls.app = Flask(__name__, root_path=cls.temp.name,
                        instance_path=str(Path(cls.temp.name) / 'instance'))
        cls.app.config.from_object(Config)
        cls.app.config['TESTING'] = True
        db.init_app(cls.app)
        cls.app.register_blueprint(cls.cards_mod.cards_bp)
        cls.app.register_blueprint(cls.decks_mod.decks_bp)
        cls.app.register_blueprint(main_mod.main_bp)
        cls.db = db
        cls.cache_model = CardQueryCache

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.db.create_all()
        self.client = self.app.test_client()
        self.rule_path = Path(self.temp.name) / 'synthetic-banlist.json'
        self.rule_revision = 0
        path_patch = mock.patch.object(self.decks_mod, '_BANLIST_PATH', str(self.rule_path))
        path_patch.start()
        self.addCleanup(path_patch.stop)
        # 每项测试独占状态，不能让上一项的最后有效规则掩盖首次加载失败。
        cache_patch = mock.patch.object(self.decks_mod, '_banlist_cache', {})
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.text_path = Path(self.temp.name) / 'synthetic-card-texts.json'
        text_path_patch = mock.patch.object(self.cards_mod, '_CARD_TEXTS_PATH', str(self.text_path))
        text_path_patch.start()
        self.addCleanup(text_path_patch.stop)
        text_cache_patch = mock.patch.object(self.cards_mod, '_card_text_cache', {})
        text_cache_patch.start()
        self.addCleanup(text_cache_patch.stop)
        if self.text_path.exists():
            self.text_path.unlink()
        self._write_rules({'cards': {'123': 1},
                           'meta': {'effective_date': '2026-10-01',
                                    'fetched': '2026-10-27', 'count': 1}})

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.context.pop()

    def _write_rules(self, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.rule_path.write_text(text, encoding='utf-8')
        self.rule_revision += 1
        timestamp = 1700000000000000000 + self.rule_revision * 1000000000
        os.utime(self.rule_path, ns=(timestamp, timestamp))

    def _cache_card(self, *, age_days=0, ban=3, card_id=123):
        raw = json.dumps([{'id': card_id, 'ban': ban, 'data': {'type': 33}}])
        row = self.cache_model(
            keyword='synthetic-card', result_json=raw,
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days))
        self.db.session.add(row)
        self.db.session.commit()
        return raw

    def _text_card(self, card_id=68059897):
        return {'id': card_id, 'cn_name': '合成中文卡名', 'sc_name': '合成简中卡名',
                'jp_name': '合成日本語カード', 'en_name': 'Synthetic Source Card',
                'text': {'types': '魔法卡 通常', 'pdesc': '合成灵摆文本',
                         'desc': '合成原始效果：保留标点。\n保留换行。'},
                'data': {'type': 2, 'race': 0, 'attribute': 0, 'level': 0}}

    def _write_texts(self, cards, fetched='2026-10-27'):
        self.text_path.write_text(json.dumps({
            'meta': {'fetched': fetched, 'count': len(cards)}, 'cards': cards
        }, ensure_ascii=False), encoding='utf-8')
        self.rule_revision += 1
        timestamp = 1700000000000000000 + self.rule_revision * 1000000000
        os.utime(self.text_path, ns=(timestamp, timestamp))

    def test_local_text_fallback_preserves_source_effects_and_declares_snapshot(self):
        source = self._text_card()
        self._write_texts([source])
        queries = ('68059897', '合成中文', '合成简中', '日本語', 'synthetic source')
        with mock.patch.object(self.cards_mod, '_fetch_remote', side_effect=OSError('合成上游异常')):
            for query in queries:
                with self.subTest(query=query):
                    response = self.client.get('/api/cards/search', query_string={'q': query})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json['local_snapshot'])
                    self.assertFalse(response.json['cached'])
                    self.assertEqual(response.json['snapshot_date'], '2026-10-27')
                    self.assertIn('本地文本快照', response.json['notice'])
                    self.assertIn('2026-10-27', response.json['notice'])
                    self.assertEqual(response.json['cards'][0]['desc'], source['text']['desc'])
                    self.assertEqual(response.json['cards'][0]['pdesc'], source['text']['pdesc'])
        self.assertEqual(self.cache_model.query.count(), 0)

    def test_local_text_passcode_lookup_matches_main_id_and_alternate_image(self):
        self._write_rules({'cards': {'18144507': 1}, 'aliases': {'18144506': 18144507}})
        self._write_texts([self._text_card(18144506)])
        with mock.patch.object(self.cards_mod, '_fetch_remote', side_effect=OSError('合成上游异常')):
            for query in ('18144506', '18144507'):
                with self.subTest(query=query):
                    response = self.client.get('/api/cards/search',
                                               query_string={'q': query, 'ban': 'limited'})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json['local_snapshot'])
                    self.assertEqual(response.json['cards'][0]['id'], 18144506)
                    self.assertEqual(response.json['cards'][0]['ban'], 1)
        self.assertEqual(self.cache_model.query.count(), 0)

    def test_missing_broken_or_unmatched_local_text_keeps_existing_502(self):
        with mock.patch.object(self.cards_mod, '_fetch_remote', side_effect=OSError('合成上游异常')):
            response = self.client.get('/api/cards/search?q=68059897')
            self.assertEqual(response.status_code, 502)
            self._write_texts([self._text_card()])
            response = self.client.get('/api/cards/search?q=没有的合成卡名')
            self.assertEqual(response.status_code, 502)
            for invalid in ('{', '{"cards": {}}', '{"cards": [{"id": true}]}'):
                with self.subTest(invalid=invalid):
                    self.cards_mod._card_text_cache.clear()
                    self.text_path.write_text(invalid, encoding='utf-8')
                    response = self.client.get('/api/cards/search?q=68059897')
                    self.assertEqual(response.status_code, 502)
                    self.assertNotIn('local_snapshot', response.json)

    def test_stale_keyword_cache_still_precedes_local_text_snapshot(self):
        raw = self._cache_card(age_days=8)
        self._write_texts([self._text_card()])
        with mock.patch.object(self.cards_mod, '_fetch_remote', side_effect=OSError('合成上游异常')), \
                mock.patch.object(self.cards_mod, '_search_local_texts') as local:
            response = self.client.get('/api/cards/search?q=synthetic-card')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['cached'])
        self.assertTrue(response.json['stale'])
        self.assertNotIn('local_snapshot', response.json)
        self.assertEqual(response.json['cards'][0]['id'], 123)
        self.assertEqual(self.cache_model.query.one().result_json, raw)
        local.assert_not_called()

    def test_local_text_snapshot_reloads_after_mtime_change(self):
        source = self._text_card()
        self._write_texts([source])
        with mock.patch.object(self.cards_mod, '_fetch_remote', side_effect=OSError('合成上游异常')):
            first = self.client.get('/api/cards/search?q=68059897')
            self.assertEqual(first.json['snapshot_date'], '2026-10-27')
            source['text']['desc'] = '合成更新后的原始效果'
            self._write_texts([source], fetched='2026-10-28')
            second = self.client.get('/api/cards/search?q=68059897')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json['snapshot_date'], '2026-10-28')
        self.assertEqual(second.json['cards'][0]['desc'], '合成更新后的原始效果')
        self.assertEqual(self.cache_model.query.count(), 0)

    def test_local_text_fallback_obeys_max_results(self):
        self._write_texts([self._text_card(900000001 + i) for i in range(35)])
        with mock.patch.object(self.cards_mod, '_fetch_remote', side_effect=OSError('合成上游异常')):
            response = self.client.get('/api/cards/search?q=合成中文')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['cards']), self.cards_mod.MAX_RESULTS)
        self.assertTrue(response.json['local_snapshot'])
        self.assertEqual(self.cache_model.query.count(), 0)

    def test_fresh_cache_uses_current_ban_without_rewriting_raw_cache(self):
        raw = self._cache_card(ban=3)
        with mock.patch.object(self.cards_mod, 'get_banlist', return_value={'123': 0}), \
                mock.patch.object(self.cards_mod, '_fetch_remote') as upstream:
            response = self.client.get('/api/cards/search?q=synthetic-card')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['cards'][0]['ban'], 0)
        self.assertTrue(response.json['cached'])
        upstream.assert_not_called()
        self.assertEqual(self.cache_model.query.one().result_json, raw)

    def test_stale_fallback_uses_current_ban_without_rewriting_raw_cache(self):
        raw = self._cache_card(age_days=8, ban=0)
        with mock.patch.object(self.cards_mod, 'get_banlist', return_value={}), \
                mock.patch.object(self.cards_mod, '_fetch_remote',
                                  side_effect=OSError('合成上游不可用')):
            response = self.client.get('/api/cards/search?q=synthetic-card')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['cards'][0]['ban'], 3)
        self.assertTrue(response.json['stale'])
        self.assertEqual(self.cache_model.query.one().result_json, raw)

    def test_filtered_cache_matches_and_displays_the_same_current_ban(self):
        self._cache_card(ban=3)
        with mock.patch.object(self.cards_mod, 'get_banlist', return_value={'123': 1}), \
                mock.patch.object(self.cards_mod, '_fetch_remote') as upstream:
            response = self.client.get('/api/cards/search?q=synthetic-card&ban=limited')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([card['ban'] for card in response.json['cards']], [1])
        upstream.assert_not_called()

    def test_attribute_filter_matches_official_passcode_and_main_image(self):
        self._write_rules({'cards': {'18144507': 1}, 'aliases': {'18144506': 18144507}})
        card = {'id': 18144507, 'cn': '合成中文卡名', 'en': 'Synthetic card', 'jp': '',
                'ty': 'Spell Card', 'ra': 'Normal', 'at': '', 'lv': 0, 'lnk': 0,
                'sc': -1, 'atk': -1, 'def': -1}
        with mock.patch.object(self.cards_mod, 'get_card_db', return_value=[card]):
            for query in ('18144506', '18144507'):
                with self.subTest(query=query):
                    response = self.client.get('/api/cards/filter',
                                               query_string={'q': query, 'ban': 'limited'})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json['total'], 1)
                    self.assertEqual(response.json['cards'][0]['id'], 18144507)
                    self.assertEqual(response.json['cards'][0]['ban'], 1)

    def test_response_copy_does_not_mutate_source_cards(self):
        cards = [{'id': 123, 'ban': 3, 'sc_name': '合成卡'}]
        with mock.patch.object(self.cards_mod, 'get_banlist', return_value={'123': 2}):
            response_cards = self.cards_mod._with_current_ban_status(cards)
        self.assertEqual(cards[0]['ban'], 3)
        self.assertEqual(response_cards[0]['ban'], 2)
        self.assertIsNot(response_cards[0], cards[0])

    def test_rule_api_exposes_only_safe_dates_and_actual_count(self):
        response = self.client.get('/decks/api/banlist')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['cards'], {'123': 1})
        self.assertEqual(response.json['aliases'], {})
        self.assertEqual(response.json['meta'], {
            'available': True, 'effective_date': '2026-10-01',
            'fetched': '2026-10-27', 'count': 1, 'stale': False})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_broken_or_missing_file_preserves_last_valid_rules_and_aliases(self):
        self._write_rules({'cards': {'123': 0}, 'aliases': {'122': 123}})
        self.assertEqual(self.decks_mod.get_banlist(), {'123': 0})
        self._write_rules('{')
        self.assertEqual(self.decks_mod.get_banlist(), {'123': 0})
        self.assertEqual(self.decks_mod.canonical_card_id(122), 123)
        self.assertTrue(self.decks_mod.get_banlist_status()['stale'])
        self.rule_path.unlink()
        response = self.client.get('/decks/api/banlist')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['meta']['available'])
        self.assertTrue(response.json['meta']['stale'])
        self.assertEqual(response.json['aliases'], {'122': 123})

    def test_invalid_rule_schema_never_becomes_an_empty_available_table(self):
        invalid_payloads = [
            '{', [], {}, {'cards': {}}, {'cards': []},
            {'cards': {'123': None}}, {'cards': {'123': True}},
            {'cards': {'123': 1.0}}, {'cards': {'123': 3}},
            {'cards': {'0': 1}}, {'cards': {'01': 1}},
            {'cards': {'１２３': 1}}, {'cards': {str(2 ** 53): 1}},
            {'cards': {'123': 1}, 'aliases': []},
            {'cards': {'123': 1}, 'aliases': {'122': '123'}},
            {'cards': {'123': 1}, 'aliases': {'122': True}},
            {'cards': {'123': 1}, 'aliases': {'122': 122}},
            {'cards': {'123': 1}, 'aliases': {'121': 122, '122': 123}},
            {'cards': {'123': 1}, 'aliases': {'123': 124}},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.decks_mod._banlist_cache.clear()
                self._write_rules(payload)
                response = self.client.get('/decks/api/banlist')
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.json['meta']['available'])
                self.assertEqual(response.json['cards'], {})
                self.assertEqual(response.json['aliases'], {})

    def test_no_valid_rules_block_card_apis_without_calling_upstream(self):
        self.rule_path.unlink()
        with mock.patch.object(self.cards_mod, '_fetch_remote') as upstream, \
                mock.patch.object(self.cards_mod, 'get_card_db') as card_db:
            for url in ('/api/cards/search?q=synthetic-card', '/api/cards/filter'):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn('禁限规则', response.json['error'])
                    self.assertEqual(response.json['cards'], [])
        upstream.assert_not_called()
        card_db.assert_not_called()

    def _validate_deck(self, main_ids, extra_ids=()):
        def rows(ids):
            return json.dumps([{'id': card_id, 'name': '合成卡', 'types': '怪兽卡'}
                               for card_id in ids])
        with self.app.test_request_context('/decks/new', method='POST', data={
                'title': '合成卡组', 'description': '', 'main_json': rows(main_ids),
                'extra_json': rows(extra_ids), 'side_json': '[]'}), \
                mock.patch.object(self.decks_mod, '_check_redline', return_value=False):
            return self.decks_mod._validate_form()

    def test_no_valid_rules_block_deck_save_validation(self):
        self.rule_path.unlink()
        result = self._validate_deck(range(900000001, 900000041))
        self.assertIn('禁限规则暂不可用', result[-1])

    def test_rule_reload_replaces_old_entries_and_recovers_availability(self):
        self._write_rules('{')
        self.assertFalse(self.decks_mod.get_banlist_status()['available'])
        self._write_rules({'cards': {'123': 0}, 'aliases': {'122': 123}})
        self.assertEqual(self.decks_mod.get_banlist(), {'123': 0})
        self._write_rules({'cards': {'456': 2}})
        self.assertEqual(self.decks_mod.get_banlist(), {'456': 2})
        self.assertEqual(self.decks_mod.get_banlist_aliases(), {})
        self.assertFalse(self.decks_mod.get_banlist_status()['stale'])

    def test_original_passcode_and_main_image_share_limited_card_count(self):
        self._write_rules({'cards': {'18144507': 1},
                           'aliases': {'18144506': 18144507}})
        result = self._validate_deck(
            [18144506] + list(range(900000001, 900000040)), [18144507])
        self.assertIn('限制卡', result[-1])
        self.assertIn('最多投入 1 张', result[-1])

    def test_unrestricted_alternate_images_share_the_three_copy_limit(self):
        self._write_rules({'cards': {'123': 0}, 'aliases': {'456': 789}})
        result = self._validate_deck([456, 456, 789] + list(range(900000001, 900000038)), [789])
        self.assertIn('最多放 3 张', result[-1])
        valid = self._validate_deck([456, 456, 789] + list(range(900000001, 900000038)))
        self.assertIsNone(valid[-1])
        self.assertEqual([row['id'] for row in valid[2][:3]], [456, 456, 789])

    def test_alternate_image_status_is_current_in_cached_and_remote_cards(self):
        self._write_rules({'cards': {'18144507': 0},
                           'aliases': {'18144506': 18144507}})
        raw = self._cache_card(card_id=18144506, ban=3)
        with mock.patch.object(self.cards_mod, '_fetch_remote') as upstream:
            response = self.client.get('/api/cards/search?q=synthetic-card&ban=forbidden')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['cards'][0]['id'], 18144506)
        self.assertEqual(response.json['cards'][0]['ban'], 0)
        upstream.assert_not_called()
        self.assertEqual(self.cache_model.query.one().result_json, raw)
        normalized = self.cards_mod._normalize({'id': 18144506})
        self.assertEqual(normalized['id'], 18144506)
        self.assertEqual(normalized['ban'], 0)

    def test_card_ids_reject_ambiguous_or_unsafe_numbers(self):
        invalid_ids = [None, True, False, '123', 123.0, 1.5, 0, -1,
                       2 ** 53, float('inf'), float('nan'), {}, []]
        for card_id in invalid_ids:
            with self.subTest(card_id=card_id):
                cards, error = self.decks_mod._parse_zone(
                    json.dumps([{'id': card_id}]), '主卡组')
                self.assertIsNone(cards)
                self.assertIsNotNone(error)
        cards, error = self.decks_mod._parse_zone('[{"id":1e309}]', '主卡组')
        self.assertIsNone(cards)
        self.assertIsNotNone(error)

    def test_positive_integer_ids_keep_unknown_card_compatibility(self):
        for card_id in (1, 900000001, 2 ** 53 - 1):
            with self.subTest(card_id=card_id):
                cards, error = self.decks_mod._parse_zone(
                    json.dumps([{'id': card_id, 'name': '合成先行卡',
                                 'types': '怪兽卡 融合'}]), '主卡组')
                self.assertIsNone(error)
                self.assertEqual(cards[0]['id'], card_id)
                self.assertEqual(cards[0]['types'], '怪兽卡 融合')

    def test_upload_route_reads_the_configured_temporary_directory(self):
        content = b'synthetic isolated upload'
        (self.upload_dir / 'synthetic.txt').write_bytes(content)
        response = self.client.get('/uploads/synthetic.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, content)
        response.close()


if __name__ == '__main__':
    unittest.main()
