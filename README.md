# AirScan-QR 桌面版 📡

**AirScan-QR** 是一款专为**物理隔离环境 (Air-gapped)** 及 **跨端受限网络** 设计的文件传输工具。通过"屏幕显示动态二维码流 + 窗口捕获解码"的视觉链路，在无网络、无 U 盘、无蓝牙的场景下把文件从一台 PC "隔空"传到另一台 PC。

本版本为**原生 Python 桌面应用**（pywebview + HTML 现代化界面），可打包成免环境 `.exe`，专注 **PC → PC** 传输。

## 🌟 核心场景

* **封闭开发/实验室**：从物理隔离的内网 PC 取出日志、代码、小文件。
* **远程桌面 / VPN 穿透**：接收端锁定发送端窗口，把文件从远程服务器"拿"回本地。
* **无痕传输**：无需安装驱动、无需账号，纯本地执行。

## 🚀 使用方式

> **系统依赖**：本项目 UI 依赖 **Microsoft Edge WebView2 Runtime**。无论在开发环境中运行还是使用打包后的 `.exe`，如果系统未安装该运行时或运行时组件损坏，界面都可能显示异常或无法使用；请先安装或修复 WebView2 Runtime。

### 运行（开发环境）

```bash
pip install -r requirements.txt
python main.py
```

### 发送端（源 PC）

1. 打开"发送"页，输入文本或点"选择文件"。
2. 默认使用稳定的 1×1 / 8 FPS；也可按窗口大小选择 2×2 / 3×3、纠错级和 FPS。
3. 点"开始广播"，二维码宫格开始循环播放。
4. 可随时暂停广播；继续时，序号未修改则从暂停位置继续，修改序号则从指定帧开始循环。
5. 广播中也可修改文本或换文件，点"发送"按钮即可**热切换**广播内容。
6. 文本框按 Enter 直接发送，Shift+Enter 换行；发送后自动清空。
7. 点击二维码上方的折叠按钮可进入专注模式，将主要窗口空间留给二维码；再次点击恢复控制区。
8. 接收端复制缺失序号后，展开“缺失序号补发”，直接粘贴 `181, 283, 546-559` 并点击“补发缺失帧”；收齐后可恢复全部帧。

### 接收端（目标 PC）

1. 打开"接收"页，从下拉列表中选择发送端所在的窗口（点"↻ 刷新"更新列表）。
2. 点"② 开始接收"，进度条实时递增，直到集齐所有帧。
3. 完成后：
   - **文件**：自动保存到系统 `Downloads` 目录；重名文件自动追加 `(1)`、`(2)`。
   - **文本消息**：静默写入系统剪贴板，同时在下方消息列表逐条展示（最新在最上），每条旁有 📋 复制按钮。
4. 暂停接收会保留已收帧和临时文件，继续后复用原任务；只有“重置任务”才清理进度。
5. 可查看并复制当前缺失的序号区间，发送端据此从指定序号补发。

> **后台运行**：锁定窗口后，即使发送端窗口被遮挡或在后台，也能持续接收（发送端窗口不要最小化）。

## ✨ 技术亮点

* **QR 字节模式 + 原始二进制**：不再 Base64，单帧体积直接省 33%。
* **XOR keystream 加扰**：解决 ZBar 按内容自动猜字符集导致的二进制损坏问题，全字节值域 100% 可逆。
* **N×N 宫格并发**：同屏渲染多个二维码，pyzbar 单次捕获解出全部，吞吐 ×N。
* **懒渲染**：Sender 构建瞬间完成（5MB 文件 4ms），QR 编码分摊到后台广播线程，UI 永不阻塞。
* **窗口捕获 (PrintWindow)**：用 `PW_RENDERFULLCONTENT` 抓取指定窗口内容，即使被遮挡也能持续接收。
* **边收边落盘**：接收端按 fileSize 预分配临时文件，数据帧按偏移 `seek+write`，用位图记录进度，大文件不吃内存。
* **SHA-1 校验**：完成后比对整文件哈希，不匹配则继续等待重传。
* **现代化界面**：pywebview + 本地 HTML/CSS/JS（无 CDN 依赖），响应式控制区优先为二维码保留空间，离线可用。

## 🛠️ 传输协议

自定义二进制帧，经 XOR 加扰后写入 QR byte 模式：

* **Meta 帧**：`magic | ver | type | taskId | flags | totalFrames | chunkSize | fileSize | nameLen | name | sha1`
* **Data 帧**：`magic | ver | type | taskId | frameIndex | payload(原始文件字节)`

发送端循环广播并周期性注入 meta；接收端随时加入都能拿到元信息并按索引落盘重组。

## 📦 打包 exe

```bash
build.bat
```

产物 `dist\AirScan-QR.exe`，双击即用，无需 Python 环境。打包包含：
- PyInstaller 内置的精简 Python 运行时与标准库
- `--icon` 嵌入 QR 主题蓝色图标
- `--add-data` 打入 ui.html / ui.css / ui.js 界面资源
- `--collect-binaries pyzbar` 打包 ZBar 原生 DLL
- `--collect-all webview` 打包 pywebview JS bridge
- `--hidden-import` 打包 pywin32 窗口捕获模块

> **注意**：打包前须关闭正在运行的 exe，否则文件被占用会导致打包失败。

## 📁 项目结构

```
main.py              # 入口
airscan/
  __init__.py
  protocol.py        # 二进制帧、XOR 加扰、QR 编解码、切片、sha1
  sender.py          # 懒渲染切片、宫格合成、循环广播
  receiver.py        # 解码、落盘重组、连传
  storage.py         # Downloads 自动保存、重名处理、系统剪贴板
  wincap.py          # 窗口捕获 (PrintWindow, 支持后台抓窗)
  app.py             # pywebview 主窗口 + js_api 桥
  ui.html            # 界面结构 (无 CDN)
  ui.css             # 响应式样式
  ui.js              # pywebview 前端交互
  icon.ico           # 应用图标
requirements.txt
build.bat
archive/             # 旧的纯浏览器 HTML 版本 (仅存档)
```

## 📜 许可证

MIT License © [Jack/topcss]

---

> 旧的纯浏览器版本 (`index.html` / `airscan-fountain.html` 等) 已移至 `archive/` 仅作存档。
