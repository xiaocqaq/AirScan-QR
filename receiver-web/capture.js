(function (root, factory) {
  'use strict';
  const api = factory(root);
  root.AirScan = root.AirScan || {};
  root.AirScan.capture = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  class CaptureController {
    constructor(video, options) {
      this.video = video;
      this.onFrame = options.onFrame;
      this.onEnded = options.onEnded || (() => {});
      this.onError = options.onError || (() => {});
      this.intervalMs = options.intervalMs || 80;
      this.stream = null;
      this.animationId = 0;
      this.active = false;
      this.paused = false;
      this.processing = false;
      this.lastFrameAt = 0;
      this.scan = this.scan.bind(this);
      this.handleEnded = this.handleEnded.bind(this);
    }

    async scan(timestamp) {
      if (!this.active) return;
      this.animationId = root.requestAnimationFrame(this.scan);
      const waiting = this.paused || this.processing || this.video.readyState < 2;
      if (waiting || timestamp - this.lastFrameAt < this.intervalMs) return;
      this.lastFrameAt = timestamp;
      this.processing = true;
      try {
        await this.onFrame(this.video);
      } catch (error) {
        this.onError(error);
      } finally {
        this.processing = false;
      }
    }

    handleEnded() {
      this.active = false;
      this.paused = false;
      root.cancelAnimationFrame(this.animationId);
      this.onEnded();
    }

    async start() {
      const media = root.navigator && root.navigator.mediaDevices;
      if (!media || !media.getDisplayMedia) {
        throw new Error('当前浏览器不支持共享窗口，请使用最新版 Chrome 或 Edge');
      }
      this.stop();
      this.stream = await media.getDisplayMedia({ video: { cursor: 'never' }, audio: false });
      this.video.srcObject = this.stream;
      await this.video.play();
      const track = this.stream.getVideoTracks()[0];
      track.addEventListener('ended', this.handleEnded, { once: true });
      this.active = true;
      this.paused = false;
      this.lastFrameAt = 0;
      this.animationId = root.requestAnimationFrame(this.scan);
      return this.stream;
    }

    pause() { if (this.active) this.paused = true; }
    resume() { if (this.active) this.paused = false; }
    isActive() { return this.active; }
    isPaused() { return this.paused; }

    stop() {
      this.active = false;
      this.paused = false;
      root.cancelAnimationFrame(this.animationId);
      if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
      this.video.srcObject = null;
    }
  }

  function createCapture(video, options) {
    return new CaptureController(video, options);
  }

  return { CaptureController, createCapture };
}));
