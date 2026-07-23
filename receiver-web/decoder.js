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
  // jsQR 最佳工作边长：太小模块糊，太大计算慢
  const MIN_DECODE_SIDE = 360;
  // 高密度二进制 QR 需要更多像素/模块；上限控制 CPU，避免 FPS 掉光
  const MAX_DECODE_SIDE = 900;
  const PREVIEW_MAX_SIDE = 720;
  let decoderFunction = null;
  let loadPromise = null;
  let workCanvas = null;
  let previewCanvas = null;

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
    return data[offset] + data[offset + 1] + data[offset + 2] >= 600;
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
      return bright / samples >= 0.12;
    });
    if (!row) return { x: 0, y: 0, width, height, step };
    const column = longestRun(width, step, (x) => {
      let bright = 0;
      let samples = 0;
      for (let y = row.start; y < row.end; y += step) {
        bright += isBright(data, width, x, y) ? 1 : 0;
        samples += 1;
      }
      return bright / samples >= 0.12;
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
    const padding = Math.max(stage.step * 2, Math.round(cell * 0.06));
    const left = Math.max(stage.x, minX - padding);
    const top = Math.max(stage.y, minY - padding);
    const right = Math.min(stage.x + stage.width, maxX + stage.step + padding);
    const bottom = Math.min(stage.y + stage.height, maxY + stage.step + padding);
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  function clampRegion(region, width, height) {
    const x = Math.max(0, Math.min(width - 1, Math.floor(region.x)));
    const y = Math.max(0, Math.min(height - 1, Math.floor(region.y)));
    const right = Math.max(x + 1, Math.min(width, Math.ceil(region.x + region.width)));
    const bottom = Math.max(y + 1, Math.min(height, Math.ceil(region.y + region.height)));
    return { x, y, width: right - x, height: bottom - y };
  }

  function expandRegion(region, ratio, bounds) {
    const padX = Math.round(region.width * ratio);
    const padY = Math.round(region.height * ratio);
    const x = Math.max(bounds.x, region.x - padX);
    const y = Math.max(bounds.y, region.y - padY);
    const right = Math.min(bounds.x + bounds.width, region.x + region.width + padX);
    const bottom = Math.min(bounds.y + bounds.height, region.y + region.height + padY);
    return { x, y, width: Math.max(1, right - x), height: Math.max(1, bottom - y) };
  }

  function centerRegion(width, height, ratio) {
    const w = Math.max(1, Math.round(width * ratio));
    const h = Math.max(1, Math.round(height * ratio));
    return {
      x: Math.floor((width - w) / 2),
      y: Math.floor((height - h) / 2),
      width: w,
      height: h,
    };
  }

  function enhanceLuminance(image) {
    const { data, width, height } = image;
    let min = 255;
    let max = 0;
    const luma = new Uint8Array(width * height);
    for (let i = 0, p = 0; i < data.length; i += 4, p += 1) {
      const y = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
      luma[p] = y;
      if (y < min) min = y;
      if (y > max) max = y;
    }
    const range = Math.max(1, max - min);
    const out = new Uint8ClampedArray(data.length);
    for (let p = 0, i = 0; p < luma.length; p += 1, i += 4) {
      const stretched = ((luma[p] - min) * 255 / range) | 0;
      out[i] = out[i + 1] = out[i + 2] = stretched;
      out[i + 3] = 255;
    }
    return { data: out, width, height };
  }

  function createCanvas(width, height) {
    if (typeof root.OffscreenCanvas === 'function') {
      try {
        return new root.OffscreenCanvas(width, height);
      } catch (_) {
        // fall through
      }
    }
    if (root.document) {
      const canvas = root.document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      return canvas;
    }
    return null;
  }

  function ensureCanvas(slot, width, height) {
    let canvas = slot === 'work' ? workCanvas : previewCanvas;
    if (!canvas || canvas.width !== width || canvas.height !== height) {
      canvas = createCanvas(width, height);
      if (slot === 'work') workCanvas = canvas;
      else previewCanvas = canvas;
    }
    return canvas;
  }

  function chooseScales(width, height) {
    // 对外保持数组 API；热路径只用 ideal 一个尺度
    const minSide = Math.min(width, height);
    const maxSide = Math.max(width, height);
    const scales = [];
    if (maxSide > MAX_DECODE_SIDE) scales.push(MAX_DECODE_SIDE / maxSide);
    else if (minSide < MIN_DECODE_SIDE) scales.push(MIN_DECODE_SIDE / minSide);
    else scales.push(1);
    if (minSide < 160) scales.push(Math.min(4, (MIN_DECODE_SIDE * 1.25) / minSide));
    return scales.filter((scale) => scale > 0.25 && scale <= 4);
  }

  function targetSize(width, height) {
    const minSide = Math.min(width, height);
    const maxSide = Math.max(width, height);
    let scale = 1;
    if (maxSide > MAX_DECODE_SIDE) scale = MAX_DECODE_SIDE / maxSide;
    else if (minSide < MIN_DECODE_SIDE) scale = MIN_DECODE_SIDE / minSide;
    return {
      width: Math.max(1, Math.round(width * scale)),
      height: Math.max(1, Math.round(height * scale)),
    };
  }

  function extractRegion(sourceCanvas, region) {
    const size = targetSize(region.width, region.height);
    const canvas = ensureCanvas('work', size.width, size.height);
    if (!canvas) return null;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.imageSmoothingEnabled = false;
    context.clearRect(0, 0, size.width, size.height);
    context.drawImage(
      sourceCanvas,
      region.x, region.y, region.width, region.height,
      0, 0, size.width, size.height,
    );
    return context.getImageData(0, 0, size.width, size.height);
  }

  function tryDecodeOnce(decode, image, inversionAttempts) {
    const result = decode(image.data, image.width, image.height, { inversionAttempts });
    return result && result.binaryData && result.binaryData.length
      ? Uint8Array.from(result.binaryData)
      : null;
  }

  function tryDecodeImage(decode, image, deep) {
    // 快路径：原图 + 不反色（正常黑码白底）
    let payload = tryDecodeOnce(decode, image, 'dontInvert');
    if (payload) return payload;
    payload = tryDecodeOnce(decode, image, 'attemptBoth');
    if (payload || !deep) return payload;
    // 慢路径：仅失败时做亮度拉伸
    const enhanced = enhanceLuminance(image);
    payload = tryDecodeOnce(decode, enhanced, 'dontInvert');
    if (payload) return payload;
    return tryDecodeOnce(decode, enhanced, 'attemptBoth');
  }

  function decodeRegion(sourceCanvas, region, decode, deep) {
    if (region.width < 8 || region.height < 8) return null;
    const image = extractRegion(sourceCanvas, region);
    if (!image) return null;
    return tryDecodeImage(decode, image, deep);
  }

  function payloadKey(payload) {
    let key = '';
    for (const byte of payload) key += String.fromCharCode(byte);
    return key;
  }

  function buildPreview(sourceCanvas) {
    const maxSide = Math.max(sourceCanvas.width, sourceCanvas.height);
    const scale = maxSide > PREVIEW_MAX_SIDE ? PREVIEW_MAX_SIDE / maxSide : 1;
    const width = Math.max(1, Math.round(sourceCanvas.width * scale));
    const height = Math.max(1, Math.round(sourceCanvas.height * scale));
    const canvas = ensureCanvas('preview', width, height);
    if (!canvas) {
      return {
        scale: 1,
        image: sourceCanvas.getContext('2d', { willReadFrequently: true })
          .getImageData(0, 0, sourceCanvas.width, sourceCanvas.height),
      };
    }
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.imageSmoothingEnabled = true;
    context.drawImage(sourceCanvas, 0, 0, width, height);
    return { scale, image: context.getImageData(0, 0, width, height) };
  }

  function mapRegion(region, scale, fullWidth, fullHeight) {
    if (scale === 1) return clampRegion(region, fullWidth, fullHeight);
    return clampRegion({
      x: region.x / scale,
      y: region.y / scale,
      width: region.width / scale,
      height: region.height / scale,
    }, fullWidth, fullHeight);
  }

  function collectPayloads(sourceCanvas, regions, decode, bounds, deep) {
    const unique = new Map();
    for (const region of regions) {
      const expanded = expandRegion(region, 0.04, bounds);
      const payload = decodeRegion(sourceCanvas, expanded, decode, deep);
      if (payload) unique.set(payloadKey(payload), payload);
    }
    return unique;
  }

  async function decodeFrame(canvas, grid) {
    const decode = await loadDecoder();
    const bounds = { x: 0, y: 0, width: canvas.width, height: canvas.height };
    // 1) 小预览定位亮区，避免对 2K/4K 整帧 getImageData
    const preview = buildPreview(canvas);
    const areaPreview = findQrRegion(preview.image, grid);
    const area = mapRegion(areaPreview, preview.scale, canvas.width, canvas.height);
    const regions = gridRegions(area.width, area.height, grid, area.x, area.y);

    // 2) 快路径：单尺度 + 不做增强
    let unique = collectPayloads(canvas, regions, decode, bounds, false);

    // 3) 失败再深扫一次（增强），仍只扫裁剪区
    if (!unique.size) {
      unique = collectPayloads(canvas, regions, decode, bounds, true);
    }

    // 4) 1×1 再试中心区域，永不直接硬扫整桌面大图
    if (!unique.size && grid === 1) {
      const fallbacks = [
        centerRegion(canvas.width, canvas.height, 0.55),
        centerRegion(canvas.width, canvas.height, 0.75),
      ];
      for (const region of fallbacks) {
        let payload = decodeRegion(canvas, region, decode, false);
        if (!payload) payload = decodeRegion(canvas, region, decode, true);
        if (payload) {
          unique.set(payloadKey(payload), payload);
          break;
        }
      }
    }

    return Array.from(unique.values());
  }

  function setDecoderForTest(decode) {
    decoderFunction = decode;
    loadPromise = null;
  }

  return {
    CDN_URL,
    LOCAL_URL,
    LOAD_TIMEOUT_MS,
    MIN_DECODE_SIDE,
    MAX_DECODE_SIDE,
    PREVIEW_MAX_SIDE,
    loadDecoder,
    gridRegions,
    findQrRegion,
    enhanceLuminance,
    chooseScales,
    decodeFrame,
    setDecoderForTest,
  };
}));
