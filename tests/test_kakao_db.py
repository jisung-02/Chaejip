import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.kakao_db import collect_database, selected_room


@unittest.skipUnless(shutil.which('sqlcipher'), 'SQLCipher required for encrypted DB integration')
class KakaoDatabaseTests(unittest.TestCase):
    def test_exact_room_preserves_original_text_and_excludes_other_rooms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'encrypted.db'
            key = 'a' * 256
            sql = f"""PRAGMA key='{key}'; PRAGMA cipher_compatibility=3;
            CREATE TABLE NTChatRoom(chatId INTEGER, linkId INTEGER);
            CREATE TABLE NTOpenLink(linkId INTEGER, linkName TEXT);
            CREATE TABLE NTUser(userId INTEGER, friendNickName TEXT, displayName TEXT, nickName TEXT);
            CREATE TABLE NTChatMessage(chatId INTEGER, logId INTEGER, authorId INTEGER, sentAt INTEGER, message TEXT);
            INSERT INTO NTChatRoom VALUES(1,10),(2,20);
            INSERT INTO NTOpenLink VALUES(10,'지정방'),(20,'다른방');
            INSERT INTO NTUser VALUES(3,NULL,'작성자',NULL);
            INSERT INTO NTChatMessage VALUES(1,100,3,1700000000,'  원문\n두 번째 줄  '),(2,200,3,1700000001,'DO NOT COLLECT');
            """
            subprocess.run([shutil.which('sqlcipher'), str(path)], input=sql, text=True, capture_output=True, check=True)
            with patch('crawler.kakao_db.resolve_database', return_value=(path, key)):
                items = collect_database({'room_title': '지정방'})
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['body'], '  원문\n두 번째 줄  ')
            self.assertEqual(items[0]['key'], '1:100')
            self.assertEqual(items[0]['author'], '작성자')
            with self.assertRaises(ValueError):
                selected_room(path, key, "지정방' OR 1=1 --")
            # Reading uses -readonly and must leave the encrypted file unchanged.
            before = path.read_bytes()
            selected_room(path, key, '지정방')
            self.assertEqual(path.read_bytes(), before)
