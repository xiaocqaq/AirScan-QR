(function (root, factory) {
  'use strict';
  const api = factory(root);
  root.AirScan = root.AirScan || {};
  root.AirScan.protocol = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  const MAGIC = new Uint8Array([0x41, 0x53]);
  const VERSION = 1;
  const TYPE_META = 0;
  const TYPE_DATA = 1;
  const FLAG_TEXT = 0x01;
  const META_HEAD_LEN = 27;
  const DATA_HEAD_LEN = 12;
  const SHA1_LEN = 20;
  const seed = new TextEncoder().encode('AirScan-QR/v1');
  let keystreamCache = new Uint8Array(0);
  let extendQueue = Promise.resolve();

  function cryptoApi() {
    if (!root.crypto || !root.crypto.subtle) throw new Error('当前环境不支持 Web Crypto API');
    return root.crypto.subtle;
  }

  async function extendKeystream(size) {
    if (keystreamCache.length >= size) return;
    const target = Math.ceil(size / 32) * 32;
    const expanded = new Uint8Array(target);
    expanded.set(keystreamCache);
    const input = new Uint8Array(seed.length + 8);
    input.set(seed);
    const view = new DataView(input.buffer);
    for (let offset = keystreamCache.length; offset < target; offset += 32) {
      view.setBigUint64(seed.length, BigInt(offset / 32), false);
      const digest = await cryptoApi().digest('SHA-256', input);
      expanded.set(new Uint8Array(digest), offset);
    }
    keystreamCache = expanded;
  }

  async function scramble(input) {
    const bytes = toBytes(input);
    extendQueue = extendQueue.then(() => extendKeystream(bytes.length));
    await extendQueue;
    const result = new Uint8Array(bytes.length);
    for (let index = 0; index < bytes.length; index += 1) {
      result[index] = bytes[index] ^ keystreamCache[index];
    }
    return result;
  }

  function toBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    return Uint8Array.from(value || []);
  }

  function validPrefix(bytes) {
    return bytes.length >= 4 && bytes[0] === MAGIC[0] && bytes[1] === MAGIC[1]
      && bytes[2] === VERSION;
  }

  function parseMeta(bytes, view) {
    if (bytes.length < META_HEAD_LEN + SHA1_LEN) return null;
    const nameLength = view.getUint16(25, false);
    const needed = META_HEAD_LEN + nameLength + SHA1_LEN;
    if (bytes.length < needed) return null;
    const fileSize = view.getBigUint64(17, false);
    if (fileSize > BigInt(Number.MAX_SAFE_INTEGER)) return null;
    const nameBytes = bytes.slice(META_HEAD_LEN, META_HEAD_LEN + nameLength);
    return {
      type: TYPE_META,
      tid: bytes.slice(4, 8),
      flags: bytes[8],
      total: view.getUint32(9, false),
      chunkSize: view.getUint32(13, false),
      fileSize: Number(fileSize),
      name: new TextDecoder('utf-8', { fatal: false }).decode(nameBytes),
      sha1: bytes.slice(META_HEAD_LEN + nameLength, needed),
    };
  }

  function parseData(bytes, view) {
    if (bytes.length < DATA_HEAD_LEN) return null;
    return {
      type: TYPE_DATA,
      tid: bytes.slice(4, 8),
      index: view.getUint32(8, false),
      payload: bytes.slice(DATA_HEAD_LEN),
    };
  }

  function parseFrame(input) {
    const bytes = toBytes(input);
    if (!validPrefix(bytes)) return null;
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (bytes[3] === TYPE_META) return parseMeta(bytes, view);
    if (bytes[3] === TYPE_DATA) return parseData(bytes, view);
    return null;
  }

  async function sha1Hex(input) {
    const bytes = toBytes(input);
    const digest = new Uint8Array(await cryptoApi().digest('SHA-1', bytes));
    return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  function formatMissing(indices) {
    const sorted = Array.from(new Set(indices)).sort((left, right) => left - right);
    if (!sorted.length) return '无缺失帧';
    const ranges = [];
    let start = sorted[0] + 1;
    let previous = start;
    for (const index of sorted.slice(1)) {
      const current = index + 1;
      if (current === previous + 1) {
        previous = current;
        continue;
      }
      ranges.push(start === previous ? String(start) : `${start}-${previous}`);
      start = current;
      previous = current;
    }
    ranges.push(start === previous ? String(start) : `${start}-${previous}`);
    return ranges.join(', ');
  }

  function buildDataForTest(tid, index, payload) {
    const result = new Uint8Array(DATA_HEAD_LEN + payload.length);
    result.set(MAGIC, 0);
    result.set([VERSION, TYPE_DATA], 2);
    result.set(toBytes(tid).slice(0, 4), 4);
    new DataView(result.buffer).setUint32(8, index, false);
    result.set(toBytes(payload), DATA_HEAD_LEN);
    return result;
  }

  function buildMetaForTest(meta) {
    const name = new TextEncoder().encode(meta.name);
    const result = new Uint8Array(META_HEAD_LEN + name.length + SHA1_LEN);
    const view = new DataView(result.buffer);
    result.set(MAGIC, 0);
    result.set([VERSION, TYPE_META], 2);
    result.set(toBytes(meta.tid).slice(0, 4), 4);
    result[8] = meta.flags;
    view.setUint32(9, meta.total, false);
    view.setUint32(13, meta.chunkSize, false);
    view.setBigUint64(17, BigInt(meta.fileSize), false);
    view.setUint16(25, name.length, false);
    result.set(name, META_HEAD_LEN);
    result.set(toBytes(meta.sha1).slice(0, SHA1_LEN), META_HEAD_LEN + name.length);
    return result;
  }

  return {
    MAGIC, VERSION, TYPE_META, TYPE_DATA, FLAG_TEXT,
    scramble, parseFrame, sha1Hex, formatMissing,
    buildDataForTest, buildMetaForTest,
  };
}));
