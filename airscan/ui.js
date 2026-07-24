const GRID_FPS_DEFAULTS = { 1: 8, 2: 5, 3: 3 };
const HISTORY_ITEM_LIMIT = 5;
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
  const tabs = ['send', 'recv', 'sync'];
  tabs.forEach(name => {
    const active = name === tab;
    document.getElementById('tab-' + name).classList.toggle('active', active);
    document.getElementById('panel-' + name).classList.toggle('show', active);
  });
  if (tab === 'recv') {
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
function updateSendStatus(status) {
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
function prependHistoryItem(list, item) {
  list.insertBefore(item, list.firstChild);
  while (list.childElementCount > HISTORY_ITEM_LIMIT) {
    list.lastElementChild.remove();
  }
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
  prependHistoryItem(list, item);
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
    await api('copy_text', body.innerText);
    button.innerText = '完成';
    setTimeout(() => { button.innerText = '复制'; }, 1200);
  };
  item.append(body, button);
  const list = document.getElementById('msgList');
  prependHistoryItem(list, item);
}
/* --- 文件夹同步 --- */
window._syncFolders = { cloudBroadcast: null, applied: null, applyTarget: null, source: null };
const SYNC_FOLDER_LABELS = {
  cloudBroadcast: 'syncCloudFolder',
  applied: 'syncAppliedFolder',
  applyTarget: 'syncApplyTargetFolder',
  source: 'syncSourceFolder',
};
async function syncPickFolder(target) {
  const folder = await api('sync_pick_folder');
  if (!folder) return;
  window._syncFolders[target] = folder;
  const label = document.getElementById(SYNC_FOLDER_LABELS[target]);
  if (label) {
    label.innerText = folder;
    label.classList.add('set');
  }
  if (target === 'cloudBroadcast') {
    document.getElementById('btnSyncBroadcast').disabled = false;
  } else if (target === 'source') {
    document.getElementById('btnSyncDiff').disabled = false;
  } else if (target === 'applied' || target === 'applyTarget') {
    document.getElementById('btnSyncApply').disabled =
      !(window._syncFolders.applied && window._syncFolders.applyTarget);
  }
}
async function syncBroadcast() {
  const folder = window._syncFolders.cloudBroadcast;
  if (!folder) { toast('请先选择目标文件夹'); return; }
  const result = await api('sync_broadcast_manifest', folder);
  if (result && result.error) { toast(result.error); return; }
  document.getElementById('syncBroadcastStatus').innerText =
    `正在广播清单 · ${result.files} 个文件（${result.root}）`;
  document.getElementById('syncStatus').innerText =
    '清单广播中 · 让宿主机在“接收”页锁定本窗口收清单';
}
async function syncDiff() {
  const folder = window._syncFolders.source;
  if (!folder) { toast('请先选择本地源文件夹'); return; }
  const result = await api('sync_compute_diff', folder);
  if (result && result.error) { toast(result.error); return; }
  const box = document.getElementById('syncDiffResult');
  box.innerText =
    `新增/修改 ${result.send_count} 个 · 待删除 ${result.delete_count} 个`;
  box.classList.add('show');
  document.getElementById('btnSyncBuild').disabled =
    !(result.send_count || result.delete_count);
  document.getElementById('syncStatus').innerText = result.send_count || result.delete_count
    ? '已算出差异 · 点“生成并打开输出文件夹”'
    : '两端已一致，无需同步';
}
async function syncBuild() {
  const result = await api('sync_build_output');
  if (result && result.error) { toast(result.error); return; }
  document.getElementById('syncDiffResult').innerText =
    `已生成：${result.out_root}（复制 ${result.copied} 个文件，删除 ${result.delete_count} 个）`;
  document.getElementById('syncStatus').innerText =
    '输出文件夹已生成并打开 · 整份粘贴到云端后执行第 ④ 步';
}
async function syncApply() {
  const applied = window._syncFolders.applied;
  const target = window._syncFolders.applyTarget;
  if (!applied || !target) { toast('请选择粘贴进来的文件夹和目标文件夹'); return; }
  const result = await api('sync_apply', applied, target);
  if (result && result.error) { toast(result.error); return; }
  const status = document.getElementById('syncApplyStatus');
  if (result.ok_flag) {
    status.innerText =
      `同步完成，文件一致 · 应用 ${result.applied} 个 · 删除 ${result.deleted} 个 · 校正 ${result.corrected} 个时间戳`;
    document.getElementById('syncStatus').innerText = '同步完成，两端文件一致';
  } else {
    const detail = (result.mismatches || []).slice(0, 5)
      .map(m => `${m.path}(${m.reason})`).join('、');
    status.innerText =
      `已应用但仍有 ${result.mismatches.length} 处不一致：${detail}${result.mismatches.length > 5 ? ' …' : ''}`;
    document.getElementById('syncStatus').innerText = '同步后仍有差异，请检查上方列表';
  }
}
function onSyncManifest(rootName, count) {
  document.getElementById('syncManifestStatus').innerText =
    `已收到云端清单：${rootName} · ${count} 个文件`;
  toast('已收到云端清单');
}
function onSyncError(message) {
  toast(message);
  document.getElementById('syncStatus').innerText = message;
}
document.getElementById('inputText').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  startSend();
});
window.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeMissing();
});
