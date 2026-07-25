# AirScan-QR 文件夹增量同步（宿主机 → 云桌面）

> 目标：把宿主机上的代码文件夹增量同步到云桌面，同步后两端文件夹**完全一致**（含删除）。
> 每次改动不多，走"清单比对 + 只传变更"的类 git 增量模式。

## 1. 通道模型（关键约束）

| 方向 | 载体 | 能力 | 本方案用途 |
|------|------|------|-----------|
| 云 → 宿主机 | 二维码（云端窗口显示 QR，宿主机截屏解码） | **弱**（低带宽） | 只传一份很小的**清单**（gzip 后通常几 KB） |
| 宿主机 → 云 | 剪贴板 / RDP 文件夹粘贴 | **强**（可直接粘贴整个文件夹） | 直接粘贴一个**变更输出文件夹** `sync-out/` |

强弱分布与增量同步天然契合：**弱通道只承载小体量的清单元信息，强通道直接搬运真正的文件**。经实测，宿主机→云桌面可直接粘贴文件夹，因此不再需要 zip 打包/解包，云端"应用"只做删除传播与一致性校正。

## 2. 角色与主方向

- **宿主机 = 源（Source of Truth）**：代码在这里改，向云端 **推送（push）**。
- **云桌面 = 目标**：接收变更、覆盖、删除多余文件，向宿主机看齐。
- 反向（云 → 宿主机拉取大量数据）会让大数据走弱 QR 通道，超出本期范围，列为二期。

## 3. 同步流程（一个完整回合）

```
[云]  1. 选目标文件夹 → 扫描 → 生成清单(gzip) → 走现有 QR 发送管线广播
        ↓ (QR 弱通道)
[宿主机] 2. 用现有窗口捕获收下云端清单
         3. 选本地源文件夹 → 扫描 → 与云端清单做 diff(相对路径+大小+mtime)
            → 得到 to_send(新增/修改) 和 to_delete(云端多余)
         4. 生成输出文件夹 sync-out/:
              - 变更文件按原相对路径复制进去
              - 写 .airscan-sync/plan.json (删除清单 + 宿主机完整清单 + sha1)
         5. 打开 sync-out/ → 用户全选复制
        ↓ (剪贴板/RDP 文件夹粘贴，强通道)
[云]  6. 把 sync-out/ 里的内容整份粘到云端项目文件夹(覆盖变更文件, .airscan-sync 一起进来)
         7. app "应用同步" 指向云端项目文件夹:
              - 读 .airscan-sync/plan.json
              - to_delete 文件移入 .airscan-sync-backup/ (带备份, 非物理删)
              - 按宿主机清单 os.utime 校正每个文件 mtime
              - 重新扫描比对宿主机清单 → 报告"文件一致 / 不一致列表"
              - 清掉 .airscan-sync 标记
```

一个回合：云端播一次清单（QR，很快）+ 宿主机粘一次文件夹 + 云端点一次"应用同步"。

## 4. 差异判定

- 判据：**相对路径 + 文件大小 + 修改时间(mtime_ns)**（此前选择：大小+时间，快）。
- `to_send`：路径在宿主机存在，且（云端没有 或 大小/mtime 不同）。
- `to_delete`：路径在云端存在，宿主机没有 → 云端需删除。
- 权衡：大小+mtime 极快；极少数"大小和时间都恰好相同、内容不同"会漏判，二期可加可选 SHA-1 精确模式。

**mtime 一致性处理（关键）**：用户手动粘贴文件后，Windows 会把 mtime 设为复制时刻，导致下一回合云端清单的 mtime 与宿主机对不上、被误判为"又变了"重复传。因此"应用同步"步骤按 `plan.json` 里宿主机清单的 `mtime_ns` 用 `os.utime` 校正每个已同步文件，保证下一回合 diff 干净。

## 5. 数据结构

**清单（Manifest）** — gzip(JSON)，走 QR：
```json
{ "v": 1, "root": "myproject", "files": [ ["src/app.py", 1234, 1737600000000000000], ... ] }
```
扫描忽略：`.git`、`__pycache__`、`node_modules`、`*.pyc` 等（可配置忽略表）。

