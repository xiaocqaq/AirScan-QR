# AirScan-QR 桌面版 📡

**AirScan-QR** 是一款专为**物理隔离环境 (Air-gapped)** 及 **跨端受限网络** 设计的文件传输工具。通过"屏幕显示动态二维码流 + 窗口捕获解码"的视觉链路，在无网络、无 U 盘、无蓝牙的场景下把文件从一台 PC "隔空"传到另一台 PC。

本版本为**原生 Python 桌面应用**（pywebview + HTML 现代化界面），可打包成免环境 `.exe`，专注 **PC → PC** 传输。

## 🌟 核心场景

* **封闭开发/实验室**：从物理隔离的内网 PC 取出日志、代码、小文件。
* **远程桌面 / VPN 穿透**：接收端锁定发送端窗口，把文件从远程服务器"拿"回本地。
* **无痕传输**：无需安装驱动、无需账号，纯本地执行。

## 🚀 使用方式

### 运行（开发环境）

```bash
pip install -r requirements.txt
python main.py
```

### 发送端（源 PC）

1. 打开"发送"页，输入文本或点"选择文件"。
2. 选宫格（1×1 / 2×2 / 3×3，越大单位时间吞吐越高）、纠错级、FPS。
3. 点"开始广播"，二维码宫格开始循环播放。
4. 广播中可直接修改文本或换文件，点"发送"按钮即可**热切换**广播内容（无需先停止）。
5. 发送文本后输入框自动清空，方便连续发送。

### 接收端（目标 PC）

1. 打开"接收"页，从下拉列表中选择发送端所在的窗口（点"↻ 刷新"更新列表）。
2. 点"② 开始接收"，进度条实时递增，直到集齐所有帧。
3. 完成后：
   - **文件**：弹出另存对话框，选保存位置。
   - **文本消息**：静默写入系统剪贴板，同时在下方消息列表逐条展示（最新在最上），每条旁有 📋 复制按钮。
4. 完成后**不停止捕获**，发送端换新文件再次广播时，接收端自动识别新任务并开始下一轮（连传）。

> **后台运行**：锁定窗口后，即使发送端窗口被遮挡或在后台，也能持续接收（发送端窗口不要最小化）。

## ✨ 技术亮点

* **QR 字节模式 + 原始二进制**：不再 Base64，单帧体积直接省 33%。
* **XOR keystream 加扰**：解决 ZBar 按内容自动猜字符集导致的二进制损坏问题，全字节值域 100% 可逆。
* **N×N 宫格并发**：同屏渲染多个二维码，pyzbar 单次捕获解出全部，吞吐 ×N。
* **懒渲染**：Sender 构建瞬间完成（5MB 文件 4ms），QR 编码分摊到后台广播线程，UI 永不阻塞。
* **窗口捕获 (PrintWindow)**：用 `PW_RENDERFULLCONTENT` 抓取指定窗口内容，即使被遮挡也能持续接收。
* **边收边落盘**：接收端按 fileSize 预分配临时文件，数据帧按偏移 `seek+write`，用位图记录进度，大文件不吃内存。
* **SHA-1 校验**：完成后比对整文件哈希，不匹配则继续等待重传。
* **现代化界面**：pywebview + 内联 CSS（无 CDN 依赖），圆角卡片、蓝色主题按钮、胶囊标签页，离线可用。

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
- `--icon` 嵌入 QR 主题蓝色图标
- `--add-data` 打入 ui.html 界面
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
  wincap.py          # 窗口捕获 (PrintWindow, 支持后台抓窗)
  app.py             # pywebview 主窗口 + js_api 桥
  ui.html            # 现代化界面 (内联 CSS, 无 CDN)
  icon.ico           # 应用图标
requirements.txt
build.bat
archive/             # 旧的纯浏览器 HTML 版本 (仅存档)
```

## 📜 许可证

MIT License © [Jack/topcss]

---

> 旧的纯浏览器版本 (`index.html` / `airscan-fountain.html` 等) 已移至 `archive/` 仅作存档。
