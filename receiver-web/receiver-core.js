(function (root, factory) {
  'use strict';
  const protocol = root.AirScan && root.AirScan.protocol
    ? root.AirScan.protocol : (typeof require === 'function' ? require('./protocol.js') : null);
  const sha1 = root.AirScan && root.AirScan.sha1
    ? root.AirScan.sha1 : (typeof require === 'function' ? require('./sha1.js') : null);
  const api = factory(protocol, sha1);
  root.AirScan = root.AirScan || {};
  root.AirScan.receiver = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (protocol, sha1Module) {
  'use strict';

  function sameBytes(left, right) {
    return left.length === right.length && left.every((byte, index) => byte === right[index]);
  }

  function emptyCallback() {}

  function bytesFromTid(value) {
    if (value instanceof Uint8Array) return value;
    if (Array.isArray(value)) return Uint8Array.from(value);
    if (typeof value === 'string') {
      return Uint8Array.from(value.match(/.{2}/g) || [], (pair) => Number.parseInt(pair, 16));
    }
    return new Uint8Array(0);
  }

  class ReceiverCore {
    constructor(options) {
      if (!options || !options.store) throw new Error('ReceiverCore 需要持久化存储');
      this.store = options.store;
      this.onMeta = options.onMeta || emptyCallback;
      this.onProgress = options.onProgress || emptyCallback;
      this.onStatus = options.onStatus || emptyCallback;
      this.onComplete = options.onComplete || emptyCallback;
      this.task = null;
      this.received = new Set();
      this.paused = false;
      this.blob = null;
    }

    async acceptFrame(encodedBytes) {
      if (this.paused) return false;
      const raw = await protocol.scramble(encodedBytes);
      const frame = protocol.parseFrame(raw);
      if (!frame) return false;
      if (frame.type === protocol.TYPE_META) return this._acceptMeta(frame);
      if (frame.type === protocol.TYPE_DATA) return this._acceptData(frame);
      return false;
    }

    async _acceptMeta(meta) {
      const stored = await this.store.upsertTask(meta);
      this.task = { ...meta, receivedCount: stored.receivedCount || 0, done: Boolean(stored.done) };
      this.received = new Set(await this.store.getReceivedIndices(meta.tid));
      this.task.receivedCount = this.received.size;
      this.blob = null;
      this.onMeta(this.task);
      this._emitProgress();
      return true;
    }

    async _acceptData(frame) {
      if (!this.task || this.task.done || !sameBytes(frame.tid, this.task.tid)) return false;
      if (frame.index >= this.task.total || this.received.has(frame.index)) return false;
      const inserted = await this.store.putChunk(frame.tid, frame.index, frame.payload);
      if (!inserted) return false;
      this.received.add(frame.index);
      this.task.receivedCount = this.received.size;
      this._emitProgress();
      if (this.received.size === this.task.total) await this.finalize();
      return true;
    }

    _emitProgress() {
      this.onProgress(this.progress());
    }

    pause() {
      this.paused = true;
      this.onStatus('paused');
    }

    resume() {
      this.paused = false;
      this.onStatus('receiving');
    }

    progress() {
      const total = this.task ? this.task.total : 0;
      const received = this.received.size;
      return { received, total, missingCount: Math.max(0, total - received) };
    }

    missing() {
      if (!this.task) return [];
      const missing = [];
      for (let index = 0; index < this.task.total; index += 1) {
        if (!this.received.has(index)) missing.push(index);
      }
      return missing;
    }

    _validatedParts(chunks) {
      const hash = new sha1Module.Sha1();
      const parts = [];
      let remaining = this.task.fileSize;
      for (const chunk of chunks) {
        const bytes = chunk instanceof Uint8Array ? chunk : new Uint8Array(chunk);
        const part = bytes.subarray(0, Math.min(bytes.length, remaining));
        hash.update(part);
        parts.push(part);
        remaining -= part.length;
      }
      return { parts, digest: hash.digest(), complete: remaining === 0 };
    }

    async finalize() {
      this.onStatus('validating');
      const chunks = await this.store.readChunks(this.task.tid, this.task.total);
      const result = this._validatedParts(chunks);
      if (!result.complete || !sameBytes(result.digest, this.task.sha1)) {
        await this.store.resetTask(this.task.tid);
        this.received.clear();
        this.task.receivedCount = 0;
        this.onComplete({ ok: false, name: this.task.name, size: this.task.fileSize,
          blob: null, error: 'SHA-1 校验失败，已保留任务并清空损坏分片' });
        this._emitProgress();
        return false;
      }
      this.blob = new Blob(result.parts, { type: 'application/octet-stream' });
      this.task.done = true;
      if (this.store.markDone) await this.store.markDone(this.task.tid);
      this.onComplete({ ok: true, name: this.task.name, size: this.task.fileSize,
        blob: this.blob, error: '' });
      return true;
    }

    downloadBlob() {
      return this.blob;
    }

    async restoreLatest() {
      if (!this.store.listIncompleteTasks) return null;
      const tasks = await this.store.listIncompleteTasks();
      if (!tasks.length) return null;
      const record = tasks[0];
      this.task = {
        ...record,
        tid: bytesFromTid(record.tid),
        sha1: Uint8Array.from(record.sha1),
        done: false,
      };
      this.received = new Set(await this.store.getReceivedIndices(this.task.tid));
      this.task.receivedCount = this.received.size;
      this.blob = null;
      this.onMeta(this.task);
      this._emitProgress();
      return { ...this.task };
    }
  }

  return { ReceiverCore, sameBytes };
}));