**输出文件夹（sync-out/）** — 宿主机生成，用户直接粘贴：
```
src/app.py                    # 变更文件，按相对路径摆放
src/util/helper.py
.airscan-sync/plan.json       # { delete:[...], host_manifest:{...}, manifest_sha1:"..." }
```

## 6. 代码改动清单

### 新增 `airscan/foldersync.py`（纯逻辑，可单测）
- `scan_folder(root, ignore) -> dict[relpath, (size, mtime_ns)]`
- `serialize_manifest(files, root_name) -> bytes`（gzip JSON）
- `deserialize_manifest(blob) -> (root_name, files)`
- `diff_manifests(source, target) -> {"send": [...], "delete": [...]}`
- `build_sync_out(source_root, send, delete, source_files, out_dir)`（复制变更文件 + 写 plan.json）
- `apply_sync_out(target_root, backup_dir) -> report`（读 plan.json、删除进备份、mtime 校正、清标记）
- `verify(target_root, expected_files) -> (ok, mismatches)`

### `airscan/protocol.py`
- 新增 `FLAG_SYNC = 0x02`（bit1：同步清单）。帧格式不变，payload 就是 gzip 清单字节，复用 `build_meta / build_data / slice_data`。

### `airscan/sender.py`
- `Sender` 增加 `is_sync=False` 参数 → flags 置 `FLAG_SYNC`。云端广播清单时用。

### `airscan/receiver.py`
- `Task` 读取 `is_sync = flags & FLAG_SYNC`。
- 完成回调时若 `is_sync`，把清单字节交给同步流程回调，而非文件另存 / 剪贴板。

### `airscan/app.py`（新增 js_api）
- 云端：`sync_broadcast_manifest(folder)` — 扫描目标文件夹并用现有发送管线广播清单。
- 宿主机：`sync_on_manifest(blob)`（内部：收到云端清单缓存起来）、`sync_compute_diff(local_folder)`（返回新增/修改/删除摘要）、`sync_build_output()`（生成 sync-out/ 文件夹，`os.startfile` 打开它，返回路径）。
- 云端：`sync_apply(target_folder)` — 执行删除+备份、mtime 校正、校验，返回一致性报告。
- 删除是破坏性操作：默认移入 `.airscan-sync-backup/`，UI 二次确认后才执行。

### UI（`ui.html` / `ui.js` / `ui.css`）
- 新增"文件夹同步"标签页，按角色分区：
  - **云端区**：选目标文件夹 →「广播清单」（触发 QR 广播）；底部「应用同步」→ 选云端项目文件夹 → 二次确认 → 显示一致性结果。
  - **宿主机区**：提示先在"接收"页锁定云端窗口收清单 → 选本地文件夹 →「计算差异」→ 列出新增/修改/删除 →「生成输出文件夹」→ 自动打开 sync-out/，提示"全选复制粘贴到云端，再在云端点应用同步"。

## 7. 一致性保证

- 宿主机清单 SHA-1 写进 `plan.json`；云端"应用同步"后重新扫描比对：一致 → "同步完成，文件一致"，不一致 → 列出差异路径（供排查）。
- 删除传播：默认移入备份目录（可恢复），非物理删除，满足"两端一致"又不丢数据。

## 8. 边界与二期

- **首次同步**：云端为空 → 清单为空 → 宿主机把整个文件夹放进 sync-out/（走强通道，直接粘）。
- **反向同步（云 → 宿主机）**：大数据要走弱 QR，二期评估（可复用现有分帧 + 补发）。
- **精确 diff**：二期可选 SHA-1 内容比对模式。
- **忽略表**：先内置常见忽略项，二期做成可配置 / 读 `.gitignore`。

## 9. 测试计划

- `tests/test_foldersync.py`：scan / serialize 往返 / diff（新增·修改·删除·忽略）/ build_sync_out + apply_sync_out 往返后 `verify` 通过 / mtime 校正 / 删除进备份。
- 复用现有 QR 编解码测试路径验证清单能过 QR 往返。
