# AirScan-QR 桌面版 📡

**AirScan-QR** 是一款专为**物理隔离环境 (Air-gapped)** 及 **跨端受限网络** 设计的文件传输工具。通过"屏幕显示动态二维码流 + 屏幕录制解码"的视觉链路，在无网络、无 U 盘、无蓝牙的场景下把文件从一台 PC "隔空"传到另一台 PC。

本版本为**原生 Python 桌面应用**（pywebview + HTML 界面），可打包成免环境 `.exe`，专注 **PC → PC** 传输。

## 🌟 核心场景

* **封闭开发/实验室**：从物理隔离的内网 PC 取出日志、代码、小文件。
* **远程桌面 / VPN 穿透**：直接框选远程窗口里的二维码，把文件从远程服务器"拿"回本地。
* **无痕传输**：无需安装驱动、无需账号，纯本地执行。

## 🚀 使用方式

### 运行（开发环境）

```bash
pip install -r requirements.txt
python main.py
```

### 发送端（源 PC）

1. 打开"发送"页，输入文本或点"选文件"。
2. 选宫格（1×1 / 2×2 / 3×3，越大单位时间吞吐越高）、纠错级、FPS。
3. 点"开始广播"，二维码宫格开始循环播放。

### 接收端（目标 PC）

1. 打开"接收"页，点"① 框选区域"，拖拽框住发送端二维码所在的屏幕区域（可跨窗口、远程桌面）。
2. 点"② 开始接收"，进度实时递增，直到集齐所有帧。
3. 完成后：
   - **文件**：弹出另存对话框，选保存位置。
   - **文本消息**：直接写入系统剪贴板，不生成 txt。
4. 完成后**不关闭捕获**，发送端换新文件再次广播时，接收端自动识别并开始下一轮（连传）。

## ✨ 技术亮点

* **QR 字节模式 + 原始二进制**：不再 Base64，单帧体积直接省 33%。
* **XOR keystream 加扰**：解决 ZBar 按内容自动猜字符集导致的二进制损坏问题，全字节值域 100% 可逆（500+ 随机样本 + 对抗样本验证）。
* **N×N 宫格并发**：同屏渲染多个二维码，pyzbar 单次捕获解出全部，吞吐 ×N。
* **边收边落盘**：接收端按 fileSize 预分配临时文件，数据帧按偏移 `seek+write`，用位图记录进度，大文件不吃内存。
* **SHA-1 校验**：完成后比对整文件哈希，不匹配则继续等待重传。

## 🛠️ 传输协议

自定义二进制帧，经 XOR 加扰后写入 QR byte 模式：

* **Meta 帧**：`magic | ver | type | taskId | flags | totalFrames | chunkSize | fileSize | nameLen | name | sha1`
* **Data 帧**：`magic | ver | type | taskId | frameIndex | payload(原始文件字节)`

发送端循环广播并周期性注入 meta；接收端随时加入都能拿到元信息并按索引落盘重组。

## 📦 打包 exe

```bash
build.bat
```

产物 `dist\AirScan-QR.exe`，双击即用，无需 Python 环境。脚本用 PyInstaller `--onefile --windowed`，并 `--collect-binaries pyzbar` 确保 ZBar 原生 DLL 被打包。

## 📁 项目结构

```
main.py            # 入口
airscan/
  protocol.py      # 二进制帧、XOR 加扰、QR 编解码、切片、sha1
  sender.py        # 切片、宫格合成、循环广播
  receiver.py      # 屏幕捕获、解码、落盘重组、连传
  app.py           # tkinter 主窗口
requirements.txt
build.bat
archive/           # 旧的纯浏览器 HTML 版本 (仅存档)
```

## 📜 许可证

MIT License © [Jack/topcss]

---

> 旧的纯浏览器版本 (`index.html` / `airscan-fountain.html` 等) 已移至 `archive/` 仅作存档。
