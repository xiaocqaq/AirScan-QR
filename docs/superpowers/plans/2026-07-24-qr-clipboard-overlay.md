# QR Clipboard Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 开始广播后，以 1×1 置顶悬浮窗显示二维码，并自动监听用户复制/剪切的文本内容进行广播。

**Architecture:** 发送端仍复用现有 `Sender` 协议和 5轮/30秒自动暂停逻辑。新增剪贴板监听器把文本变化转成发送任务；新增专用浮窗页面接收同一张 QR data URL，保持外部接收端协议不变。

**Tech Stack:** Python 3、pywebview、pywin32、HTML/CSS/JS、本地 `unittest`。

## Global Constraints

- 不修改接收端协议；文本仍通过 `FLAG_TEXT` 和新 `tid` 触发接收端自动切换。
- 悬浮窗广播固定使用 `1×1`；主界面原宫格设置仅影响手动广播。
- 每次剪贴板文本变化立即替换当前广播任务，并重新应用 5轮/30秒阈值。
- 剪贴板监听只在发送广播生命周期内启用；暂停后停止监听。
- UI 保持可关闭、可缩放、可拖拽，状态提示清晰。

---

### Task 1: 剪贴板监听器

**Files:**
- Create: `airscan/clipboard.py`
- Test: `tests/test_clipboard.py`

**Interfaces:**
- Produces: `normalize_clipboard_text(value: str) -> str | None`
- Produces: `ClipboardWatcher(on_text, interval=0.2, reader=None)` with `start()`, `stop()`, `ignore_text(text)`

- [x] **Step 1: Write failing tests**

```python
import unittest
from airscan.clipboard import ClipboardWatcher, normalize_clipboard_text

class ClipboardTests(unittest.TestCase):
    def test_normalize_ignores_empty_text(self):
        self.assertIsNone(normalize_clipboard_text('  \r\n  '))

    def test_watcher_suppresses_ignored_text_once(self):
        seen = []
        watcher = ClipboardWatcher(seen.append, reader=lambda: 'secret')
        watcher.ignore_text('secret')
        watcher.poll_once()
        self.assertEqual([], seen)
        watcher.poll_once()
        self.assertEqual(['secret'], seen)
```

- [x] **Step 2: Implement minimal watcher**

`airscan/clipboard.py` 提供轮询式实现，Windows 上默认读取 `CF_UNICODETEXT`，测试可注入 reader。

- [x] **Step 3: Run tests**

Run: `python -m unittest tests.test_clipboard -v`
Expected: PASS

### Task 2: 发送端任务协调

**Files:**
- Modify: `airscan/app.py`

**Interfaces:**
- Consumes: `ClipboardWatcher`
- Produces: `Api._replace_send_source(src, err, grid, fps, start_index=1)`
- Produces: `Api._start_clipboard_watch()` and `Api._stop_clipboard_watch()`

- [x] **Step 1: Serialize send replacement**

为发送任务切换增加锁，剪贴板和手动发送共用同一路径，避免线程交叉 join 或覆盖 `self.sender`。

- [x] **Step 2: Clipboard text creates 1×1 task**

剪贴板变化调用内部发送路径，使用当前纠错等级/FPS，强制 `grid=1`，状态提示为“剪贴板广播”。

- [x] **Step 3: Pause stops watcher**

手动暂停和自动暂停都停止剪贴板监听，避免暂停后继续重启广播。

### Task 3: 置顶悬浮窗

**Files:**
- Create: `airscan/overlay.html`
- Create: `airscan/overlay.css`
- Create: `airscan/overlay.js`
- Modify: `airscan/app.py`
- Modify: `airscan/ui.js`

**Interfaces:**
- Produces: `Api.open_overlay()` and `Api.close_overlay()`
- Produces: `pushOverlayQR(dataurl, status)` in overlay page

- [x] **Step 1: Create floating page**

悬浮页只包含 QR 图像、状态文本和关闭按钮，保证高对比、明确 alt 文案、无 emoji 结构图标。

- [x] **Step 2: Open on broadcast**

开始广播时创建 `on_top=True` 可缩放窗口，关闭不停止主广播；暂停时隐藏/关闭。

- [x] **Step 3: Mirror QR frames**

`_send_loop()` 将同一张 QR data URL 推送到主窗口和悬浮窗。

### Task 4: Final verification

**Files:**
- Verify: `airscan/app.py`, `airscan/clipboard.py`, `airscan/overlay.*`, `airscan/ui.js`

- [x] **Step 1: Run unit tests**

Run: `python -m unittest discover -v`
Expected: PASS

- [x] **Step 2: Compile changed Python files**

Run: `python -m py_compile airscan/app.py airscan/clipboard.py`
Expected: no output and exit 0
