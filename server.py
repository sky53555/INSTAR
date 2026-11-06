"""Local, single-user content studio. No third-party Python dependencies."""
import base64
import binascii
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import html
import sys
import drive
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('STUDIO_DATA_DIR', str(ROOT / '.studio'))).resolve()
STATUSES = ['보관', '제작 중', '게시 예정', '게시 완료']


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'images').mkdir(exist_ok=True)
    conn = sqlite3.connect(DATA / 'studio.db', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
    conn.commit()
    return conn


def now():
    return datetime.now(timezone.utc).isoformat()


def validate(body, previous=None):
    if not isinstance(body, dict):
        raise ValueError('객체 형식의 콘텐츠가 필요합니다.')
    item = dict(previous or {})
    for name in ['title', 'caption', 'script', 'prompt', 'scheduled', 'link', 'source']:
        value = body.get(name, item.get(name, ''))
        if not isinstance(value, str) or len(value) > 100000:
            raise ValueError('텍스트 형식을 확인해 주세요.')
        item[name] = value.strip()
    if not item['title']:
        raise ValueError('제목을 입력해 주세요.')
    if item['link'] and not item['link'].startswith(('https://', 'http://')):
        raise ValueError('게시 링크는 http 또는 https 주소여야 합니다.')
    item['account'] = body.get('account', item.get('account', 0))
    item['status'] = body.get('status', item.get('status', '보관'))
    item['format'] = body.get('format', item.get('format', '카드뉴스'))
    if type(item['account']) is not int or item['account'] not in (0, 1):
        raise ValueError('계정을 선택해 주세요.')
    if item['status'] not in STATUSES or item['format'] not in ['카드뉴스', '이미지', '릴스']:
        raise ValueError('상태 또는 형식을 확인해 주세요.')
    if item['scheduled']:
        try:
            datetime.strptime(item['scheduled'], '%Y-%m-%dT%H:%M')
        except ValueError:
            raise ValueError('게시 일정을 확인해 주세요.')
    metrics = body.get('metrics', item.get('metrics', {}))
    if not isinstance(metrics, dict):
        raise ValueError('성과 수치를 확인해 주세요.')
    item['metrics'] = {}
    for name in ['reach', 'likes', 'saves', 'shares', 'comments']:
        value = metrics.get(name, None)
        if value is not None and (type(value) is not int or value < 0 or value > 10**12):
            raise ValueError('성과는 0 이상의 정수로 입력해 주세요.')
        item['metrics'][name] = value
    item.setdefault('images', [])
    item.setdefault('id', uuid.uuid4().hex)
    item.setdefault('created', now())
    item['updated'] = now()
    return item


def save(conn, item):
    conn.execute('INSERT OR REPLACE INTO posts VALUES (?, ?)', (item['id'], json.dumps(item, ensure_ascii=False)))
    conn.commit()


def get(conn, ident):
    row = conn.execute('SELECT body FROM posts WHERE id=?', (ident,)).fetchone()
    if not row:
        raise LookupError('콘텐츠를 찾을 수 없습니다.')
    return json.loads(row['body'])


def store_image(raw):
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise ValueError('이미지는 15MB 이하로 준비해 주세요.')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        ext = 'png'
    elif raw.startswith(b'\xff\xd8\xff'):
        ext = 'jpg'
    elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        ext = 'webp'
    else:
        raise ValueError('PNG, JPG, WebP 이미지만 지원합니다.')
    filename = f'{uuid.uuid4().hex}.{ext}'
    (DATA / 'images' / filename).write_bytes(raw)
    return {'id': filename, 'url': '/images/' + filename, 'created': now()}


class Handler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        # Never log OAuth authorization codes or state query parameters.
        self.log_message("%s %s %s", self.command, urlsplit(self.path).path, code)

    def reply(self, status, data, content_type='application/json; charset=utf-8', attachment=None):
        raw = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith('/api/drive/callback?'):
            try:
                drive.callback(DATA, urlsplit(self.path).query)
                message = '구글 드라이브가 연결되었습니다. 작업실에서 동기화를 눌러 주세요.'
                code = 200
            except ValueError as error:
                message, code = str(error), 400
            return self.reply(code, ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>드라이브 연결</title><body><h1>' + html.escape(message) + '</h1><a href="/">작업실로 돌아가기</a></body></html>').encode(), 'text/html; charset=utf-8')
        if self.path == '/api/drive/status':
            return self.reply(200, drive.status(DATA))
        with db() as conn:
            if self.path == '/api/state':
                row = conn.execute('SELECT body FROM settings WHERE id=1').fetchone()
                accounts = json.loads(row['body']) if row else ['계정 A', '계정 B']
                posts = [json.loads(r['body']) for r in conn.execute('SELECT body FROM posts ORDER BY rowid DESC')]
                return self.reply(200, {'posts': posts, 'accounts': accounts,
                                       'generation': False, 'drive': drive.status(DATA)})
            if self.path == '/api/backup':
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                    posts = [json.loads(r['body']) for r in conn.execute('SELECT body FROM posts')]
                    row = conn.execute('SELECT body FROM settings WHERE id=1').fetchone()
                    archive.writestr('contents.json', json.dumps({'version': 1, 'posts': posts, 'accounts': json.loads(row['body']) if row else ['계정 A', '계정 B']}, ensure_ascii=False, indent=2))
                    for p in (DATA / 'images').iterdir():
                        if p.is_file():
                            archive.write(p, 'images/' + p.name)
                return self.reply(200, output.getvalue(), 'application/zip', 'content-studio-backup.zip')
        if re.fullmatch(r'/images/[a-f0-9]{32}\.(png|jpg|webp)', self.path):
            path = DATA / 'images' / self.path.rsplit('/', 1)[-1]
            if path.is_file():
                return self.reply(200, path.read_bytes(), {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[path.suffix[1:]])
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path in files:
            name, mime = files[self.path]
            return self.reply(200, (ROOT / name).read_bytes(), mime)
        self.reply(404, {'error': '페이지를 찾을 수 없습니다.'})

    def do_POST(self):
        # Browser cross-origin writes are rejected. API clients may send JSON without Origin.
        origin = self.headers.get('Origin')
        if origin and origin not in ['http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')]:
            return self.reply(403, {'error': '외부 페이지에서의 저장 요청은 허용하지 않습니다.'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'JSON 요청이 필요합니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 22 * 1024 * 1024:
                raise ValueError('요청 크기를 확인해 주세요.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('JSON 객체가 필요합니다.')
            if self.path.startswith('/api/drive/'):
                if self.path == '/api/drive/config':
                    return self.reply(200, drive.set_config(DATA, body))
                if self.path == '/api/drive/connect':
                    return self.reply(200, {'url': drive.authorization_url()})
                if self.path == '/api/drive/sync':
                    return self.reply(200, drive.sync(sys.modules[__name__]))
                if self.path == '/api/drive/disconnect':
                    return self.reply(200, drive.disconnect(DATA))
                return self.reply(404, {'error': 'API를 찾을 수 없습니다.'})
            if self.path.endswith('/generate'):
                return self.reply(403, {'error': '추가 비용 방지를 위해 유료 AI 생성은 비활성화했습니다. ChatGPT Pro에서 만든 이미지를 가져와 주세요.'})
            with db() as conn:
                if self.path == '/api/posts':
                    item = validate(body)
                    save(conn, item)
                    return self.reply(201, item)
                if self.path == '/api/accounts':
                    accounts = body.get('accounts')
                    if not isinstance(accounts, list) or len(accounts) != 2 or any(not isinstance(a, str) or not a.strip() or len(a) > 40 for a in accounts):
                        raise ValueError('두 계정의 이름을 40자 이내로 입력해 주세요.')
                    conn.execute('INSERT OR REPLACE INTO settings VALUES (1, ?)', (json.dumps([a.strip() for a in accounts]),))
                    conn.commit()
                    return self.reply(200, {'ok': True})
                match = re.fullmatch(r'/api/posts/([a-f0-9]{32})(?:/(image|delete|remove-image))?', self.path)
                if not match:
                    return self.reply(404, {'error': 'API를 찾을 수 없습니다.'})
                ident, action = match.groups()
                item = get(conn, ident)
                if action == 'delete':
                    conn.execute('DELETE FROM posts WHERE id=?', (ident,))
                    conn.commit()
                    for image in item['images']:
                        (DATA / 'images' / image['id']).unlink(missing_ok=True)
                    return self.reply(200, {'ok': True})
                if action == 'remove-image':
                    image = next((i for i in item['images'] if i['id'] == body.get('id')), None)
                    if not image:
                        raise LookupError('이미지를 찾을 수 없습니다.')
                    item['images'].remove(image)
                    save(conn, item)
                    (DATA / 'images' / image['id']).unlink(missing_ok=True)
                elif action == 'image':
                    if len(item['images']) >= 20:
                        raise ValueError('콘텐츠당 이미지는 20개까지 저장할 수 있습니다.')
                    try:
                        raw = base64.b64decode(body.get('data', ''), validate=True)
                    except (binascii.Error, TypeError):
                        raise ValueError('이미지 데이터를 확인해 주세요.') from None
                    image = store_image(raw)
                    image['source'] = 'uploaded'
                    item['images'].append(image)
                    item['updated'] = now()
                    save(conn, item)
                else:
                    item = validate(body, item)
                    save(conn, item)
                return self.reply(200, item)
        except LookupError as error:
            self.reply(404, {'error': str(error)})
        except (ValueError, UnicodeError) as error:
            self.reply(400, {'error': str(error)})
        except Exception:
            self.log_error('Internal storage or service error')
            self.reply(500, {'error': '저장 중 오류가 발생했습니다. 저장 공간과 서버 상태를 확인해 주세요.'})


if __name__ == '__main__':
    with db():
        pass
    address = os.environ.get('STUDIO_HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    print(f'Content Studio listening on {address}:{port}', flush=True)
    ThreadingHTTPServer((address, port), Handler).serve_forever()
