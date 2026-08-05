// AirScan-QR 解码 Worker（module worker，由 app.js 以 { type: 'module' } 创建）。
// 职责：在 worker 线程用 zxing-wasm 原生多码解码，主线程只抓帧+落盘+UI。
// 协议边界：返回的 payload 仍是「原始 QR 字节」（加扰态），与 jsQR 版一致，
// 由主线程 ReceiverCore.acceptFrame → protocol.scramble 逆变换。
//
// 前提（Phase 0 已验证）：ZXing result.bytes 提供未经字符集破坏的原始字节，
// 能替代 jsQR 的 binaryData 保住二进制协议。
import {
  readBarcodes,
  prepareZXingModule,
} from 'https://cdn.jsdelivr.net/npm/zxing-wasm@3.1.2/dist/es/reader/index.js';

// ESM 在 dist/es/reader/，wasm 在 dist/reader/，默认相对定位会 404，必须显式指向。
const WASM_URL = 'https://cdn.jsdelivr.net/npm/zxing-wasm@3.1.2/dist/reader/zxing_reader.wasm';

let readyPromise = null;
let workCanvas = null;

function ensureReady() {
  if (!readyPromise) {
    // fireImmediately: 立即下载并实例化 wasm，避免首帧解码时才 404 重试。
    readyPromise = prepareZXingModule({
      overrides: {
        locateFile: (path, prefix) => (path.endsWith('.wasm') ? WASM_URL : prefix + path),
      },
      fireImmediately: true,
    });
  }
  return readyPromise;
}

function imageDataFromBitmap(bitmap) {
  const { width, height } = bitmap;
  if (!workCanvas || workCanvas.width !== width || workCanvas.height !== height) {
    workCanvas = new OffscreenCanvas(width, height);
  }
  const context = workCanvas.getContext('2d', { willReadFrequently: true });
  context.imageSmoothingEnabled = false;
  context.drawImage(bitmap, 0, 0);
  return context.getImageData(0, 0, width, height);
}

function dedupePayloads(results) {
  // ZXing 原生一次返回画面内全部 QR，按字节内容去重（同一 QR 可能多次命中）。
  const unique = new Map();
  for (const result of results) {
    if (!result || !result.isValid || !result.bytes || !result.bytes.length) continue;
    const bytes = result.bytes instanceof Uint8Array
      ? result.bytes : Uint8Array.from(result.bytes);
    let key = '';
    for (const byte of bytes) key += String.fromCharCode(byte);
    if (!unique.has(key)) unique.set(key, bytes);
  }
  return Array.from(unique.values());
}

self.onmessage = async (event) => {
  const { id, bitmap, grid } = event.data || {};
  if (typeof id !== 'number') return;
  try {
    await ensureReady();
    const imageData = imageDataFromBitmap(bitmap);
    if (typeof bitmap.close === 'function') bitmap.close();
    const results = await readBarcodes(imageData, {
      formats: ['QRCode'],
      tryHarder: true,
      // grid*grid 作为上限；ZXing 整帧检测，无需手动切块。
      maxNumberOfSymbols: Math.max(1, (grid || 1) * (grid || 1)),
    });
    const payloads = dedupePayloads(results);
    // 转移各 payload 的底层 buffer，避免拷贝。
    const transfer = payloads.map((payload) => payload.buffer);
    self.postMessage({ id, payloads, engine: 'zxing' }, transfer);
  } catch (error) {
    if (bitmap && typeof bitmap.close === 'function') bitmap.close();
    self.postMessage({ id, error: String((error && error.message) || error) });
  }
};
