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
  let messageFeed = null;
  const payloadQueue = [];
  let payloadPumping = false;
  let pendingProgress = null;
  let progressTimer = 0;
  let lastProgressPaint = 0;
  const PROGRESS_MIN_INTERVAL_MS = 120;

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

  function paintProgress(progress) {
    byId('progress').textContent = progress.received + ' / ' + progress.total;
    byId('missingCount').textContent = '缺失 ' + progress.missingCount + ' 帧';
    const percent = progress.total ? progress.received / progress.total * 100 : 0;
    byId('progressFill').style.width = percent + '%';
    lastProgressPaint = performance.now();
  }

  function updateProgress(progress) {
    pendingProgress = progress;
    const now = performance.now();
    // 文件传输时每帧刷 DOM 会卡 UI，节流到约 8 次/秒
    if (now - lastProgressPaint < PROGRESS_MIN_INTERVAL_MS) {
      if (!progressTimer) {
        progressTimer = global.setTimeout(() => {
          progressTimer = 0;
          if (pendingProgress) paintProgress(pendingProgress);
        }, PROGRESS_MIN_INTERVAL_MS);
      }
      return;
    }
    paintProgress(progress);
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

  async function onComplete(result) {
    if (!result.ok) {
      state.completed = null;
      byId('downloadButton').disabled = true;
      setStatus(`${result.error} · 请继续扫描`, 'error');
      return;
    }
    if (result.flags & global.AirScan.protocol.FLAG_TEXT) {
      clearCompletedDownload();
      const text = await result.blob.text();
      await messageFeed.addText(text, new Date(), { copyOnReceive: true });
      setStatus(`收到文字 · ${result.size} 字节`, 'live');
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

  function updateCaptureRes(video) {
    const node = byId('captureRes');
    if (!node) return;
    const width = video.videoWidth || 0;
    const height = video.videoHeight || 0;
    node.textContent = width && height ? ('捕获 ' + width + '×' + height) : '捕获 —';
  }

  function drawVideoFrame(video) {
    const canvas = byId('scanCanvas');
    // 关键：用 MediaStream 原始宽高，与页面 CSS / 预览尺寸无关
    const width = video.videoWidth || 0;
    const height = video.videoHeight || 0;
    if (!width || !height) return canvas;
    updateCaptureRes(video);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const context = canvas.getContext('2d', { willReadFrequently: true });
    // 关闭平滑，保留 QR 模块锐利边缘
    context.imageSmoothingEnabled = false;
    context.drawImage(video, 0, 0, width, height);
    return canvas;
  }

  function updateScanRate() {
    const elapsed = (performance.now() - state.rateStartedAt) / 1000;
    if (elapsed < 1) return;
    byId('scanRate').textContent = (state.decodedFrames / elapsed).toFixed(1) + ' FPS';
  }

  async function processFrame(video) {
    const canvas = drawVideoFrame(video);
    const grid = Number(byId('gridSelect').value);
    // 仅在此 await 解码；落盘走队列，避免 IndexedDB 阻塞下一帧扫描
    const payloads = await global.AirScan.decoder.decodeFrame(canvas, grid);
    state.decodedFrames += 1;
    updateScanRate();
    byId('scanBadge').textContent = payloads.length
      ? ('识别 ' + payloads.length + ' 个二维码')
      : '等待识别';
    if (payloads.length) {
      for (const payload of payloads) payloadQueue.push(payload);
      // 传输忙时略降采集频率，把 CPU 让给写盘与 UI
      if (payloadQueue.length > 8 && captureController) {
        captureController.intervalMs = 90;
      } else if (captureController) {
        captureController.intervalMs = 50;
      }
      pumpPayloadQueue();
    }
  }

  async function pumpPayloadQueue() {
    if (payloadPumping) return;
    payloadPumping = true;
    try {
      while (payloadQueue.length && state.active && !state.paused) {
        const payload = payloadQueue.shift();
        await handlePayload(payload);
        // 让出主线程，避免进度条/点击无响应
        await new Promise((resolve) => global.setTimeout(resolve, 0));
      }
    } finally {
      payloadPumping = false;
      if (payloadQueue.length && state.active && !state.paused) pumpPayloadQueue();
    }
  }

  async function handlePayload(payload) {
    if (state.receiverCore) await state.receiverCore.acceptFrame(payload);
  }

  function onCaptureEnded() {
    state.active = false;
    state.paused = false;
    payloadQueue.length = 0;
    byId('shareButton').disabled = false;
    byId('shareButton').textContent = '重新选择共享窗口';
    byId('pauseButton').disabled = true;
    const stageEmpty = byId('stageEmpty');
    if (stageEmpty) {
      stageEmpty.hidden = false;
      stageEmpty.textContent = '共享已结束。任务进度仍保留，可重新选择发送端窗口继续。';
    }
    byId('scanBadge').textContent = '共享已结束';
    const cap = byId('captureRes');
    if (cap) cap.textContent = '捕获 —';
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
      const stageEmpty = byId('stageEmpty');
      if (stageEmpty) {
        stageEmpty.textContent = '后台扫描中：解码使用共享流原始分辨率（见「捕获 W×H」），与页面布局无关。';
      }
      byId('pauseButton').disabled = false;
      shareButton.disabled = false;
      shareButton.textContent = '更换共享窗口';
      const video = byId('shareVideo');
      updateCaptureRes(video);
      const res = (video.videoWidth && video.videoHeight)
        ? (video.videoWidth + '×' + video.videoHeight)
        : '原始分辨率';
      setStatus('后台扫描中 · 捕获 ' + res + '（非页面预览）', 'live');
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
    const copied = await global.AirScan.messages.copyText(text);
    setStatus(copied ? '缺失序号已复制' : '复制失败 · 请手动选择序号', copied ? 'live' : 'error');
  }

  function addTextMessage(text, receivedAt) {
    return messageFeed.addText(text, receivedAt);
  }

  function init() {
    messageFeed = global.AirScan.messages.createMessageFeed({
      list: byId('messageList'),
      empty: byId('messageEmpty'),
      count: byId('messageCount'),
      onCopy: (copied) => setStatus(
        copied ? '文字已复制' : '复制失败 · 请手动选择文字', copied ? 'live' : 'error'),
    });
    initialization = initializeReceiver();
    initialization.catch(() => {});
    captureController = global.AirScan.capture.createCapture(byId('shareVideo'), {
      onFrame: processFrame,
      onEnded: onCaptureEnded,
      onError: onCaptureError,
      intervalMs: 60,
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

  const api = {
    startSharing, pauseScanning, resumeScanning, showMissing, downloadCurrent, addTextMessage,
  };
  global.airscanReceiver = api;
  global.addEventListener('DOMContentLoaded', init, { once: true });
}(window));
