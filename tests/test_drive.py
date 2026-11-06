import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive
import server
from test_studio import PNG

class DriveTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_data = server.DATA
        server.DATA = Path(self.folder.name)
        with server.db():
            pass

    def tearDown(self):
        server.DATA = self.old_data
        self.folder.cleanup()

    def fake_request(self, data, path, params=None, binary=False, limit=None):
        if path == 'files/' + drive.DEFAULT_FOLDER:
            return {'id': drive.DEFAULT_FOLDER, 'name': '보관함', 'mimeType': drive.FOLDER}
        if path == 'files':
            return {'files': [{'id': 'image-1', 'name': '이미지.png', 'mimeType': 'image/png', 'modifiedTime': self.modified},
                              {'id': 'doc-1', 'name': '대본', 'mimeType': drive.DOC, 'modifiedTime': self.modified}]}
        if path == 'files/image-1':
            return PNG
        if path == 'files/doc-1/export':
            return self.text.encode()
        raise AssertionError(path)

    def test_sync_idempotency_updates_conflicts_and_backup_separation(self):
        self.modified = '2026-11-06T00:00:00Z'
        self.text = '첫 문서'
        with patch.object(drive, 'request', side_effect=self.fake_request):
            first = drive.sync(server)
            self.assertEqual(first['created'], 1)
            self.assertEqual(drive.sync(server)['unchanged'], 1)
            with server.db() as conn:
                item = json.loads(conn.execute('SELECT body FROM posts').fetchone()['body'])
                self.assertIn('첫 문서', item['script'])
                self.assertEqual(len(item['images']), 1)
                item['script'] = '사용자가 수정한 대본'
                item['caption'] = '내 캡션'
                server.save(conn, item)
            self.modified = '2026-11-07T00:00:00Z'
            self.text = '드라이브 수정본'
            second = drive.sync(server)
            self.assertEqual(second['updated'], 1)
            self.assertEqual(len(second['conflicts']), 1)
            with server.db() as conn:
                item = server.get(conn, item['id'])
                self.assertEqual(item['script'], '사용자가 수정한 대본')
                self.assertEqual(item['caption'], '내 캡션')
                self.assertIn('드라이브 수정본', item['drive']['imported_text'])
                self.assertEqual(len(item['images']), 1)
            self.assertEqual(len(list((server.DATA / 'images').iterdir())), 1)

    def test_oauth_state_pkce_and_private_storage(self):
        env = {'GOOGLE_DRIVE_CLIENT_ID': 'test-client', 'GOOGLE_DRIVE_CLIENT_SECRET': 'test-secret'}
        with patch.dict('os.environ', env):
            url = drive.authorization_url()
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            self.assertEqual(query['scope'], [drive.SCOPES])
            self.assertEqual(query['code_challenge_method'], ['S256'])
            state = query['state'][0]
            with patch.object(drive, 'token_request', return_value={'access_token': 'test-access', 'refresh_token': 'test-refresh', 'expires_in': 3600}) as exchange:
                drive.callback(server.DATA, urllib.parse.urlencode({'state': state, 'code': 'test-code'}))
                self.assertIn('code_verifier', exchange.call_args.args[0])
            self.assertTrue(drive.status(server.DATA)['connected'])
            self.assertEqual((server.DATA / 'drive-token.json').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                drive.callback(server.DATA, urllib.parse.urlencode({'state': state, 'code': 'test-code'}))
            drive.disconnect(server.DATA)
            self.assertFalse(drive.status(server.DATA)['connected'])

    def test_validation_and_missing_connection(self):
        with self.assertRaises(ValueError):
            drive.set_config(server.DATA, {'folder': 'https://untrusted.example/folders/1234567890123'})
        config = drive.set_config(server.DATA, {'folder': 'https://drive.google.com/drive/folders/' + drive.DEFAULT_FOLDER + '?usp=drive_link', 'account': 1})
        self.assertEqual(config['folder'], drive.DEFAULT_FOLDER)
        self.assertEqual(config['account'], 1)
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'OAuth'):
                drive.authorization_url()
        with self.assertRaisesRegex(ValueError, '연결'):
            drive.access_token(server.DATA)

    def test_pagination_and_subfolders(self):
        responses = [{'files': [{'id': 'child', 'name': '콘텐츠1', 'mimeType': drive.FOLDER}], 'nextPageToken':'next'},
                     {'files': [{'id': 'a', 'name': 'a.txt', 'mimeType': 'text/plain'}]},
                     {'files': [{'id': 'b', 'name': 'b.png', 'mimeType': 'image/png'}]}]
        with patch.object(drive, 'request', side_effect=responses) as request:
            result = drive.scan(server.DATA, drive.DEFAULT_FOLDER)
            self.assertEqual(len(result), 2)
            self.assertEqual(request.call_args_list[1].args[2]['pageToken'], 'next')
            self.assertEqual(result[1]['name'], '콘텐츠1')

if __name__ == '__main__':
    unittest.main()
