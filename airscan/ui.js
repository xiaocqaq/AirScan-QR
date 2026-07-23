const GRID_FPS_DEFAULTS = { 1: 8, 2: 5, 3: 3 };
window._fpsTouched = false;
window._sendPaused = false;

function api(name, ...args) {
  return window.pywebview.api[name](...args);
}

function toast(msg) {
  const element = document.getElementById('toast');
  element.innerText = msg;
  element.classList.add('show');
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => element.classList.remove('show'), 2600);
}

function switchTab(tab) {
  const sending = tab === 'send';
  document.getElementById('tab-send').classList.toggle('active', sending);
  document.getElementById('tab-recv').classList.toggle('active', !sending);
  document.getElementById('panel-send').classList.toggle('show', sending);
  document.getElementById('panel-recv').classList.toggle('show', !sending);
  if (!sending) {
    refreshWindows();
    loadDownloadDir();
  }
}

async function pickFile() {
  const name = await api('pick_file');
  if (!name) return;
  const label = document.getElementById('fileName');
  label.innerText = name;
  label.classList.add('set');
  document.getElementById('btnClearFile').style.display = 'inline-flex';
}

async function clearFile() {
  await api('clear_file');
  const label = document.getElementById('fileName');
  label.innerText = '未选择文件';
  label.classList.remove('set');
  document.getElementById('btnClearFile').style.display = 'none';
}

function onFpsInput(input) {
  window._fpsTouched = true;
  document.getElementById('fpsVal').innerText = input.value;
  if (window._sending) api('set_fps', +input.value);
}

function onGridChange() {
  if (window._fpsTouched) return;
  const grid = +document.getElementById('gridSel').value;
  const fps = GRID_FPS_DEFAULTS[grid];
  document.getElementById('fpsRange').value = fps;
  document.getElementById('fpsVal').innerText = fps;
}

async function startSend() {
  const text = document.getElementById('inputText').value;
  const grid = +document.getElementById('gridSel').value;
  const err = document.getElementById('errSel').value;
  const fps = +document.getElementById('fpsRange').value;
  const startIndex = Math.max(1, +document.getElementById('startIndex').value || 1);
  const result = await api('start_send', text, grid, err, fps, startIndex);
  if (result && result.error) {
    toast(result.error);
    return;
  }
  document.getElementById('inputText').value = '';
  window._sending = true;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = true;
  document.getElementById('btnSend').innerText = '开始广播';
  document.getElementById('btnPauseSend').disabled = false;
  document.getElementById('sendStatus').innerText = '正在处理...';
  document.getElementById('qrStage').innerHTML = '<span class="placeholder">正在处理，请稍候...</span>';
}

function onSendReady(total, startIndex) {
  const input = document.getElementById('startIndex');
  input.max = total;
  input.value = startIndex;
}

function onSendError(message) {
  toast(message);
  window._sending = false;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = false;
  document.getElementById('btnPauseSend').disabled = true;
  document.getElementById('sendStatus').innerText = '就绪';
  document.getElementById('qrStage').innerHTML = '<span class="placeholder">二维码将显示在这里</span>';
}

async function startOrResumeSend() {
  if (window._sendPaused) {
    await resumeSend();
    return;
  }
  await startSend();
}

async function pauseSend() {
  await api('pause_send');
  window._sending = false;
  window._sendPaused = true;
  document.getElementById('btnSend').disabled = false;
  document.getElementById('btnSend').innerText = '继续广播';
  document.getElementById('btnPauseSend').disabled = true;
  document.getElementById('sendStatus').innerText = '已暂停 · 可修改起始序号后继续';
}

async function resumeSend() {
  const startIndex = Math.max(1, +document.getElementById('startIndex').value || 1);
  const result = await api('resume_send', startIndex);
  if (result && result.error) {
    toast(result.error);
    return;
  }
  window._sending = true;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = true;
  document.getElementById('btnSend').innerText = '开始广播';
  document.getElementById('btnPauseSend').disabled = false;
  document.getElementById('startIndex').value = result.start_index;
}

function pushQR(dataurl, status) {
  const stage = document.getElementById('qrStage');
  let image = stage.querySelector('img');
  if (!image) {
    stage.innerHTML = '';
    image = document.createElement('img');
    image.alt = '正在广播的二维码';
    stage.appendChild(image);
  }
  image.src = dataurl;
  document.getElementById('sendStatus').innerText = status;
}

async function startRecv() {
  const result = await api('start_recv');
  if (result && result.error) {
    toast(result.error);
    return;
  }
  document.getElementById('btnRecv').disabled = true;
  document.getElementById('btnRecv').innerText = '继续接收';
  document.getElementById('btnPauseRecv').disabled = false;
  document.getElementById('recvStatus').innerText = result.resumed ? '继续接收中...' : '接收中...';
}

