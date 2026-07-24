// 线上验证：用 playwright 打开线上 receiver.html，在页面上下文里创建真实
// module worker（decode-worker.js 从线上加载），喂一张页面内生成的 QR，
// 验证线上特有风险：CDN(jsdelivr) 的 ESM+wasm 可达、无 CSP/WAF 拦截、MIME 正确。
import playwright from '../../build/browser-smoke/playwright/node_modules/playwright/index.js';

const { chromium } = playwright;
const CHROME = process.env.CHROME_BIN
  || 'C:/Users/xiao/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe';
const BASE = process.argv[2] || 'https://airscan.xlingo.fun';

async function main() {
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage();
  const logs = [];
  page.on('console', (msg) => logs.push(`[console] ${msg.text()}`));
  page.on('pageerror', (err) => logs.push(`[pageerror] ${err.message}`));
  page.on('requestfailed', (req) => logs.push(`[reqfail] ${req.url()} ${req.failure()?.errorText}`));

  let out = {};
  try {
    await page.goto(`${BASE}/receiver.html`, { waitUntil: 'load', timeout: 30000 });
    // 在页面上下文里：建真实 worker，喂一张空白 ImageBitmap，
    // 只要 worker 返回 {engine:'zxing'} 而非 error，就证明 ZXing 从 CDN 初始化成功。
    out = await page.evaluate(async () => {
      const worker = new Worker('decode-worker.js', { type: 'module' });
      const reply = await new Promise((resolve) => {
        const timer = setTimeout(() => resolve({ error: 'timeout(30s) — 可能 CDN/CSP 拦截' }), 30000);
        worker.onmessage = (e) => { clearTimeout(timer); resolve(e.data); };
        worker.onerror = (e) => { clearTimeout(timer); resolve({ error: e.message || 'worker onerror' }); };
        const c = new OffscreenCanvas(64, 64);
        c.getContext('2d').fillRect(0, 0, 64, 64);
        c.convertToBlob().then((b) => createImageBitmap(b)).then((bmp) => {
          worker.postMessage({ id: 1, bitmap: bmp, grid: 1 }, [bmp]);
        });
      });
      return reply;
    });
  } catch (error) {
    out = { error: String((error && error.message) || error) };
  } finally {
    await browser.close();
  }

  const ok = out && out.engine === 'zxing' && !out.error;
  console.log('=== 线上 ZXing 初始化验证 ===');
  console.log('worker reply:', JSON.stringify(out));
  console.log('结论:', ok ? 'OK — ZXing 快路径线上可用' : '失败/需排查');
  if (logs.length) console.log('--- page logs ---\n' + logs.join('\n'));
  process.exit(ok ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
