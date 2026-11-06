'use strict';
const $ = s => document.querySelector(s);
const form = $('#edit-form');
const dialog = $('#editor');
let state = {posts: [], accounts: ['계정 A', '계정 B'], generation: false};
let account = 'all', view = 'library', current = null, busy = false, dirty = false;
const metricNames = ['reach', 'likes', 'saves', 'shares', 'comments'];
const textNames = ['title', 'caption', 'script', 'prompt', 'scheduled', 'link'];
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 6000); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || '요청을 처리하지 못했습니다.');
  return result;
}
async function refresh() {
  state = await api('/api/state');
  state.accounts.forEach((a, i) => { $(`#account${i}`).textContent = a; $('#edit-account').options[i].textContent = a; });
  renderDrive();
  render();
}
function element(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function render() {
  const posts = state.posts.filter(p => account === 'all' || p.account === Number(account));
  $('#total').textContent = posts.length;
  $('#working').textContent = posts.filter(p => p.status === '제작 중').length;
  $('#scheduled').textContent = posts.filter(p => p.status === '게시 예정').length;
  $('#published').textContent = posts.filter(p => p.status === '게시 완료').length;
  $('#heading').textContent = {library: '콘텐츠 보관함', schedule: '게시 일정', insights: '성과 기록'}[view];
  $('#view-note').textContent = {library: '', schedule: '예약 시간은 기록용입니다. 인스타그램에 자동으로 게시되지는 않습니다.', insights: '직접 입력한 기록입니다. 저장·공유율 = (저장 + 공유) ÷ 도달 × 100. 성과 수치만으로 인과관계를 판단하지 않습니다.'}[view];
  let shown = posts.filter(p => (!$('#status').value || p.status === $('#status').value) && `${p.title} ${p.caption} ${p.script}`.toLowerCase().includes($('#search').value.toLowerCase()));
  if (view === 'schedule') shown = shown.filter(p => p.scheduled).sort((a,b) => a.scheduled.localeCompare(b.scheduled));
  if (view === 'insights') shown = shown.filter(p => p.status === '게시 완료');
  const container = $('#contents'); container.replaceChildren();
  if (!shown.length) {
    const empty = element('div', 'empty');
    empty.append(element('h3', '', view === 'library' ? '첫 번째 콘텐츠를 모아보세요' : '아직 기록이 없어요'), element('p', '', view === 'library' ? '채팅 결과를 붙여넣거나 구글 드라이브의 문서·이미지를 가져오세요.' : '콘텐츠를 열어 게시 일정이나 게시 완료 상태와 성과를 입력해 주세요.'));
    const add = element('button', '', '＋ 콘텐츠 가져오기'); add.onclick = () => openEditor(); empty.append(add); container.append(empty); return;
  }
  for (const p of shown) {
    const card = element('button', 'card'); card.onclick = () => openEditor(p);
    if (p.images.length) { const img = element('img', 'cover'); img.src = p.images[0].url; img.alt = p.title; img.loading = 'lazy'; card.append(img); }
    else { const cover = element('div', 'cover placeholder', '✧'); cover.append(element('small', '', '이미지를 기다리는 중')); card.append(cover); }
    const body = element('div', 'card-body'); const meta = element('div', 'card-meta'); meta.append(element('span', '', state.accounts[p.account] + ' · ' + p.format), element('span', 'badge', p.status));
    body.append(meta, element('h3', '', p.title), element('div', 'excerpt', p.caption || p.script || '캡션이나 대본을 추가해 주세요.'));
    if (view === 'insights') {
      const m = p.metrics, rate = m.reach > 0 && m.saves !== null && m.shares !== null ? ((m.saves + m.shares) / m.reach * 100).toFixed(1) + '%' : '미입력';
      body.append(element('div', 'metrics-line', `도달 ${m.reach ?? '미입력'} · 저장·공유율 ${rate}`));
    }
    const bottom = element('div', 'card-bottom'); bottom.append(element('span', '', p.scheduled ? p.scheduled.replace('T', ' ') + ' KST' : '게시 일정 미정'), element('span', '', `이미지 ${p.images.length}`)); body.append(bottom); card.append(body); container.append(card);
  }
}
function openEditor(post) {
  form.reset(); $('#import-text').value = ''; current = post ? structuredClone(post) : null; dirty = false;
  $('#editor-title').textContent = post ? '콘텐츠 작업실' : '콘텐츠 가져오기';
  $('#import-area').hidden = Boolean(post); $('#delete').hidden = !post; $('#save-status').textContent = '';
  if (post) {
    for (const name of [...textNames, 'account', 'format', 'status']) form.elements[name].value = post[name] ?? '';
    for (const name of metricNames) form.elements[name].value = post.metrics[name] ?? '';
  } else form.elements.account.value = account === 'all' ? '0' : account;
  $('#generation-note').textContent = '추가 AI 비용 없이 사용하세요. ChatGPT Pro에서 만든 이미지를 드라이브에서 가져오거나, 복사·붙여넣기와 드래그로 저장할 수 있어요. 이 사이트는 유료 AI API를 호출하지 않습니다.';
  $('#drive-origin')?.remove();
  if (post?.drive) {
    const origin = element('details'); origin.id = 'drive-origin';
    origin.append(element('summary', '', post.drive.conflict ? '드라이브 변경 내용 확인 — 내 대본 수정본은 유지했습니다' : '드라이브에서 가져온 원문'));
    origin.append(element('pre', 'drive-source', post.drive.imported_text || '이미지 콘텐츠'));
    const link = element('a', '', '드라이브 원본 폴더 열기 ↗'); link.href = 'https://drive.google.com/drive/folders/' + post.drive.folder; link.target = '_blank'; link.rel = 'noopener noreferrer'; origin.append(link);
    $('#import-area').after(origin);
  }
  renderImages(); dialog.showModal();
}
function values() {
  const data = {};
  for (const name of [...textNames, 'format', 'status']) data[name] = form.elements[name].value;
  data.account = Number(form.elements.account.value);
  data.metrics = Object.fromEntries(metricNames.map(name => [name, form.elements[name].value === '' ? null : Number(form.elements[name].value)]));
  if ($('#import-text').value) data.source = $('#import-text').value;
  return data;
}
async function persist() {
  if (!form.reportValidity()) throw Error('입력 내용을 확인해 주세요.');
  current = await api(current ? `/api/posts/${current.id}` : '/api/posts', values());
  dirty = false; $('#delete').hidden = false; $('#save-status').textContent = '저장됨';
  await refresh(); return current;
}
async function task(fn) {
  if (busy) return; busy = true;
  for (const id of ['save', 'delete']) $('#' + id).disabled = true;
  $('#save-status').textContent = '처리 중…';
  try { await fn(); } catch (error) { toast(error.message); $('#save-status').textContent = '다시 시도해 주세요'; }
  finally { busy = false; for (const id of ['save', 'delete']) $('#' + id).disabled = false; }
}
function closeEditor() { if (busy) return toast('진행 중인 작업이 끝날 때까지 기다려 주세요.'); if (dirty && !confirm('저장하지 않은 변경 사항을 닫을까요?')) return; dialog.close(); }
$('#close').onclick = closeEditor;
dialog.addEventListener('cancel', e => {e.preventDefault(); closeEditor();});
form.addEventListener('input', () => {dirty = true; $('#save-status').textContent = '저장 전';});
form.onsubmit = e => { e.preventDefault(); task(async () => {await persist(); toast('콘텐츠를 저장했습니다.'); dialog.close();}); };
$('#new').onclick = () => openEditor();
$('#search').oninput = render; $('#status').onchange = render;
document.querySelectorAll('[data-account]').forEach(b => b.onclick = () => { account = b.dataset.account; document.querySelectorAll('[data-account]').forEach(c => c.classList.toggle('selected', c === b)); render(); });
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => {view = b.dataset.view; document.querySelectorAll('[data-view]').forEach(c => c.classList.toggle('active', c === b)); render();});
$('#parse').onclick = () => {
  const raw = $('#import-text').value.trim(); if (!raw) return toast('채팅 결과를 먼저 붙여넣어 주세요.');
  const aliases = {'제목':'title','캡션':'caption','해시태그':'caption','대본':'script','스크립트':'script','이미지 생성 지시':'prompt','이미지 프롬프트':'prompt','프롬프트':'prompt','계정':'account','형식':'format','이미지':'script'};
  const result = {}; let field = 'script';
  for (const line of raw.split('\n')) {
    const clean = line.replace(/^\s*(?:#{1,6}\s*|[-*]\s+)?/, '').replace(/\*\*/g, '');
    const match = clean.match(/^(제목|캡션|해시태그|대본|스크립트|이미지 생성 지시|이미지 프롬프트|프롬프트|계정|형식)\s*[:：]\s*(.*)$/);
    if (match) {field = aliases[match[1]]; result[field] = (result[field] ? result[field] + '\n' : '') + match[2];}
    else result[field] = (result[field] ? result[field] + '\n' : '') + line;
  }
  for (const name of ['title','caption','script','prompt']) if (result[name]) form.elements[name].value = result[name].trim();
  if (!form.elements.title.value) form.elements.title.value = raw.split('\n').find(l => l.trim()).slice(0,100);
  const ai = state.accounts.indexOf((result.account || '').trim()); if (ai >= 0) form.elements.account.value = ai;
  if (['카드뉴스','이미지','릴스'].includes((result.format || '').trim())) form.elements.format.value = result.format.trim();
  dirty = true; toast('항목을 나눴습니다. 내용을 확인한 뒤 저장해 주세요.');
};
function renderImages() {
  const container = $('#images'); container.replaceChildren();
  for (const image of current?.images || []) {
    const wrap = element('div', 'image-item'); const img = element('img'); img.src = image.url; img.alt = '저장된 콘텐츠 이미지';
    const row = element('div'); const link = element('a', '', '원본 저장 ↓'); link.href = image.url; link.download = image.id;
    const remove = element('button', '', '삭제'); remove.type = 'button'; remove.onclick = () => {if (confirm('이 이미지를 삭제할까요?')) task(async () => {current = await api(`/api/posts/${current.id}/remove-image`, {id: image.id}); renderImages(); await refresh();});};
    row.append(link, remove); wrap.append(img, row); container.append(wrap);
  }
}
function readFile(file) { return new Promise((resolve,reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(Error('파일을 읽지 못했습니다.')); reader.readAsDataURL(file); }); }
async function upload(files) {
  if (!files.length) return;
  await task(async () => {
    if ([...files].some(f => !['image/png','image/jpeg','image/webp'].includes(f.type) || f.size > 15 * 1024 * 1024)) throw Error('15MB 이하의 PNG, JPG, WebP 파일을 선택해 주세요.');
    await persist(); let count = 0;
    try { for (const file of files) { current = await api(`/api/posts/${current.id}/image`, {data: await readFile(file)}); count++; renderImages(); } }
    finally { await refresh(); $('#save-status').textContent = `${count}개 이미지 저장됨`; }
    toast('이미지를 보관함에 저장했습니다.');
  });
}
const drop = $('#drop-zone');
drop.onclick = () => {if (!busy) $('#files').click();}; drop.onkeydown = e => {if (['Enter',' '].includes(e.key)) {e.preventDefault(); drop.click();}};
$('#files').onchange = async e => {await upload([...e.target.files]); e.target.value = '';};
drop.ondragover = e => {e.preventDefault(); drop.classList.add('over');}; drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = e => {e.preventDefault(); drop.classList.remove('over'); upload([...e.dataTransfer.files]);};
dialog.addEventListener('paste', e => { const files = [...(e.clipboardData?.files || [])]; if (files.length) {e.preventDefault(); upload(files);} });
$('#delete').onclick = () => {if (current && confirm('콘텐츠와 연결된 이미지를 삭제할까요? 되돌릴 수 없습니다.')) task(async () => {await api(`/api/posts/${current.id}/delete`, {}); dirty = false; dialog.close(); await refresh(); toast('삭제했습니다.');});};
$('#rename').onclick = () => {$('#account-form').elements.first.value = state.accounts[0]; $('#account-form').elements.second.value = state.accounts[1]; $('#account-dialog').showModal();};
$('#cancel-accounts').onclick = () => $('#account-dialog').close();
$('#account-form').onsubmit = async e => {e.preventDefault(); try {await api('/api/accounts', {accounts: [e.target.elements.first.value,e.target.elements.second.value]}); await refresh(); $('#account-dialog').close(); toast('계정 이름을 저장했습니다.');} catch(error) {toast(error.message);} };
window.addEventListener('beforeunload', e => {if (dirty || busy) {e.preventDefault(); e.returnValue = '';}});
refresh().catch(error => {$('#contents').append(element('div', 'empty error', '서버에 연결하지 못했습니다. python3 server.py로 실행한 뒤 새로고침해 주세요.')); toast(error.message);});

let syncing = false;
function renderDrive() {
  const d = state.drive;
  if (!d) return;
  $('#drive-summary').textContent = d.connected ? ('구글 계정 연결됨 · ' + (d.last_sync ? '최근 동기화 ' + new Date(d.last_sync).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'}) : '아직 가져온 기록이 없습니다.')) : (d.configured ? '구글 계정을 연결하고 동기화하세요.' : '폴더 링크 저장됨 · 최초 구글 연결 설정이 필요합니다.');
  $('#drive-sync').disabled = syncing || !d.connected;
  $('#drive-connect').disabled = !d.configured || syncing;
  $('#drive-disconnect').disabled = !d.connected || syncing;
  $('#drive-status-text').textContent = d.connected ? '연결됨 · 읽기 전용 가져오기' : (d.configured ? '구글 로그인이 필요합니다.' : '아직 구글 OAuth 클라이언트가 설정되지 않았습니다. 아래 연결 안내를 확인해 주세요.');
  $('#drive-callback').textContent = d.redirect_uri;
  for (let i=0;i<2;i++) $('#drive-account').options[i].textContent = state.accounts[i];
}
function openDrive() {
  const d = state.drive; if (!d) return toast('연결 상태를 먼저 확인해 주세요.');
  $('#drive-folder').value = 'https://drive.google.com/drive/folders/' + d.folder;
  $('#drive-account').value = d.account;
  renderDrive(); $('#drive-dialog').showModal();
}
$('#drive-open').onclick = openDrive; $('#drive-settings').onclick = openDrive;
$('#drive-close').onclick = () => $('#drive-dialog').close();
$('#drive-form').onsubmit = async e => {e.preventDefault(); try {
  await api('/api/drive/config', {folder:$('#drive-folder').value, account:Number($('#drive-account').value)});
  await refresh(); toast('드라이브 폴더 설정을 저장했습니다.');
} catch(error) {toast(error.message);} };
$('#drive-connect').onclick = async () => {try {
  const result = await api('/api/drive/connect', {}); window.location.assign(result.url);
} catch(error) {toast(error.message);} };
$('#drive-disconnect').onclick = async () => {
  if (!confirm('서버의 구글 연결 정보를 지울까요? 가져온 콘텐츠와 드라이브 원본은 유지됩니다. 구글 계정 보안 설정에서 앱 권한도 철회할 수 있습니다.')) return;
  try { await api('/api/drive/disconnect', {}); await refresh(); toast('연결을 해제했습니다.'); } catch(error) {toast(error.message);}
};
$('#drive-sync').onclick = async () => {
  if (syncing) return; syncing = true; renderDrive(); $('#drive-summary').textContent = '문서와 이미지를 가져오는 중…';
  try { const r = await api('/api/drive/sync', {}); await refresh(); toast(`새 콘텐츠 ${r.created} · 업데이트 ${r.updated} · 변경 없음 ${r.unchanged}` + (r.conflicts.length ? ` · 로컬 편집 충돌 ${r.conflicts.length}건 (원문 확인)` : '') + (r.groups === 0 ? ' · 지원되는 파일이 없습니다.' : '')); }
  catch(error) {await refresh().catch(()=>{}); toast(error.message);}
  finally {syncing = false; renderDrive();}
};
