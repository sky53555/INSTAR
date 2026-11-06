"""Google Drive read-only import. Tokens stay on this server, outside backups."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qs, quote
from urllib.request import Request, urlopen

DEFAULT_FOLDER = '1Xze-MFZpmFKE6nP2UhVFv4whrdDK-ZLy'
SCOPES = 'https://www.googleapis.com/auth/drive.readonly'
LOCK = threading.Lock()
SYNC_LOCK = threading.Lock()
PENDING = {}
FOLDER = 'application/vnd.google-apps.folder'
DOC = 'application/vnd.google-apps.document'
IMAGES = ('image/png', 'image/jpeg', 'image/webp')
TEXTS = ('text/plain', 'text/markdown', 'application/json')


def config_path(data):
    return data / 'drive-config.json'


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def configuration(data):
    return read_json(config_path(data), {'folder': DEFAULT_FOLDER, 'account': 0, 'last_sync': None})


def credentials():
    client = os.environ.get('GOOGLE_DRIVE_CLIENT_ID', '')
    secret = os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET', '')
    redirect = os.environ.get('GOOGLE_DRIVE_REDIRECT_URI', 'http://127.0.0.1:8000/api/drive/callback')
    return client, secret, redirect


def status(data):
    client, secret, redirect = credentials()
    config = configuration(data)
    return {**config, 'configured': bool(client and secret),
            'connected': (data / 'drive-token.json').exists(),
            'redirect_uri': redirect, 'readonly': True}


def set_config(data, body):
    raw = body.get('folder', '')
    if not isinstance(raw, str):
        raise ValueError('드라이브 폴더 링크를 입력해 주세요.')
    if raw.startswith('https://'):
        parsed = urlsplit(raw)
        if parsed.hostname != 'drive.google.com':
            raise ValueError('구글 드라이브 폴더 링크를 입력해 주세요.')
        match = re.search(r'/folders/([\w-]+)', parsed.path)
        raw = match.group(1) if match else ''
    if not re.fullmatch(r'[\w-]{10,200}', raw, re.ASCII):
        raise ValueError('구글 드라이브 폴더 ID를 확인해 주세요.')
    account = body.get('account', 0)
    if type(account) is not int or account not in (0, 1):
        raise ValueError('계정을 선택해 주세요.')
    with LOCK:
        config = configuration(data)
        config.update(folder=raw, account=account)
        private_write(config_path(data), config)
    return status(data)


def authorization_url():
    client, secret, redirect = credentials()
    if not client or not secret:
        raise ValueError('구글 연결 설정이 필요합니다. 연결 안내의 일회성 OAuth 설정을 완료해 주세요. AI API 키나 유료 AI 결제는 필요하지 않습니다.')
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    with LOCK:
        for key in list(PENDING):
            if PENDING[key]['expires'] < time.time():
                del PENDING[key]
        PENDING[state] = {'expires': time.time() + 600, 'verifier': verifier, 'redirect': redirect}
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': client, 'redirect_uri': redirect, 'response_type': 'code',
        'scope': SCOPES, 'state': state, 'access_type': 'offline', 'prompt': 'consent',
        'code_challenge': challenge, 'code_challenge_method': 'S256'})


def token_request(fields):
    client, secret, _ = credentials()
    request = Request('https://oauth2.googleapis.com/token',
                      data=urlencode({**fields, 'client_id': client, 'client_secret': secret}).encode(),
                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urlopen(request, timeout=30) as response:
            value = json.load(response)
        if not value.get('access_token'):
            raise ValueError('구글 연결 응답에 인증 정보가 없습니다. 다시 연결해 주세요.')
        return value
    except (HTTPError, URLError, TimeoutError):
        raise ValueError('구글 인증을 완료하지 못했습니다. 연결 설정과 네트워크를 확인하거나 다시 로그인해 주세요.') from None


def callback(data, query):
    params = parse_qs(query)
    state = params.get('state', [''])[0]
    with LOCK:
        pending = PENDING.pop(state, None)
    if not pending or pending['expires'] < time.time():
        raise ValueError('연결 요청이 만료되었거나 올바르지 않습니다. 작업실에서 다시 연결해 주세요.')
    if params.get('error') or not params.get('code'):
        raise ValueError('구글 연결이 취소되었습니다. 필요할 때 다시 연결할 수 있습니다.')
    value = token_request({'grant_type': 'authorization_code', 'code': params['code'][0],
                           'redirect_uri': pending['redirect'], 'code_verifier': pending['verifier']})
    if not value.get('refresh_token'):
        raise ValueError('지속 연결 권한을 받지 못했습니다. 다시 연결해 주세요.')
    value['expires_at'] = time.time() + value.get('expires_in', 3600) - 60
    with LOCK:
        private_write(data / 'drive-token.json', value)


def access_token(data):
    with LOCK:
        path = data / 'drive-token.json'
        value = read_json(path, {})
        if not value:
            raise ValueError('먼저 구글 계정을 연결해 주세요.')
        if value.get('expires_at', 0) < time.time():
            new = token_request({'grant_type': 'refresh_token', 'refresh_token': value['refresh_token']})
            value.update(new)
            value['expires_at'] = time.time() + new.get('expires_in', 3600) - 60
            private_write(path, value)
        return value['access_token']


def disconnect(data):
    with LOCK:
        (data / 'drive-token.json').unlink(missing_ok=True)
    # Local disconnect does not delete imported data or revoke Google's grant.
    return {'ok': True}


def request(data, path, params=None, binary=False, limit=15 * 1024 * 1024):
    url = 'https://www.googleapis.com/drive/v3/' + path
    if params:
        url += '?' + urlencode(params)
    req = Request(url, headers={'Authorization': 'Bearer ' + access_token(data)})
    try:
        with urlopen(req, timeout=60) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('드라이브 파일이 가져오기 제한을 초과했습니다.')
        return raw if binary else json.loads(raw)
    except HTTPError as error:
        if error.code in (401, 403):
            raise ValueError('드라이브 읽기 권한 또는 API 할당량을 확인해 주세요. 필요하면 구글 계정을 다시 연결해 주세요.') from None
        if error.code == 404:
            raise ValueError('드라이브 폴더나 파일에 접근할 수 없습니다. 연결한 계정의 공유 권한을 확인해 주세요.') from None
        raise ValueError(f'구글 드라이브 응답 오류 ({error.code}). 잠시 후 다시 시도해 주세요.') from None
    except (URLError, TimeoutError):
        raise ValueError('구글 드라이브에 연결하지 못했습니다. 네트워크 설정을 확인해 주세요.') from None


def scan(data, folder):
    pending = [(folder, '')]
    visited = set()
    groups = []
    count = 0
    while pending:
        ident, label = pending.pop(0)
        if ident in visited:
            continue
        visited.add(ident)
        if len(visited) > 100:
            raise ValueError('하위 폴더가 100개를 넘습니다. 연결 폴더를 더 작게 나눠 주세요.')
        files, token = [], None
        while True:
            params = {'q': f"'{ident}' in parents and trashed = false", 'pageSize': 100,
                      'fields': 'nextPageToken,files(id,name,mimeType,modifiedTime,size)',
                      'orderBy': 'name', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
            if token:
                params['pageToken'] = token
            response = request(data, 'files', params)
            batch = response.get('files', [])
            files.extend(batch)
            count += len(batch)
            if count > 1000:
                raise ValueError('파일이 1,000개를 넘습니다. 연결 폴더를 더 작게 나눠 주세요.')
            token = response.get('nextPageToken')
            if not token:
                break
        supported = []
        for file in files:
            mime = file['mimeType']
            if mime == FOLDER:
                pending.append((file['id'], (label + ' / ' if label else '') + file['name']))
            elif mime in IMAGES + TEXTS + (DOC,) or file['name'].lower().endswith(('.txt', '.md')):
                supported.append(file)
        if supported:
            groups.append({'id': ident, 'name': label or '드라이브 콘텐츠', 'files': supported})
    return groups


def parse_content(text, title):
    aliases = {'제목': 'title', '캡션': 'caption', '해시태그': 'caption', '대본': 'script',
               '스크립트': 'script', '이미지 생성 지시': 'prompt', '이미지 프롬프트': 'prompt', '프롬프트': 'prompt'}
    fields, field = {}, 'script'
    for line in text.splitlines():
        clean = re.sub(r'^\s*(?:#{1,6}\s*|[-*]\s+)?', '', line).replace('**', '')
        match = re.match(r'^(제목|캡션|해시태그|대본|스크립트|이미지 생성 지시|이미지 프롬프트|프롬프트)\s*[:：]\s*(.*)$', clean)
        if match:
            field = aliases[match[1]]
            value = match[2]
        else:
            value = line
        fields[field] = (fields.get(field, '') + '\n' + value).strip()
    fields['title'] = fields.get('title') or title
    for field in ['caption', 'script', 'prompt']:
        fields.setdefault(field, '')
    return fields


def sync(studio):
    if not SYNC_LOCK.acquire(blocking=False):
        raise ValueError('동기화가 이미 진행 중입니다.')
    try:
        data = studio.DATA
        config = configuration(data)
        folder = config['folder']
        metadata = request(data, 'files/' + quote(folder, safe=''), {'fields': 'id,name,mimeType', 'supportsAllDrives': 'true'})
        if metadata.get('mimeType') != FOLDER:
            raise ValueError('연결 대상이 드라이브 폴더가 아닙니다.')
        groups = scan(data, folder)
        created, updated, unchanged, conflicts = 0, 0, 0, []
        with studio.db() as conn:
            for group in groups:
                images = [f for f in group['files'] if f['mimeType'] in IMAGES]
                if len(images) > 20:
                    raise ValueError(f"{group['name']}: 폴더당 이미지 20개까지 지원합니다.")
                signature = hashlib.sha256(json.dumps([(f['id'], f.get('modifiedTime'), f['name']) for f in group['files']]).encode()).hexdigest()
                existing = None
                for row in conn.execute('SELECT body FROM posts'):
                    candidate = json.loads(row['body'])
                    if candidate.get('drive', {}).get('folder') == group['id']:
                        existing = candidate
                        break
                if existing and existing['drive'].get('signature') == signature:
                    unchanged += 1
                    continue
                texts = []
                for file in group['files']:
                    if file['mimeType'] in IMAGES:
                        continue
                    path = 'files/' + quote(file['id'], safe='')
                    params = {'mimeType': 'text/plain'} if file['mimeType'] == DOC else {'alt': 'media', 'supportsAllDrives': 'true'}
                    raw = request(data, path + '/export' if file['mimeType'] == DOC else path, params, binary=True, limit=100000)
                    texts.append(file['name'] + '\n' + raw.decode('utf-8-sig'))
                text = '\n\n'.join(texts)
                if len(text) > 100000:
                    raise ValueError(f"{group['name']}: 문서가 너무 큽니다. 폴더별 100,000자까지 지원합니다.")
                title = metadata['name'] if group['id'] == folder else group['name']
                fields = parse_content(text, title)
                item = studio.validate({**fields, 'account': config['account']}, existing)
                conflict = False
                if existing:
                    prior = existing['drive'].get('imported_fields', {
                        'title': existing['drive'].get('imported_title', ''),
                        'script': existing['drive'].get('imported_text', ''), 'caption': '', 'prompt': ''})
                    for field in ['title', 'caption', 'script', 'prompt']:
                        if existing.get(field, '') != prior.get(field, ''):
                            item[field] = existing[field]
                            conflict = True
                    if conflict:
                        conflicts.append(title)
                    item['account'] = existing['account']
                old_images = {i.get('drive_id'): i for i in (existing or {}).get('images', []) if i.get('drive_id')}
                new_images = [i for i in (existing or {}).get('images', []) if not i.get('drive_id')]
                # Keep removed images in the app; Drive removal must never erase a local copy.
                new_images.extend(old_images.values())
                staged, superseded = [], []
                try:
                    for file in images:
                        old = old_images.get(file['id'])
                        if old and old.get('drive_modified') == file.get('modifiedTime'):
                            continue
                        if not old and len(new_images) >= 20:
                            raise ValueError(f'{title}: 이미지가 20개를 넘습니다.')
                        raw = request(data, 'files/' + quote(file['id'], safe=''), {'alt': 'media', 'supportsAllDrives': 'true'}, binary=True)
                        image = studio.store_image(raw)
                        staged.append(image['id'])
                        image.update(source='drive', drive_id=file['id'], drive_modified=file.get('modifiedTime'), drive_name=file['name'])
                        if old:
                            new_images.remove(old)
                            superseded.append(old['id'])
                        new_images.append(image)
                    item['images'] = new_images
                    item['drive'] = {'folder': group['id'], 'root': folder, 'signature': signature,
                                     'imported_text': text, 'imported_title': fields['title'], 'imported_fields': fields,
                                     'synced': studio.now(), 'conflict': conflict,
                                     'files': [{'id': f['id'], 'name': f['name'], 'mimeType': f['mimeType']} for f in group['files']]}
                    studio.save(conn, item)
                except Exception:
                    for image in staged:
                        (data / 'images' / image).unlink(missing_ok=True)
                    raise
                for image in superseded:
                    (data / 'images' / image).unlink(missing_ok=True)
                if existing:
                    updated += 1
                else:
                    created += 1
        config['last_sync'] = datetime.now(timezone.utc).isoformat()
        private_write(config_path(data), config)
        return {'created': created, 'updated': updated, 'unchanged': unchanged, 'conflicts': conflicts, 'groups': len(groups)}
    finally:
        SYNC_LOCK.release()
