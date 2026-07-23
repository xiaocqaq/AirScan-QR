(function (global) {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const state = {
    active: false,
    paused: false,
    task: null,
    decodedFrames: 0,
    rateStartedAt: 0,
    receiverCore: null,
    completed: null,
  };
  let captureController = null;
  let initialization = null;

  function setStatus(message, level) {
    byId('status').textContent = message;
    const dot = byId('statusDot');
    dot.classList.toggle('live', level === 'live');
    dot.classList.toggle('error', level === 'error');
  }

  function updateMode() {
    const value = byId('gridSelect').value;
    const labels = { 1: '1×1 · 单区扫描', 2: '2×2 · 并行扫描 4 区', 3: '3×3 · 并行扫描 9 区' };
    byId('modeLabel').textContent = labels[value];
  }

  function setPaused(paused) {
    state.paused = paused;
    byId('pauseButton').textContent = paused ? '继续扫描' : '暂停扫描';
    setStatus(paused ? '已暂停 · 任务仍保留' : '扫描中 · 等待二维码', paused ? 'idle' : 'live');
  }

  function updateProgress(progress) {
    byId('progress').textContent = `${progress.received} / ${progress.total}`;
    byId('missingCount').textContent = `缺失 ${progress.missingCount} 帧`;
    const percent = progress.total ? progress.received / progress.total * 100 : 0;
    byId('progressFill').style.width = `${percent}%`;
  }

  function sameTask(left, right) {
    if (!left || !right || left.tid.length !== right.tid.length) return false;
    return left.tid.every((byte, index) => byte === right.tid[index]);
  }

  function clearCompletedDownload() {
    state.completed = null;
    byId('downloadButton').disabled = true;
  }

  function onMeta(task) {
    const isNewTask = !sameTask(state.task, task);
    if (isNewTask) clearCompletedDownload();
    state.task = task;
    byId('taskName').textContent = task.name;
    byId('missingButton').disabled = false;
    setStatus(`已识别任务 · ${task.name}`, 'live');
  }

  function onReceiverStatus(status) {
    if (status === 'validating') setStatus('帧已收齐 · 正在校验 SHA-1', 'live');
  }

  function onComplete(result) {
    if (!result.ok) {
      state.completed = null;
      byId('downloadButton').disabled = true;
      setStatus(`${result.error} · 请继续扫描`, 'error');
      return;
    }
    state.completed = result;
    byId('downloadButton').disabled = false;
    setStatus(`接收完成 · ${result.name} · ${(result.size / 1024).toFixed(1)} KB`, 'live');
    downloadCurrent();
  }

  async function initializeReceiver() {
    try {
      const store = await global.AirScan.store.openStore();
      state.receiverCore = new global.AirScan.receiver.ReceiverCore({
        store,
        onMeta,
        onProgress: updateProgress,
        onStatus: onReceiverStatus,
        onComplete,
      });
      const restored = await state.receiverCore.restoreLatest();
      if (restored) setStatus(`已恢复任务 · ${restored.name}`, 'live');
    } catch (error) {
      setStatus(`本地存储不可用 · ${error.message || error}`, 'error');
      throw error;
    }
  }

  function drawVideoFrame(video) {
    const canvas = byId('scanCanvas');
    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
    }
    canvas.getContext('2d', { willReadFrequently: true }).drawImage(video, 0, 0);
    return canvas;
  }

  function updateScanRate() {
    const elapsed = (performance.now() - state.rateStartedAt) / 1000;
    if (elapsed < 1) return;
    byId('scanRate').textContent = `${(state.decodedFrames / elapsed).toFixed(1)} FPS`;
  }

  async function processFrame(video) {
    const canvas = drawVideoFrame(video);
    const grid = Number(byId('gridSelect').value);
    const payloads = await global.AirScan.decoder.decodeFrame(canvas, grid);
    state.decodedFrames += 1;
    updateScanRate();
    byId('scanBadge').textContent = payloads.length ? `识别 ${payloads.length} 个二维码` : '等待识别';
    for (const payload of payloads) await handlePayload(payload);
  }

  async function handlePayload(payload) {
    if (state.receiverCore) await state.receiverCore.acceptFrame(payload);
  }

  function onCaptureEnded() {
    state.active = false;
    state.paused = false;
    byId('shareButton').disabled = false;
    byId('shareButton').textContent = '重新选择共享窗口';
    byId('pauseButton').disabled = true;
    setStatus('共享已结束 · 已接收任务仍保留', 'error');
  }

  function onCaptureError(error) {
    captureController.pause();
    if (state.receiverCore) state.receiverCore.pause();
    state.paused = true;
    byId('pauseButton').textContent = '继续扫描';
    setStatus(`扫描失败 · ${error.message || error}`, 'error');
  }

  async function startSharing() {
    const shareButton = byId('shareButton');
    shareButton.disabled = true;
    setStatus('正在加载解码器并请求窗口权限', 'idle');
    try {
      await initialization;
      await global.AirScan.decoder.loadDecoder();
      await captureController.start();
      state.active = true;
      state.paused = false;
      state.decodedFrames = 0;
      state.rateStartedAt = performance.now();
      byId('stageEmpty').hidden = true;
      byId('scanBadge').hidden = false;
      byId('pauseButton').disabled = false;
      shareButton.disabled = false;
      shareButton.textContent = '更换共享窗口';
      setStatus('扫描中 · 等待二维码', 'live');
    } catch (error) {
      shareButton.disabled = false;
      setStatus(`无法开始共享 · ${error.message || error}`, 'error');
    }
  }

  function pauseScanning() {
    if (!captureController || !captureController.isActive()) return;
    captureController.pause();
    if (state.receiverCore) state.receiverCore.pause();
    setPaused(true);
  }

  function resumeScanning() {
    if (!captureController || !captureController.isActive()) return;
    captureController.resume();
    if (state.receiverCore) state.receiverCore.resume();
    setPaused(false);
  }

  function showMissing() {
    if (!state.receiverCore || !state.task) return;
    const missing = state.receiverCore.missing();
    byId('missingMeta').textContent = `${state.task.name} · ${missing.length} 帧待补发`;
    byId('missingRanges').textContent = global.AirScan.protocol.formatMissing(missing);
    byId('missingDialog').showModal();
  }

  function downloadCurrent() {
    if (!state.completed || !state.completed.blob) {
      setStatus('暂无可下载文件', 'error');
      return;
    }
    const link = document.createElement('a');
    const safeName = state.completed.name.replace(/[\\/:*?"<>|]/g, '_') || 'airscan-download';
    const url = URL.createObjectURL(state.completed.blob);
    link.href = url;
    link.download = safeName;
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
    global.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function copyMissing() {
    const text = byId('missingRanges').textContent;
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const input = document.createElement('textarea');
      input.value = text;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      input.remove();
    }
    setStatus('缺失序号已复制', 'live');
  }

  function init() {
    initialization = initializeReceiver();
    initialization.catch(() => {});
    captureController = global.AirScan.capture.createCapture(byId('shareVideo'), {
      onFrame: processFrame,
      onEnded: onCaptureEnded,
      onError: onCaptureError,
      intervalMs: 80,
    });
    byId('shareButton').addEventListener('click', startSharing);
    byId('pauseButton').addEventListener('click', () => {
      if (state.paused) resumeScanning(); else pauseScanning();
    });
    byId('missingButton').addEventListener('click', showMissing);
    byId('downloadButton').addEventListener('click', downloadCurrent);
    byId('gridSelect').addEventListener('change', updateMode);
    byId('closeDialog').addEventListener('click', () => byId('missingDialog').close());
    byId('dialogDone').addEventListener('click', () => byId('missingDialog').close());
    byId('copyMissing').addEventListener('click', copyMissing);
    updateMode();
  }

  const api = { startSharing, pauseScanning, resumeScanning, showMissing, downloadCurrent };
  global.airscanReceiver = api;
  global.addEventListener('DOMContentLoaded', init, { once: true });
}(window));
