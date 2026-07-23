(function (root, factory) {
  'use strict';
  const api = factory(root);
  root.AirScan = root.AirScan || {};
  root.AirScan.decoder = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  const CDN_URL = 'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js';
  const LOCAL_URL = 'vendor/jsQR.js';
  const LOAD_TIMEOUT_MS = 4000;
  let decoderFunction = null;
  let loadPromise = null;

  function loadScript(source) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        root.clearTimeout(timeoutId);
        callback(value);
      };
      script.src = source;
      script.async = true;
      script.addEventListener('load', () => finish(resolve), { once: true });
      script.addEventListener('error', () => finish(reject, new Error(`无法加载 ${source}`)), { once: true });
      const timeoutId = root.setTimeout(() => {
        script.remove();
        finish(reject, new Error(`加载 ${source} 超时`));
      }, LOAD_TIMEOUT_MS);
      document.head.appendChild(script);
    });
  }

  async function loadDecoder() {
    if (decoderFunction) return decoderFunction;
    if (typeof root.jsQR === 'function') {
      decoderFunction = root.jsQR;
      return decoderFunction;
    }
    if (!root.document) throw new Error('当前环境没有可用的 QR 解码器');
    if (!loadPromise) {
      loadPromise = loadScript(CDN_URL).catch(() => loadScript(LOCAL_URL)).then(() => {
        if (typeof root.jsQR !== 'function') throw new Error('QR 解码器加载失败，请检查网络或本地资源');
        decoderFunction = root.jsQR;
        return decoderFunction;
      });
    }
    return loadPromise;
  }

  function gridRegions(width, height, grid, originX, originY) {
    if (![1, 2, 3].includes(grid)) throw new RangeError('宫格必须是 1、2 或 3');
    const offsetX = originX || 0;
    const offsetY = originY || 0;
    if (grid === 1) return [{ x: offsetX, y: offsetY, width, height }];
    const regions = [];
    for (let row = 0; row < grid; row += 1) {
      for (let column = 0; column < grid; column += 1) {
        const left = Math.round(column * width / grid);
        const top = Math.round(row * height / grid);
        const right = Math.round((column + 1) * width / grid);
        const bottom = Math.round((row + 1) * height / grid);
        regions.push({
          x: offsetX + left,
          y: offsetY + top,
          width: right - left,
          height: bottom - top,
        });
      }
    }
    return regions;
  }

  function longestRun(length, step, qualifies) {
    let best = null;
    let start = null;
    for (let position = 0; position < length; position += step) {
      if (qualifies(position)) {
        if (start === null) start = position;
        continue;
      }
      if (start !== null && (!best || position - start > best.end - best.start)) {
        best = { start, end: position };
      }
      start = null;
    }
    if (start !== null && (!best || length - start > best.end - best.start)) {
      best = { start, end: length };
    }
    return best;
  }

  function isBright(data, width, x, y) {
    const offset = (y * width + x) * 4;
    return data[offset] + data[offset + 1] + data[offset + 2] >= 690;
  }

  function findBrightStage(image) {
    const { data, width, height } = image;
    const step = Math.max(1, Math.floor(Math.min(width, height) / 400));
    const row = longestRun(height, step, (y) => {
      let bright = 0;
      let samples = 0;
      for (let x = 0; x < width; x += step) {
        bright += isBright(data, width, x, y) ? 1 : 0;
        samples += 1;
      }
      return bright / samples >= 0.18;
    });
    if (!row) return { x: 0, y: 0, width, height, step };
    const column = longestRun(width, step, (x) => {
      let bright = 0;
      let samples = 0;
      for (let y = row.start; y < row.end; y += step) {
        bright += isBright(data, width, x, y) ? 1 : 0;
        samples += 1;
      }
      return bright / samples >= 0.18;
    });
    return column
      ? { x: column.start, y: row.start, width: column.end - column.start, height: row.end - row.start, step }
      : { x: 0, y: row.start, width, height: row.end - row.start, step };
  }

  function findQrRegion(image, grid) {
    const stage = findBrightStage(image);
    const { data, width } = image;
    let minX = stage.x + stage.width;
    let minY = stage.y + stage.height;
    let maxX = stage.x;
    let maxY = stage.y;
    for (let y = stage.y + stage.step; y < stage.y + stage.height - stage.step; y += stage.step) {
      for (let x = stage.x + stage.step; x < stage.x + stage.width - stage.step; x += stage.step) {
        const offset = (y * width + x) * 4;
        if (data[offset] + data[offset + 1] + data[offset + 2] >= 180) continue;
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
    }
    if (maxX <= minX || maxY <= minY) return stage;
    const cell = Math.min((maxX - minX) / grid, (maxY - minY) / grid);
    const padding = Math.max(stage.step, Math.round(cell * 0.04));
    const left = Math.max(stage.x, minX - padding);
    const top = Math.max(stage.y, minY - padding);
    const right = Math.min(stage.x + stage.width, maxX + stage.step + padding);
    const bottom = Math.min(stage.y + stage.height, maxY + stage.step + padding);
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  function decodeRegion(context, region, decode) {
    const image = context.getImageData(region.x, region.y, region.width, region.height);
    const result = decode(image.data, image.width, image.height, { inversionAttempts: 'attemptBoth' });
    return result && result.binaryData ? Uint8Array.from(result.binaryData) : null;
  }

  function payloadKey(payload) {
    let key = '';
    for (const byte of payload) key += String.fromCharCode(byte);
    return key;
  }

  async function decodeFrame(canvas, grid) {
    const decode = await loadDecoder();
    const context = canvas.getContext('2d', { willReadFrequently: true });
    const area = grid === 1
      ? { x: 0, y: 0, width: canvas.width, height: canvas.height }
      : findQrRegion(context.getImageData(0, 0, canvas.width, canvas.height), grid);
    const regions = gridRegions(area.width, area.height, grid, area.x, area.y);
    const decoded = await Promise.all(regions.map((region) => (
      Promise.resolve().then(() => decodeRegion(context, region, decode))
    )));
    const unique = new Map();
    decoded.filter(Boolean).forEach((payload) => unique.set(payloadKey(payload), payload));
    return Array.from(unique.values());
  }

  function setDecoderForTest(decode) {
    decoderFunction = decode;
    loadPromise = null;
  }

  return { CDN_URL, LOCAL_URL, LOAD_TIMEOUT_MS, loadDecoder, gridRegions, findQrRegion,
    decodeFrame, setDecoderForTest };
}));