async function pauseRecv() {
  await api('pause_recv');
  document.getElementById('btnRecv').disabled = false;
  document.getElementById('btnRecv').innerText = '继续接收';
  document.getElementById('btnPauseRecv').disabled = true;
  document.getElementById('recvStatus').innerText = '已暂停 · 当前进度已保留';
}

async function resetRecv() {
  if (!window.confirm('确定清空当前接收进度吗？')) return;
  await api('reset_recv');
  document.getElementById('btnRecv').innerText = '开始接收';
  document.getElementById('btnRecv').disabled = !document.getElementById('winSel').value;
  document.getElementById('btnPauseRecv').disabled = true;
  document.getElementById('btnResetRecv').disabled = true;
  document.getElementById('btnMissing').disabled = true;
  document.getElementById('recvFile').innerText = '';
  document.getElementById('progBig').innerText = '-';
  document.getElementById('pbar').style.width = '0%';
  document.getElementById('recvStatus').innerText = '任务已重置';
}

function onMeta(name, total, isText) {
  document.getElementById('recvFile').innerText = isText ? '文本消息' : name;
  document.getElementById('progBig').innerText = `0/${total}`;
  document.getElementById('progBig').classList.remove('ok');
  document.getElementById('pbar').style.width = '0%';
  document.getElementById('btnMissing').disabled = false;
  document.getElementById('btnResetRecv').disabled = false;
}

function onProgress(got, total) {
  document.getElementById('progBig').innerText = `${got}/${total}`;
  document.getElementById('pbar').style.width = `${total ? got / total * 100 : 0}%`;
  document.getElementById('recvStatus').innerText = `接收中... ${got}/${total} · 缺 ${total - got}`;
}

function onComplete(ok, isText, info) {
  const progress = document.getElementById('progBig');
  if (!ok) {
    document.getElementById('recvStatus').innerText = info || '校验失败，等待重传...';
    return;
  }
  if (isText) {
    document.getElementById('recvStatus').innerText = '已接收文本并写入剪贴板 · 等待下一次发送';
    return;
  }
  progress.classList.add('ok');
  progress.innerText = '完成';
  document.getElementById('recvStatus').innerText = info || '已保存 · 等待下一次发送';
}

async function showMissing() {
  const summary = await api('get_missing');
  document.getElementById('missingMeta').innerText = summary.total
    ? `${summary.name} · 已收 ${summary.received}/${summary.total} · 剩余 ${summary.missing_count}`
    : '当前没有接收任务';
  document.getElementById('missingRanges').innerText = summary.ranges;
  document.getElementById('missingModal').classList.add('show');
  document.querySelector('#missingModal .modal-close').focus();
}

function closeMissing() {
  document.getElementById('missingModal').classList.remove('show');
}

function onMissingBackdrop(event) {
  if (event.target.id === 'missingModal') closeMissing();
}

async function copyMissing() {
  await api('copy_text', document.getElementById('missingRanges').innerText);
  toast('缺失序号已复制');
}

async function refreshWindows() {
  const windows = (await api('list_windows')) || [];
  const select = document.getElementById('winSel');
  select.innerHTML = '<option value="">选择要接收的窗口...</option>';
  windows.forEach(windowInfo => {
    const option = document.createElement('option');
    option.value = windowInfo.hwnd;
    option.text = `${windowInfo.title} (${windowInfo.w}×${windowInfo.h})`;
    select.appendChild(option);
  });
}

async function onWinPick() {
  const hwnd = document.getElementById('winSel').value;
  document.getElementById('btnRecv').disabled = !hwnd;
  if (hwnd) await api('set_window', +hwnd);
}

async function loadDownloadDir() {
  document.getElementById('downloadDir').innerText = await api('get_download_dir');
}

async function openDownloadDir() {
  await api('open_download_dir');
}

function addMessage(text) {
  document.getElementById('msgSection').style.display = 'block';
  const item = document.createElement('div');
  item.className = 'msg-item';
  const body = document.createElement('div');
  body.className = 'msg-text';
  body.innerText = text;
  const button = document.createElement('button');
  button.className = 'msg-copy';
  button.innerText = '复制';
  button.onclick = async () => {
    await api('copy_text', text);
    button.innerText = '完成';
    setTimeout(() => { button.innerText = '复制'; }, 1200);
  };
  item.append(body, button);
  const list = document.getElementById('msgList');
  list.insertBefore(item, list.firstChild);
}

document.getElementById('inputText').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  startSend();
});
window.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeMissing();
});
