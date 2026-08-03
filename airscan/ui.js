const GRID_FPS_DEFAULTS = { 1: 8, 2: 5, 3: 3 };
const HISTORY_ITEM_LIMIT = 5;
window._fpsTouched = false;
window._sendPaused = false;

function api(name, ...args) {
  return window.pywebview.api[name](...args);
}

/* --- 接收用时计时 (累计式, 支持暂停/继续, 完成后冻结) --- */
const recvTimer = { elapsedMs: 0, runSince: null, ticker: null };
function formatDuration(totalMs) {
  const totalSec = Math.floor(Math.max(0, totalMs) / 1000);
  const mm = String(Math.floor(totalSec / 60)).padStart(2, '0');
  const ss = String(totalSec % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}
function recvTimerValueMs() {
  return recvTimer.elapsedMs + (recvTimer.runSince ? Date.now() - recvTimer.runSince : 0);
}
function recvTimerText() {
  return formatDuration(recvTimerValueMs());
}
function renderRecvTimer() {
  const el = document.getElementById('recvTimer');
  if (!el) return;
  el.innerText = recvTimerText();
  el.classList.toggle('running', recvTimer.runSince !== null);
}
function startRecvTimer(reset) {
  if (reset) recvTimer.elapsedMs = 0;
  if (recvTimer.runSince === null) recvTimer.runSince = Date.now();
  renderRecvTimer();
  if (!recvTimer.ticker) recvTimer.ticker = setInterval(renderRecvTimer, 500);
}
function pauseRecvTimer() {
  if (recvTimer.runSince !== null) {
    recvTimer.elapsedMs += Date.now() - recvTimer.runSince;
    recvTimer.runSince = null;
  }
  if (recvTimer.ticker) { clearInterval(recvTimer.ticker); recvTimer.ticker = null; }
  renderRecvTimer();
}
function stopRecvTimer() {
  pauseRecvTimer();  // 冻结在最终用时
}
function resetRecvTimer() {
  recvTimer.elapsedMs = 0;
  recvTimer.runSince = null;
  if (recvTimer.ticker) { clearInterval(recvTimer.ticker); recvTimer.ticker = null; }
  renderRecvTimer();
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
  const tabs = ['send', 'recv', 'sync', 'tunnel'];
  tabs.forEach(name => {
    const active = name === tab;
    document.getElementById('tab-' + name).classList.toggle('active', active);
    document.getElementById('panel-' + name).classList.toggle('show', active);
  });
  if (tab === 'recv') {
    refreshWindows();
    loadDownloadDir();
  } else if (tab === 'tunnel') {
    refreshGitWindows();
    refreshGitTunnelStatus();
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
async function onClipboardNeedsConfirm(preview, seq) {
  if (window._confirmResolve) closeConfirm(false);
  const ok = await confirmDialog(
    '检测到新复制的文本：\n' + preview
    + '\n\n当前正在广播文件。要改为广播这段文本吗？'
    + '\n（取消则继续广播文件，不会中断）');
  const result = ok ? await api('confirm_clipboard_send', seq)
                    : await api('discard_clipboard_send', seq);
  if (!ok && !(result && result.stale)) {
    toast('已忽略剪贴板文本，继续广播文件');
  }
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
  // 继续接收时恢复计时 (若已有任务在计时); 新任务的计时由 onMeta 重置启动。
  if (result.resumed) startRecvTimer(false);
}
async function pauseRecv() {
  await api('pause_recv');
  document.getElementById('btnRecv').disabled = false;
  document.getElementById('btnRecv').innerText = '继续接收';
  document.getElementById('btnPauseRecv').disabled = true;
  document.getElementById('recvStatus').innerText = '已暂停 · 当前进度已保留';
  pauseRecvTimer();
}
async function resetRecv() {
  if (!(await confirmDialog('确定清空当前接收进度吗？'))) return;
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
  resetRecvTimer();
}
function onMeta(name, total, isText) {
  document.getElementById('recvFile').innerText = isText ? '文本消息' : name;
  document.getElementById('progBig').innerText = `0/${total}`;
  document.getElementById('progBig').classList.remove('ok');
  document.getElementById('pbar').style.width = '0%';
  document.getElementById('btnMissing').disabled = false;
  document.getElementById('btnResetRecv').disabled = false;
  // 新任务开始接收: 计时清零并开始走表 (文件模式才显示用时统计)。
  if (!isText) startRecvTimer(true);
  else resetRecvTimer();
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
  stopRecvTimer();  // 冻结最终用时
  const info2 = info ? `${info} · 用时 ${recvTimerText()}` : info;
  document.getElementById('recvStatus').innerText = info2 || '已保存 · 等待下一次发送';
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
/* --- 应用内确认框 (替代原生 confirm, 避免 WebView2 带来的 "127.0.0.1 显示" 前缀) --- */
window._confirmResolve = null;
function confirmDialog(text) {
  document.getElementById('confirmText').innerText = text;
  document.getElementById('confirmModal').classList.add('show');
  document.getElementById('confirmOk').focus();
  return new Promise(resolve => { window._confirmResolve = resolve; });
}
function closeConfirm(ok) {
  document.getElementById('confirmModal').classList.remove('show');
  const resolve = window._confirmResolve;
  window._confirmResolve = null;
  if (resolve) resolve(!!ok);
}
function onConfirmBackdrop(event) {
  if (event.target.id === 'confirmModal') closeConfirm(false);
}
async function copyMissing() {
  await api('copy_text', document.getElementById('missingRanges').innerText);
  toast('缺失序号已复制');
}
const WINDOW_TARGET_KEYS = {
  recv: 'airscan-recv-window-target',
  git_host: 'airscan-git-window-target',
};
function loadWindowTarget(role) {
  try { return JSON.parse(localStorage.getItem(WINDOW_TARGET_KEYS[role]) || 'null'); }
  catch (_) { return null; }
}
function saveWindowTarget(role, target) {
  if (target) localStorage.setItem(WINDOW_TARGET_KEYS[role], JSON.stringify(target));
}
async function restoreWindowTarget(role, select) {
  const target = loadWindowTarget(role);
  if (!target) return false;
  const result = await api('restore_window_target', role, target);
  if (!result || !result.ok) return false;
  select.value = String(result.hwnd);
  return true;
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
  await restoreWindowTarget('recv', select);
  document.getElementById('btnRecv').disabled = !select.value;
}
async function onWinPick() {
  const hwnd = document.getElementById('winSel').value;
  document.getElementById('btnRecv').disabled = !hwnd;
  if (hwnd) {
    const result = await api('set_window', +hwnd, 'recv');
    saveWindowTarget('recv', result && result.target);
  }
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
/* --- 文件夹同步 (基于 git 变动) --- */
window._syncFolders = { cloudTarget: null, applied: null, source: null };
const SYNC_FOLDER_LABELS = {
  cloudTarget: 'syncCloudFolder',
  applied: 'syncAppliedFolder',
  source: 'syncSourceFolder',
};
function refreshSyncApplyEnabled() {
  document.getElementById('btnSyncApply').disabled =
    !(window._syncFolders.applied && window._syncFolders.cloudTarget);
}
async function syncPickFolder(target) {
  const folder = await api('sync_pick_folder');
  if (!folder) return;
  window._syncFolders[target] = folder;
  const label = document.getElementById(SYNC_FOLDER_LABELS[target]);
  if (label) {
    label.innerText = folder;
    label.classList.add('set');
  }
  if (target === 'cloudTarget') {
    refreshSyncApplyEnabled();
  } else if (target === 'source') {
    document.getElementById('btnSyncBuild').disabled = false;
  } else if (target === 'applied') {
    refreshSyncApplyEnabled();
  }
}
function setAppliedFolder(folder) {
  window._syncFolders.applied = folder;
  const label = document.getElementById('syncAppliedFolder');
  if (label) {
    label.innerText = folder;
    label.classList.add('set');
  }
  refreshSyncApplyEnabled();
}
// 由 Python 侧 drop 处理器回调 (拿到真实磁盘路径后)。
function onSyncFolderDropped(folder) {
  const zone = document.getElementById('syncDropZone');
  if (zone) zone.classList.remove('drag-over');
  if (!folder) { toast('未能识别拖入的文件夹，请改用手动选择'); return; }
  setAppliedFolder(folder);
  document.getElementById('syncStatus').innerText = '已拖入输出文件夹 · 点“应用同步”';
}
// dragenter/dragover 必须阻止默认, 否则 WebView2 会把文件夹当导航打开; 兼带高亮。
(function initSyncDropZone() {
  const zone = document.getElementById('syncDropZone');
  if (!zone) return;
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  zone.addEventListener('dragenter', (e) => { stop(e); zone.classList.add('drag-over'); });
  zone.addEventListener('dragover', (e) => { stop(e); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', (e) => { stop(e); zone.classList.remove('drag-over'); });
  // drop 的真实路径由 pywebview 的 Python 处理器负责; 这里仅收尾高亮。
  zone.addEventListener('drop', (e) => { stop(e); });
})();
// 宿主机(源): 用 git 变动一键生成输出文件夹。
async function syncBuild() {
  const folder = window._syncFolders.source;
  if (!folder) { toast('请先选择本地 git 仓库'); return; }
  const btn = document.getElementById('btnSyncBuild');
  btn.disabled = true;
  const result = await api('sync_git_build', folder);
  btn.disabled = false;
  if (result && result.error) {
    toast(result.error);
    document.getElementById('syncStatus').innerText = result.error;
    return;
  }
  const box = document.getElementById('syncDiffResult');
  box.innerText =
    `已生成并打开：${result.out_root}\n变动 ${result.copied} 个 · 删除 ${result.delete_count} 个`;
  box.classList.add('show');
  document.getElementById('syncStatus').innerText =
    '输出文件夹已打开 · 整份拖到左侧“云端”栏或粘贴到云端后应用同步';
}
// 云端(目标): 应用粘贴/拖入的输出文件夹。
async function syncApply() {
  const applied = window._syncFolders.applied;
  const target = window._syncFolders.cloudTarget;
  if (!applied || !target) { toast('请先选择目标仓库并拖入/选择输出文件夹'); return; }
  const result = await api('sync_apply', applied, target);
  if (result && result.error) { toast(result.error); return; }
  const status = document.getElementById('syncApplyStatus');
  if (result.ok_flag) {
    status.innerText =
      `同步完成 · 应用 ${result.applied} 个 · 删除 ${result.deleted} 个 · 校正 ${result.corrected} 个时间戳`;
    document.getElementById('syncStatus').innerText = '同步完成，变动文件已落到目标仓库';
  } else {
    status.innerText = `已应用但仍有 ${(result.mismatches || []).length} 处不一致`;
    document.getElementById('syncStatus').innerText = '同步后仍有差异，请检查同步结果';
  }
  showSyncResult(result, target);
}

const SYNC_MISMATCH_REASONS = {
  missing: '缺失',
  extra: '多余',
  size: '大小不符',
  mtime: '时间戳不符',
};
function showSyncResult(result, target) {
  const ok = !!result.ok_flag;
  const mismatches = result.mismatches || [];
  const banner = document.getElementById('syncResultBanner');
  banner.innerText = ok ? '同步完成 · 已与目标仓库一致'
    : `同步已应用，但仍有 ${mismatches.length} 处不一致`;
  banner.className = 'sync-result-banner ' + (ok ? 'ok' : 'warn');
  document.getElementById('syncResultStats').innerText =
    [`应用 ${result.applied} 个文件`,
     `移入备份 ${result.deleted} 个`,
     `校正 ${result.corrected} 个时间戳`,
     `目标目录：${target}`].join('\n');
  const detail = document.getElementById('syncResultDetail');
  if (ok) {
    detail.style.display = 'none';
    detail.innerText = '';
  } else {
    detail.style.display = 'block';
    detail.innerText = mismatches
      .map(m => `${SYNC_MISMATCH_REASONS[m.reason] || m.reason}  ${m.path}`)
      .join('\n');
  }
  window._syncResultTarget = target;
  document.getElementById('syncResultModal').classList.add('show');
  document.querySelector('#syncResultModal .modal-close').focus();
}
function closeSyncResult() {
  document.getElementById('syncResultModal').classList.remove('show');
}
function onSyncResultBackdrop(event) {
  if (event.target.id === 'syncResultModal') closeSyncResult();
}
async function openSyncTarget() {
  if (window._syncResultTarget) await api('open_file', window._syncResultTarget);
}
function onSyncError(message) {
  toast(message);
  document.getElementById('syncStatus').innerText = message;
}

/* --- Git 隧道 (IDEA fetch / pull / push) --- */
const GIT_BASE_KEY = 'airscan-git-base-url';
const GIT_CONTROLS = {
  host: {
    startId: 'btnGitHostStart', stopId: 'btnGitHostStop',
    start: '启动代理', running: '代理运行中', stop: '停止代理',
  },
  cloud: {
    startId: 'btnGitCloudStart', stopId: 'btnGitCloudStop',
    start: '启动转发', running: '转发运行中', stop: '停止转发',
  },
};
function gitStatus(message) {
  const el = document.getElementById('gitTunnelStatus');
  if (el) el.innerText = message;
}
function setGitControlState(scope, running, busyAction = '') {
  const control = GIT_CONTROLS[scope];
  const start = document.getElementById(control.startId);
  const stop = document.getElementById(control.stopId);
  const busy = Boolean(busyAction);
  start.disabled = busy || running;
  stop.disabled = busy || !running;
  start.innerText = busyAction === 'start' ? '启动中...' : (running ? control.running : control.start);
  stop.innerText = busyAction === 'stop' ? '停止中...' : control.stop;
}
async function runGitTunnelAction(scope, action, invoke, successMessage) {
  setGitControlState(scope, action === 'stop', action);
  try {
    const result = await invoke();
    if (result.error) {
      toast(result.error);
      gitStatus(result.error);
      return;
    }
    gitStatus(successMessage);
  } catch (error) {
    const message = 'Git 隧道操作失败: ' + (error && error.message ? error.message : error);
    toast(message);
    gitStatus(message);
  } finally {
    await refreshGitTunnelStatus();
  }
}
async function refreshGitWindows() {
  const select = document.getElementById('gitHostWindow');
  if (!select) return;
  const current = select.value;
  const wins = await api('list_windows');
  select.innerHTML = '<option value="">选择云桌面窗口...</option>';
  wins.forEach(w => {
    const option = document.createElement('option');
    option.value = w.hwnd;
    option.textContent = w.title + ' (' + w.w + '×' + w.h + ')';
    select.appendChild(option);
  });
  if (current) select.value = current;
  if (!select.value) await restoreWindowTarget('git_host', select);
}
async function onGitWindowPick() {
  const hwnd = document.getElementById('gitHostWindow').value;
  if (!hwnd) return;
  const result = await api('set_window', +hwnd, 'git_host');
  saveWindowTarget('git_host', result && result.target);
}
function initGitBaseUrl() {
  const input = document.getElementById('gitBaseUrl');
  if (!input) return;
  const saved = localStorage.getItem(GIT_BASE_KEY);
  if (saved) input.value = saved;
}
async function refreshGitTunnelStatus() {
  const result = await api('git_tunnel_status');
  const hostRunning = Boolean(result.host && result.host.running);
  const cloudRunning = Boolean(result.cloud && result.cloud.running);
  setGitControlState('host', hostRunning);
  setGitControlState('cloud', cloudRunning);
  const host = hostRunning ? '宿主机 ' + (result.host.listen || '运行中') : '宿主机未启动';
  const capacity = cloudRunning
    ? ` · 上限 ${result.cloud.max_pages} 页 / ${result.cloud.response_limit_mib} MiB`
    : '';
  const cloud = cloudRunning ? '云端 ' + (result.cloud.target || '运行中') + capacity : '云端未启动';
  gitStatus(host + ' · ' + cloud);
}
async function gitStartHost() {
  const hwnd = document.getElementById('gitHostWindow').value;
  if (!hwnd) { toast('请先选择云桌面窗口'); return; }
  await runGitTunnelAction('host', 'start',
    () => api('git_tunnel_start_host', hwnd),
    '宿主机代理已启动 · IDEA 使用 127.0.0.1:9999');
}
async function gitStopHost() {
  await runGitTunnelAction('host', 'stop',
    () => api('git_tunnel_stop_host'), '宿主机代理已停止');
}
async function gitStartCloud() {
  const input = document.getElementById('gitBaseUrl');
  const baseUrl = input.value.trim();
  localStorage.setItem(GIT_BASE_KEY, baseUrl);
  await runGitTunnelAction('cloud', 'start',
    () => api('git_tunnel_start_cloud', baseUrl),
    '云端转发已启动 · 等待 IDEA 请求');
}
async function gitStopCloud() {
  await runGitTunnelAction('cloud', 'stop',
    () => api('git_tunnel_stop_cloud'), '云端转发已停止');
}
function onGitTunnelStatus(message) {
  gitStatus(message);
}
initGitBaseUrl();
document.getElementById('inputText').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  startSend();
});
window.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  closeMissing();
  closeSyncResult();
  if (window._confirmResolve) closeConfirm(false);
});
