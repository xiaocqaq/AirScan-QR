// 通用 harness runner：起本地 http server 提供 receiver-web/，
// 用已装 chromium 打开指定 harness 页，读取 data-result。
// 用法：node run-harness.mjs tests/worker-smoke.html
// 必须走 http（ESM import / module worker / wasm fetch 在 file:// 下被拦）。
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, normalize } from 'node:path';
import playwright from '../../build/browser-smoke/playwright/node_modules/playwright/index.js';

const { chromium } = playwright;
const here = dirname(fileURLToPath(import.meta.url));
const webRoot = normalize(join(here, '..')); // receiver-web/
const targetPath = process.argv[2] || 'tests/worker-smoke.html';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.wasm': 'application/wasm',
};

function ext(path) {
  const dot = path.lastIndexOf('.');
  return dot >= 0 ? path.slice(dot).toLowerCase() : '';
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    const rel = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const full = normalize(join(webRoot, rel));
    if (!full.startsWith(webRoot)) { res.writeHead(403).end('forbidden'); return; }
    const body = await readFile(full);
    res.writeHead(200, { 'content-type': MIME[ext(full)] || 'application/octet-stream' });
    res.end(body);
  } catch (error) {
    res.writeHead(404).end(String((error && error.message) || error));
  }
});

const CHROME = process.env.CHROME_BIN
  || 'C:/Users/xiao/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe';

async function main() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const target = `http://127.0.0.1:${port}/${targetPath}`;

  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage();
  const logs = [];
  page.on('console', (msg) => logs.push(`[console] ${msg.text()}`));
  page.on('pageerror', (err) => logs.push(`[pageerror] ${err.message}`));

  let result = 'timeout';
  let output = '';
  try {
    await page.goto(target, { waitUntil: 'load' });
    await page.waitForFunction(
      () => document.body.dataset.result && document.body.dataset.result !== 'pending',
      { timeout: 30000 },
    );
    result = await page.evaluate(() => document.body.dataset.result);
    output = await page.evaluate(() => document.getElementById('output').textContent);
  } catch (error) {
    output = String((error && error.message) || error);
  } finally {
    await browser.close();
    server.close();
  }

  console.log('=== harness:', targetPath, '===');
  console.log('data-result:', result);
  console.log('output:', output);
  if (logs.length) console.log('--- page logs ---\n' + logs.join('\n'));
  process.exit(result === 'ok' ? 0 : 1);
}

main().catch((error) => {
  console.error(error);
  server.close();
  process.exit(1);
});
