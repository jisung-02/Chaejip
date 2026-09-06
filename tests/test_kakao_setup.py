import hashlib
import json
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.kakao_setup import recover_id, setup_account


class KakaoSetupTests(unittest.TestCase):
    def test_recovery_timeout_is_bounded_and_does_not_expose_hash(self):
        target = hashlib.sha512(b'4242').hexdigest()
        with patch('crawler.kakao_setup.recovery_binary', return_value=Path('/test/helper')), \
             patch('crawler.kakao_setup.subprocess.run', side_effect=subprocess.TimeoutExpired('/test/helper', 300)):
            with self.assertRaises(RuntimeError) as error:
                recover_id(target, Path('/unused'))
        self.assertNotIn(target, str(error.exception))

    @unittest.skipUnless(platform.system() == 'Darwin', 'macOS CommonCrypto helper')
    def test_native_recovery_matches_synthetic_hash_and_stops_at_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = hashlib.sha512(b'4242').hexdigest()
            self.assertEqual(recover_id(target, root, maximum=5000, timeout=10), 4242)
            with self.assertRaises(RuntimeError):
                recover_id(target, root, maximum=1000, timeout=10)

    def test_setup_preserves_source_scope_and_caches_only_verified_id(self):
        for known in (False, True):
            with self.subTest(known=known), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / 'sources.json'
                config.write_text(json.dumps([{'id': 'kakao', 'kind': 'kakao_db', 'room_title': '선택한 방', 'enabled': False}]))
                target = hashlib.sha512(b'4242').hexdigest()
                metadata = ([root / 'app.db'], {4242} if known else set(), {target})
                with patch('crawler.kakao_setup.platform.system', return_value='Darwin'), \
                     patch('crawler.kakao_setup.sqlcipher_path'), \
                     patch('crawler.kakao_setup.local_metadata', return_value=metadata), \
                     patch('crawler.kakao_setup.device_uuid', return_value='00000000-1111-2222-3333-444444444444'), \
                     patch('crawler.kakao_setup.recover_id', return_value=4242) as recover, \
                     patch('crawler.kakao_setup.query_database', return_value=[{'n': 3}]):
                    result = setup_account(config, root, 'kakao')
                self.assertEqual(recover.call_count, 0 if known else 1)
                account = Path(result['account_file'])
                self.assertEqual(json.loads(account.read_text()), {'user_id': 4242})
                self.assertEqual(account.stat().st_mode & 0o777, 0o600)
                source = json.loads(config.read_text())[0]
                self.assertEqual(source['room_title'], '선택한 방')
                self.assertFalse(source['enabled'])
                self.assertNotIn('4242', json.dumps(result))
                self.assertNotIn('key', account.read_text())

    def test_failed_db_verification_does_not_write_account_or_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'sources.json'
            original = json.dumps([{'id': 'kakao', 'kind': 'kakao_db', 'room_title': '선택한 방'}])
            config.write_text(original)
            target = hashlib.sha512(b'4242').hexdigest()
            with patch('crawler.kakao_setup.platform.system', return_value='Darwin'), \
                 patch('crawler.kakao_setup.sqlcipher_path'), \
                 patch('crawler.kakao_setup.local_metadata', return_value=([root / 'app.db'], {4242}, {target})), \
                 patch('crawler.kakao_setup.device_uuid', return_value='00000000-1111-2222-3333-444444444444'), \
                 patch('crawler.kakao_setup.query_database', side_effect=RuntimeError('cannot open')):
                with self.assertRaises(RuntimeError):
                    setup_account(config, root, 'kakao')
            self.assertEqual(config.read_text(), original)
            self.assertFalse((root / 'kakao-account.json').exists())
