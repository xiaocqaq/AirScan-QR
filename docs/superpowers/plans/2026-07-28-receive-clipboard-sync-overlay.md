# Receive Clipboard Sync Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 完成接收文本滚动条隐藏、文件广播剪贴板确认、同步结果弹窗和暂停悬浮窗取消置顶。

**Architecture:** 保持现有 pywebview API 与应用内弹窗体系。后端只增加广播源状态和待确认文本状态；前端复用已有确认弹窗并新增同步结果弹窗；悬浮窗复用现有实例，通过 `on_top` 切换暂停状态。

**Tech Stack:** Python 3.11、pywebview、HTML、CSS、JavaScript、unittest/pytest、PyInstaller。

## Global Constraints

- 不新增依赖，不修改协议和文件夹同步算法。
- 中文使用 UTF-8，不添加 BOM。
- 不执行 Git commit、push、reset 或其他历史操作。

---

### Task 1: 隐藏接收文本滚动条

**Files:**
- Modify: `airscan/ui.css`
- Test: `tests/test_ui_contract.py`

- [x] 写失败测试，要求 `.msg-text` 隐藏标准和 WebKit 滚动条。
- [x] 运行 `python -m pytest tests/test_ui_contract.py -q` 确认失败。
- [x] 添加 `scrollbar-width: none`、`-ms-overflow-style: none` 和伪元素规则，保留 `overflow: auto`。
- [x] 运行定向测试确认通过。

### Task 2: 文件广播剪贴板确认

**Files:**
- Modify: `airscan/app.py`
- Modify: `airscan/ui.js`
- Test: `tests/test_app_clipboard.py`

- [x] 写失败测试覆盖文件广播不被立即替换、确认发送、取消、旧序号和文本广播直接切换。
- [x] 运行 `python -m pytest tests/test_app_clipboard.py -q` 确认失败。
- [x] 增加 `_send_is_file`、`_pending_clipboard_text`、`_pending_clipboard_seq`、确认与取消 API。
- [x] 前端增加 `onClipboardNeedsConfirm` 并复用 `confirmDialog`。
- [x] 运行剪贴板测试确认通过。

### Task 3: 同步结果弹窗

**Files:**
- Modify: `airscan/ui.html`
- Modify: `airscan/ui.css`
- Modify: `airscan/ui.js`
- Test: `tests/test_ui_contract.py`

- [x] 写失败合同测试验证结果弹窗结构、完整异常列表和关闭行为。
- [x] 运行 UI 合同测试确认失败。
- [x] 添加同步结果弹窗 HTML 与主题化 CSS。
- [x] 将 `syncApply` 改为调用 `showSyncResult`，展示完整数据并支持打开目标目录。
- [x] 运行 UI 合同测试确认通过。

### Task 4: 暂停悬浮窗取消置顶

**Files:**
- Modify: `airscan/app.py`
- Test: `tests/test_app_rendering.py`

- [x] 写失败测试验证暂停不销毁窗口、自动暂停取消置顶、恢复时重新置顶。
- [x] 运行定向测试确认失败。
- [x] 修改 `pause_send` 保留窗口并调用 `_set_overlay_on_top(False)`，更新暂停状态。
- [x] 修改 `open_overlay` 复用窗口时恢复置顶；自动暂停同步取消置顶。
- [x] 运行定向测试确认通过。

### Task 5: 整体验证与打包

**Files:**
- Build output: `dist/AirScan-QR.exe`

- [x] 运行 `python -m pytest -q`。
- [x] 运行 `python -m compileall -q airscan main.py`。
- [x] 运行主界面和弹窗 JavaScript 语法检查。
- [x] 运行 `git diff --check`。
- [x] 运行 `build.bat` 重建 EXE。
- [x] 启动 EXE 烟测并确认进程响应。
