import test from 'node:test';
import assert from 'node:assert/strict';

import protocol from '../protocol.js';
import sha1Module from '../sha1.js';
import receiverModule from '../receiver-core.js';

const { buildDataForTest, buildMetaForTest, scramble } = protocol;
const { Sha1 } = sha1Module;
const { ReceiverCore } = receiverModule;
const encoder = new TextEncoder();

function tidKey(tid) {
  return Array.from(tid, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

class MemoryStore {
  constructor() {
    this.tasks = new Map();
    this.chunks = new Map();
    this.received = new Map();
  }

  async upsertTask(meta) {
    const key = tidKey(meta.tid);
    if (!this.tasks.has(key)) this.tasks.set(key, { ...meta, key, receivedCount: 0, done: false });
    if (!this.received.has(key)) this.received.set(key, new Set());
    return this.tasks.get(key);
  }

  async putChunk(tid, index, payload) {
    const key = tidKey(tid);
    const chunkKey = `${key}/${index}`;
    if (this.chunks.has(chunkKey)) return false;
    this.chunks.set(chunkKey, Uint8Array.from(payload));
    this.received.get(key).add(index);
    this.tasks.get(key).receivedCount += 1;
    return true;
  }

  async hasChunk(tid, index) {
    return this.chunks.has(`${tidKey(tid)}/${index}`);
  }

  async getReceivedIndices(tid) {
    return Array.from(this.received.get(tidKey(tid)) || []).sort((a, b) => a - b);
  }

  async readChunks(tid, total) {
    return Array.from({ length: total }, (_, index) => this.chunks.get(`${tidKey(tid)}/${index}`));
  }

  async getTask(tid) {
    return this.tasks.get(tidKey(tid)) || null;
  }

  async listIncompleteTasks() {
    return Array.from(this.tasks.values()).filter((task) => !task.done);
  }

  async resetTask(tid) {
    const key = tidKey(tid);
    for (const index of this.received.get(key) || []) this.chunks.delete(`${key}/${index}`);
    this.received.set(key, new Set());
    this.tasks.get(key).receivedCount = 0;
  }
}

async function sha1(bytes) {
  return new Uint8Array(await crypto.subtle.digest('SHA-1', bytes));
}

async function fixture({ corruptHash = false } = {}) {
  const tid = new Uint8Array([84, 69, 83, 84]);
  const data = encoder.encode('hello');
  const digest = await sha1(data);
  if (corruptHash) digest[0] ^= 0xff;
  const meta = buildMetaForTest({
    tid, flags: 0, total: 2, chunkSize: 3, fileSize: data.length,
    name: 'hello.txt', sha1: digest,
  });
  return {
    tid,
    meta: await scramble(meta),
    first: await scramble(buildDataForTest(tid, 0, data.slice(0, 3))),
    second: await scramble(buildDataForTest(tid, 1, data.slice(3))),
  };
}

test('incremental sha1 matches the protocol digest', () => {
  const hash = new Sha1();
  hash.update(encoder.encode('he'));
  hash.update(encoder.encode('llo'));
  assert.equal(hash.hex(), 'aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d');
});

test('out-of-order and duplicate frames preserve progress', async () => {
  const frames = await fixture();
  const core = new ReceiverCore({ store: new MemoryStore() });
  await core.acceptFrame(frames.meta);
  await core.acceptFrame(frames.second);
  await core.acceptFrame(frames.second);
  assert.deepEqual(core.progress(), { received: 1, total: 2, missingCount: 1 });
  assert.deepEqual(core.missing(), [0]);
});

test('pause keeps task and resumes from received frames', async () => {
  const frames = await fixture();
  const core = new ReceiverCore({ store: new MemoryStore() });
  await core.acceptFrame(frames.meta);
  await core.acceptFrame(frames.first);
  core.pause();
  core.resume();
  assert.deepEqual(core.missing(), [1]);
});

test('complete transfer validates and creates the original blob', async () => {
  const frames = await fixture();
  let completed = null;
  const core = new ReceiverCore({
    store: new MemoryStore(),
    onComplete: (result) => { completed = result; },
  });
  await core.acceptFrame(frames.meta);
  await core.acceptFrame(frames.second);
  await core.acceptFrame(frames.first);

  assert.equal(completed.ok, true);
  assert.equal(completed.name, 'hello.txt');
  assert.equal(await completed.blob.text(), 'hello');
  assert.deepEqual(core.progress(), { received: 2, total: 2, missingCount: 0 });
});

test('sha1 failure clears chunks but retains task metadata for retry', async () => {
  const frames = await fixture({ corruptHash: true });
  let completed = null;
  const core = new ReceiverCore({
    store: new MemoryStore(),
    onComplete: (result) => { completed = result; },
  });
  await core.acceptFrame(frames.meta);
  await core.acceptFrame(frames.first);
  await core.acceptFrame(frames.second);

  assert.equal(completed.ok, false);
  assert.match(completed.error, /SHA-1/);
  assert.deepEqual(core.progress(), { received: 0, total: 2, missingCount: 2 });
  assert.deepEqual(core.missing(), [0, 1]);
});

test('latest incomplete task restores after page reload', async () => {
  const frames = await fixture();
  const store = new MemoryStore();
  const first = new ReceiverCore({ store });
  await first.acceptFrame(frames.meta);
  await first.acceptFrame(frames.first);

  const restored = new ReceiverCore({ store });
  const task = await restored.restoreLatest();

  assert.equal(task.name, 'hello.txt');
  assert.deepEqual(restored.progress(), { received: 1, total: 2, missingCount: 1 });
  assert.deepEqual(restored.missing(), [1]);
});
