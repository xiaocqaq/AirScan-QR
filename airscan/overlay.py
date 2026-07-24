"""置顶二维码悬浮窗页面。"""

OVERLAY_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AirScan-QR 悬浮广播</title>
  <style>
    :root { color-scheme: light dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: #f8fafc;
      color: #0f172a;
      font: 13px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      overflow: hidden;
    }
    .shell { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-height: 100vh; gap: 8px; padding: 8px; }
    .bar { display: flex; align-items: center; gap: 8px; min-height: 32px; }
    .title { font-weight: 700; flex: 1; }
    button {
      min-width: 44px;
      min-height: 32px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #ffffff;
      color: #0f172a;
      cursor: pointer;
    }
    button:hover { background: #f1f5f9; }
    .stage {
      display: grid;
      place-items: center;
      min-height: 0;
      border: 1px solid #cbd5e1;
      border-radius: 12px;
      background: #ffffff;
      overflow: hidden;
    }
    img { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; }
    .placeholder { color: #64748b; padding: 16px; text-align: center; }
    .status { min-height: 34px; color: #334155; overflow: hidden; text-overflow: ellipsis; }
    @media (prefers-color-scheme: dark) {
      body { background: #020617; color: #e2e8f0; }
      button { background: #0f172a; border-color: #334155; color: #e2e8f0; }
      button:hover { background: #1e293b; }
      .stage { background: #ffffff; border-color: #475569; }
      .status, .placeholder { color: #cbd5e1; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="bar">
      <div class="title">1×1 悬浮广播</div>
      <button type="button" onclick="closeOverlay()" aria-label="关闭悬浮窗">关闭</button>
    </div>
    <section class="stage" aria-label="正在广播的二维码">
      <div class="placeholder" id="placeholder">等待广播二维码</div>
      <img id="qr" alt="正在广播的二维码" style="display:none">
    </section>
    <div class="status" id="status" aria-live="polite">就绪</div>
  </main>
  <script>
    function pushOverlayQR(dataurl, status) {
      const qr = document.getElementById('qr');
      document.getElementById('placeholder').style.display = 'none';
      qr.style.display = 'block';
      qr.src = dataurl;
      document.getElementById('status').innerText = status;
    }
    function onOverlayPaused(message) {
      document.getElementById('status').innerText = message;
    }
    async function closeOverlay() {
      await window.pywebview.api.close_overlay();
    }
  </script>
</body>
</html>
"""
