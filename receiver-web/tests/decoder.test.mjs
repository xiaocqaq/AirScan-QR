import test from 'node:test';
import assert from 'node:assert/strict';

import decoder from '../decoder.js';

test('one-by-one scan uses the complete frame', () => {
  assert.deepEqual(decoder.gridRegions(1200, 800, 1), [
    { x: 0, y: 0, width: 1200, height: 800 },
  ]);
});

test('two-by-two scan preserves every complete QR cell', () => {
  const regions = decoder.gridRegions(1000, 800, 2);

  assert.equal(regions.length, 4);
  assert.deepEqual(regions[0], { x: 0, y: 0, width: 500, height: 400 });
  assert.deepEqual(regions[3], { x: 500, y: 400, width: 500, height: 400 });
});

test('unsupported grid values are rejected', () => {
  assert.throws(() => decoder.gridRegions(1000, 800, 4), /宫格/);
});

test('qr region is located inside a dark application window', () => {
  const width = 100;
  const height = 80;
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const bright = x >= 10 && x < 90 && y >= 20 && y < 70;
      const qrDark = x >= 20 && x < 80 && y >= 25 && y < 65 && (x + y) % 2 === 0;
      const value = qrDark ? 0 : (bright ? 255 : 12);
      const offset = (y * width + x) * 4;
      pixels.set([value, value, value, 255], offset);
    }
  }

  const region = decoder.findQrRegion({ data: pixels, width, height }, 2);
  assert.ok(region.x <= 20 && region.y <= 25);
  assert.ok(region.x + region.width >= 80);
  assert.ok(region.y + region.height >= 65);
  assert.ok(region.x >= 10 && region.y >= 20);
});
