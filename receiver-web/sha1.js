(function (root, factory) {
  'use strict';
  const api = factory();
  root.AirScan = root.AirScan || {};
  root.AirScan.sha1 = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const INITIAL = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];

  function toBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    return Uint8Array.from(value || []);
  }

  function rotateLeft(value, amount) {
    return ((value << amount) | (value >>> (32 - amount))) >>> 0;
  }

  class Sha1 {
    constructor() {
      this.state = Uint32Array.from(INITIAL);
      this.buffer = new Uint8Array(64);
      this.words = new Uint32Array(80);
      this.bufferLength = 0;
      this.bytesHashed = 0;
      this.finished = false;
      this.result = null;
    }

    update(value) {
      if (this.finished) throw new Error('SHA-1 已完成，不能继续写入');
      const bytes = toBytes(value);
      this.bytesHashed += bytes.length;
      let offset = 0;
      if (this.bufferLength) offset = this._fillBuffer(bytes, offset);
      while (offset + 64 <= bytes.length) {
        this._processBlock(bytes, offset);
        offset += 64;
      }
      if (offset < bytes.length) {
        this.buffer.set(bytes.subarray(offset), 0);
        this.bufferLength = bytes.length - offset;
      }
      return this;
    }

    _fillBuffer(bytes, offset) {
      const count = Math.min(64 - this.bufferLength, bytes.length - offset);
      this.buffer.set(bytes.subarray(offset, offset + count), this.bufferLength);
      this.bufferLength += count;
      if (this.bufferLength === 64) {
        this._processBlock(this.buffer, 0);
        this.bufferLength = 0;
      }
      return offset + count;
    }

    _expandWords(block, offset) {
      for (let index = 0; index < 16; index += 1) {
        const start = offset + index * 4;
        this.words[index] = ((block[start] << 24) | (block[start + 1] << 16)
          | (block[start + 2] << 8) | block[start + 3]) >>> 0;
      }
      for (let index = 16; index < 80; index += 1) {
        this.words[index] = rotateLeft(this.words[index - 3] ^ this.words[index - 8]
          ^ this.words[index - 14] ^ this.words[index - 16], 1);
      }
    }

    _processBlock(block, offset) {
      this._expandWords(block, offset);
      let [a, b, c, d, e] = this.state;
      for (let index = 0; index < 80; index += 1) {
        let f;
        let k;
        if (index < 20) { f = (b & c) | (~b & d); k = 0x5a827999; }
        else if (index < 40) { f = b ^ c ^ d; k = 0x6ed9eba1; }
        else if (index < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdc; }
        else { f = b ^ c ^ d; k = 0xca62c1d6; }
        const next = (rotateLeft(a, 5) + f + e + k + this.words[index]) >>> 0;
        e = d; d = c; c = rotateLeft(b, 30); b = a; a = next;
      }
      this.state[0] = (this.state[0] + a) >>> 0;
      this.state[1] = (this.state[1] + b) >>> 0;
      this.state[2] = (this.state[2] + c) >>> 0;
      this.state[3] = (this.state[3] + d) >>> 0;
      this.state[4] = (this.state[4] + e) >>> 0;
    }

    digest() {
      if (this.result) return this.result.slice();
      const bitLength = BigInt(this.bytesHashed) * 8n;
      this.buffer[this.bufferLength] = 0x80;
      this.bufferLength += 1;
      if (this.bufferLength > 56) {
        this.buffer.fill(0, this.bufferLength);
        this._processBlock(this.buffer, 0);
        this.bufferLength = 0;
      }
      this.buffer.fill(0, this.bufferLength, 56);
      const view = new DataView(this.buffer.buffer);
      view.setBigUint64(56, bitLength, false);
      this._processBlock(this.buffer, 0);
      this.finished = true;
      this.result = new Uint8Array(20);
      const output = new DataView(this.result.buffer);
      this.state.forEach((value, index) => output.setUint32(index * 4, value, false));
      return this.result.slice();
    }

    hex() {
      return Array.from(this.digest(), (byte) => byte.toString(16).padStart(2, '0')).join('');
    }
  }

  return { Sha1 };
}));
