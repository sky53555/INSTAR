import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import zipfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM4sAAAAASUVORK5CYII=')

class StudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.old_data = server.DATA
        server.DATA = Path(cls.folder.name)
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        server.DATA = cls.old_data
        cls.folder.cleanup()

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers or ({'Content-Type': 'application/json'} if body is not None else {}))
        with urllib.request.urlopen(request) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get_content_type() == 'application/json' else raw

    def test_content_image_backup_and_delete(self):
        self.assertIn('모아'.encode(), self.request('/'))
        post = self.request('/api/posts', {'title': '콘텐츠 테스트', 'account': 1, 'caption': '한글 보관', 'status': '게시 예정', 'scheduled': '2026-11-07T09:00'})
        ident = post['id']
        self.request('/api/accounts', {'accounts': ['첫 계정', '두 번째 계정']})
        changed = self.request('/api/posts/' + ident, {'caption': '수정한 캡션', 'metrics': {'reach': 100, 'saves': 5, 'shares': 2}})
        self.assertEqual(changed['metrics']['saves'], 5)
        image_post = self.request('/api/posts/' + ident + '/image', {'data': base64.b64encode(PNG).decode()})
        self.assertEqual(self.request(image_post['images'][0]['url']), PNG)
        with patch.dict('os.environ', {'CONTENT_STUDIO_API_KEY': 'test-disabled-key'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/posts/' + ident + '/generate', {'prompt': 'test'})
            self.assertEqual(error.exception.code, 403)
            self.assertFalse(self.request('/api/state')['generation'])
        state = self.request('/api/state')
        self.assertEqual(state['accounts'], ['첫 계정', '두 번째 계정'])
        self.assertEqual(state['posts'][0]['caption'], '수정한 캡션')
        with zipfile.ZipFile(io.BytesIO(self.request('/api/backup'))) as z:
            content = json.loads(z.read('contents.json'))
            self.assertEqual(content['posts'][0]['id'], ident)
            self.assertEqual(len(z.namelist()), 2)
        self.request('/api/posts/' + ident + '/remove-image', {'id': image_post['images'][0]['id']})
        self.request('/api/posts/' + ident + '/delete', {})
        self.assertEqual(self.request('/api/state')['posts'], [])
        self.assertEqual(list((server.DATA / 'images').iterdir()), [])

    def test_validation_and_origin_protection(self):
        for payload in [{'title': ''}, {'title': 'x', 'account': 3}, {'title': 'x', 'metrics': {'reach': -1}}, {'title': 'x', 'scheduled': 'bad'}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/posts', payload)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/posts', {'title': 'x'}, {'Content-Type': 'application/json', 'Origin': 'https://untrusted.example'})
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(ValueError):
            server.store_image(b'<svg>not a raster image</svg>')

if __name__ == '__main__':
    unittest.main()
