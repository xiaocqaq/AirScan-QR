import test from 'node:test';
import assert from 'node:assert/strict';

import protocol from '../protocol.js';

const {
  FLAG_TEXT,
  TYPE_DATA,
  TYPE_META,
  buildDataForTest,
  buildMetaForTest,
  formatMissing,
  parseFrame,
  scramble,
} = protocol;

const TID = new Uint8Array([65, 83, 49, 50]);

test('scramble is self-inverse across the full byte range', async () => {
  const source = Uint8Array.from({ length: 256 }, (_, index) => index);
  const encoded = await scramble(source);

  assert.notDeepEqual(encoded, source);
  assert.deepEqual(await scramble(encoded), source);
});

test('data frame preserves transaction, index and binary payload', () => {
  const payload = new Uint8Array([0, 255, 1, 128, 13]);
  const parsed = parseFrame(buildDataForTest(TID, 7, payload));

  assert.equal(parsed.type, TYPE_DATA);
  assert.deepEqual(parsed.tid, TID);
  assert.equal(parsed.index, 7);
  assert.deepEqual(parsed.payload, payload);
});

test('meta frame decodes utf-8 name and transfer fields', () => {
  const sha1 = Uint8Array.from({ length: 20 }, (_, index) => index + 1);
  const parsed = parseFrame(buildMetaForTest({
    tid: TID,
    flags: FLAG_TEXT,
    total: 428,
    chunkSize: 1988,
    fileSize: 123456,
    name: '传输文件.txt',
    sha1,
  }));

  assert.equal(parsed.type, TYPE_META);
  assert.equal(parsed.flags, FLAG_TEXT);
  assert.equal(parsed.total, 428);
  assert.equal(parsed.chunkSize, 1988);
  assert.equal(parsed.fileSize, 123456);
  assert.equal(parsed.name, '传输文件.txt');
  assert.deepEqual(parsed.sha1, sha1);
});

test('invalid and truncated frames are rejected', () => {
  assert.equal(parseFrame(new Uint8Array([65, 83, 1])), null);
  assert.equal(parseFrame(new Uint8Array([88, 83, 1, 1])), null);
  assert.equal(parseFrame(new Uint8Array([65, 83, 2, 1])), null);
  assert.equal(parseFrame(new Uint8Array([65, 83, 1, 1, 0])), null);
});

test('missing ranges use one-based display numbering', () => {
  const missing = [0, 1, 282, 427, 430, ...Array.from({ length: 14 }, (_, index) => 545 + index)];
  assert.equal(formatMissing(missing), '1-2, 283, 428, 431, 546-559');
  assert.equal(formatMissing([]), '无缺失帧');
});
