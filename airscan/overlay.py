"""置顶二维码悬浮窗页面。"""

OVERLAY_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AirScan-QR 悬浮广播</title>
  <style>
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: #000;
      color: #e2e8f0;
      font: 13px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      overflow: hidden;
    }
    .shell { display: grid; grid-template-rows: minmax(0, 1fr) auto; height: 100vh; }
    /* 舞台铺满可用区, 纯黑底; 二维码尺寸由 JS 按短边算成正方形, flex 居中。 */
    .stage {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      min-width: 0;
      min-height: 0;
      background: #000;
      overflow: hidden;
    }
    /* 尺寸由 JS 设定 (正方形, 短边撑满); 不用 object-fit, 避开 WebView2 横向失效。 */
    #qr { display: block; image-rendering: pixelated; }
    .placeholder { color: #94a3b8; padding: 16px; text-align: center; }
    .status {
      padding: 4px 10px;
      color: #cbd5e1;
      background: #000;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="stage" id="stage" aria-label="正在广播的二维码">
      <div class="placeholder" id="placeholder">等待广播二维码</div>
      <img id="qr" alt="正在广播的二维码" style="display:none">
    </section>
    <div class="status" id="status" aria-live="polite">就绪</div>
  </main>
  <script>
    function sizeQR() {
      const stage = document.getElementById('stage');
      const qr = document.getElementById('qr');
      if (!stage || !qr) return;
      const s = Math.min(stage.clientWidth, stage.clientHeight);
      qr.style.width = s + 'px';
      qr.style.height = s + 'px';
    }
    function pushOverlayQR(dataurl, status) {
      const qr = document.getElementById('qr');
      document.getElementById('placeholder').style.display = 'none';
      qr.style.display = 'block';
      qr.src = dataurl;
      document.getElementById('status').innerText = status;
      sizeQR();
    }
    function onOverlayPaused(message) {
      document.getElementById('status').innerText = message;
    }
    window.addEventListener('resize', sizeQR);
    window.addEventListener('load', sizeQR);
  </script>
</body>
</html>
"""
