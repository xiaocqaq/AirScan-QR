const GRID_FPS_DEFAULTS = { 1: 8, 2: 5, 3: 3 };
window._fpsTouched = false;
window._sendPaused = false;
window._qrFocus = false;
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
/* --- 主题: 亮 / 暗 / 跟随系统 --- */
const THEME_KEY = 'airscan-theme';
const THEME_ORDER = ['system', 'light', 'dark'];
const THEME_META = {
  system: { icon: '🌓', label: '跟随系统' },
  light: { icon: '☀️', label: '亮色' },
  dark: { icon: '🌙', label: '暗色' },
};
function systemPrefersDark() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}
function applyTheme(mode) {
  const dark = mode === 'dark' || (mode === 'system' && systemPrefersDark());
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const btn = document.getElementById('themeToggle');
  if (btn) {
    btn.textContent = THEME_META[mode].icon;
    btn.title = '主题: ' + THEME_META[mode].label + '（点击切换）';
  }
}
function currentThemeMode() {
  const saved = localStorage.getItem(THEME_KEY);
  return THEME_ORDER.includes(saved) ? saved : 'system';
}
function cycleTheme() {
  const next = THEME_ORDER[(THEME_ORDER.indexOf(currentThemeMode()) + 1) % THEME_ORDER.length];
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
  toast('主题: ' + THEME_META[next].label);
}
function initTheme() {
  applyTheme(currentThemeMode());
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (currentThemeMode() === 'system') applyTheme('system');
    });
  }
}
initTheme();

function switchTab(tab) {
  const sending = tab === 'send';
  document.getElementById('tab-send').classList.toggle('active', sending);
  document.getElementById('tab-recv').classList.toggle('active', !sending);
  document.getElementById('panel-send').classList.toggle('show', sending);
  document.getElementById('panel-recv').classList.toggle('show', !sending);
  if (!sending) {
    setQrFocus(false);
    refreshWindows();
    loadDownloadDir();
  }
}
function setQrFocus(enabled) {
  window._qrFocus = enabled;
  document.body.classList.toggle('qr-focus', enabled);
  const button = document.getElementById('btnQrFocus');
  const label = enabled ? '显示控制区' : '隐藏控制区';
  button.setAttribute('aria-pressed', String(enabled));
  button.setAttribute('aria-label', label);
  button.title = label;
  button.innerText = enabled ? '▼' : '▲';
}
function toggleQrFocus() { setQrFocus(!window._qrFocus); }
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
  if (result && result.grid) document.getElementById('gridSel').value = result.grid;
  document.getElementById('inputText').value = '';
  window._sending = true;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = true;
  document.getElementById('btnSend').innerText = '开始广播';
  document.getElementById('btnPauseSend').disabled = false;
  document.getElementById('btnResend').disabled = true;
  document.getElementById('btnResumeAll').disabled = true;
  document.getElementById('sendStatus').innerText = '正在处理...';
  document.getElementById('qrStage').innerHTML = '<span class="placeholder">正在处理，请稍候...</span>';
}
function onSendReady(total, startIndex) {
  const input = document.getElementById('startIndex');
  input.max = total;
  input.value = startIndex;
  document.getElementById('btnResend').disabled = false;
  document.getElementById('btnResumeAll').disabled = true;
}
function onSendAutoStopped(cycles) {
  // 默认展示达到阈值 (max 5遍/30s) 后自动暂停 (非停止): 保留任务与当前位置,
  // 点“继续广播”从暂停处接着循环, 接收端漏帧仍可补收。
  window._sending = false;
  window._sendPaused = true;
  document.getElementById('btnSend').disabled = false;
  document.getElementById('btnSend').innerText = '继续广播';
  document.getElementById('btnPauseSend').disabled = true;
  document.getElementById('sendStatus').innerText =
     '已播 ' + cycles + ' 遍, 自动暂停 · 点“继续广播”可继续播放';
}
function onClipboardSendStarted() {
  window._sending = true;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = true;
  document.getElementById('btnSend').innerText = '开始广播';
  document.getElementById('btnPauseSend').disabled = false;
  document.getElementById('sendStatus').innerText = '检测到新剪贴板文本，重新广播中...';
}
function onSendError(message) {
  toast(message);
  window._sending = false;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = false;
  document.getElementById('btnPauseSend').disabled = true;
  document.getElementById('btnResend').disabled = true;
  document.getElementById('btnResumeAll').disabled = true;
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
async function applyResend(spec) {
  const startIndex = Math.max(1, +document.getElementById('startIndex').value || 1);
  const result = await api('resume_send', startIndex, spec);
  if (result && result.error) {
    toast(result.error);
    return;
  }
  window._sending = true;
  window._sendPaused = false;
  document.getElementById('btnSend').disabled = true;
  document.getElementById('btnPauseSend').disabled = false;
  document.getElementById('btnResumeAll').disabled = !result.selection_count;
  document.getElementById('sendStatus').innerText = result.selection_count
    ? `补发模式 · 循环发送 ${result.selection_count} 个缺失帧`
    : '已恢复全部帧顺序广播';
}
async function startResend() {
  const spec = document.getElementById('resendSpec').value.trim();
  if (!spec) {
    toast('请粘贴缺失序号');
    return;
  }
  await applyResend(spec);
}
async function resumeAllFrames() { await applyResend(''); }
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
function onComplete(ok, isText, info, path, filename) {
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
  if (path) addFile(path, filename);
}
function addFile(path, filename) {
  document.getElementById('fileSection').style.display = 'block';
  const item = document.createElement('div');
  item.className = 'msg-item file-item';
  item.title = '点击用默认应用打开：' + path;
  const body = document.createElement('div');
  body.className = 'msg-text file-name';
  body.innerText = filename || path;
  const openBtn = document.createElement('button');
  openBtn.className = 'msg-copy';
  openBtn.title = '打开';
  openBtn.innerText = '打开';
  const open = async () => {
    const result = await api('open_file', path);
    if (result && result.error) toast(result.error);
  };
  openBtn.onclick = open;
  body.onclick = open;
  item.append(body, openBtn);
  const list = document.getElementById('fileList');
  list.insertBefore(item, list.firstChild);
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
