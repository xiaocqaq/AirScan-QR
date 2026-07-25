# 基于 Git 变动的文件夹同步 (替换清单/QR 方案)

## 背景与动机

当前文件夹同步流程:
1. 云端扫描全量文件生成清单 → QR 弱通道广播
2. 宿主机锁定云端窗口收清单
3. 宿主机选源文件夹 → 与云端清单 diff → 生成输出文件夹
4. 用户粘贴输出文件夹到云端
5. 云端应用同步

痛点(用户反馈):全量扫描会把隐藏文件、`.git` 内部文件、build 产物等噪音混进来,两端内外不一致,且流程重。

核心洞察:**git 本身就是"谁变了"的权威来源**,且天然尊重 `.gitignore`。用 `git status` 判断变动后,**宿主机本地即可确定变动集,不再需要云端广播清单、也不需要 QR 弱通道** —— 直接砍掉旧流程的 1、2 步。

## 用户已确认的决策

- 变动基准:**未提交的改动**(工作区 vs HEAD)
- 未跟踪的新文件(未 git add、也未被 .gitignore 忽略):**要一起同步**

这正好对应 `git status --porcelain -z -uall` 的完整输出。

## 新流程

1. **宿主机(源)**:选 git 仓库 → 点"生成变动文件夹" → 用 `git status` 列出变动文件,按相对路径复制进输出文件夹 + 写 `plan.json`(删除清单 + 变动文件的 size/mtime)→ 自动打开
2. 用户把输出文件夹拖/粘贴到云端(RDP 强通道)
3. **云端(目标)**:选目标仓库 → 拖入/手动选输出文件夹 → 点"应用同步":变动文件覆盖到同层级路径、删除的文件移入 `.airscan-sync-backup` 备份、按清单校正 mtime

## 实现方案

### 1. `airscan/foldersync.py` — 新增 git 逻辑

```python
def git_changed_files(repo_root) -> dict:
    """基于 git status --porcelain -z -uall 返回 {send, delete}。
    - send: 当前存在于工作区的变动文件(改/增/未跟踪)
    - delete: 已被删除的文件
    对每个路径按"磁盘上是否存在"分类, 天然覆盖改/增/删/重命名, 与 XY 码顺序无关。
    尊重 .gitignore (git status 默认不显示被忽略文件)。
    """
```
- 用 `subprocess.run`,Windows 下加 `creationflags=CREATE_NO_WINDOW`(0x08000000)避免弹控制台窗口
- git 可执行:`shutil.which("git")` 否则回退 `"git"`
- 解析 `-z` 输出:按 `\0` 切分,每条 `XY <path>`;遇 R/C 再读一个 `\0` 字段作为原路径
- 对 `path` 与 `orig`(若有)逐个判断 `os.path.exists` → 存在归 send、不存在归 delete
- 错误处理:非 git 仓库、git 未安装分别返回清晰错误

```python
def git_build_output(repo_root, out_root) -> dict:
    """宿主机: git 变动 → 复制 send 文件进 out_root + 写 plan.json(partial=True)。"""
```
- 复用现有 `build_output_folder` 的复制/写 plan 逻辑,但 `manifest` 只含变动文件,plan 增加 `"partial": True` 标记

### 2. `apply_sync` / `verify` — 支持部分清单

- `verify(target_root, expected_manifest, check_extra=True)` 新增参数
- `apply_sync` 读 plan 的 `partial` 标记;partial 时 `check_extra=False`(否则目标里所有未变动文件会被误报为 "extra")
- mtime 校正只作用于清单内(变动)文件 —— 已是现有行为

保留 `scan_folder / serialize_manifest / deserialize_manifest / diff_manifests / manifest_sha1 / build_output_folder`(被测试与内部复用,不动)。

### 3. `airscan/app.py` — API 调整

新增/保留:
- `sync_git_build(repo_folder)`:校验是 git 仓库 → `git_build_output` → `os.startfile` 打开 → 返回摘要(变动数、删除数、out_root)
- 保留 `sync_pick_folder`、`sync_resolve_dropped`、`sync_apply`(apply 内部走 partial 校验)

移除(新流程不再需要):
- `sync_broadcast_manifest`、`sync_compute_diff`、旧 `sync_build_output`
- `_on_complete` 里的 `is_sync` 清单接收分支、`_sync_cloud_manifest` 字段
- `_build_and_send` 里的 `("sync", ...)` 广播分支

保留不动(有测试覆盖,避免连带破坏):`protocol.FLAG_SYNC`、`sender`/`receiver` 的 is_sync 参数(变为不被 app 调用的惰性能力)。

### 4. UI — `ui.html` / `ui.js` / `ui.css`

重构同步面板为两栏、更短的流程:
- **宿主机(源)栏**:① 选 git 仓库 → ② "生成变动文件夹"(显示"改动 N 个 / 删除 M 个")
- **云端(目标)栏**:① 选目标仓库 → ② 拖入输出文件夹(保留现有 `syncDropZone` 拖放,真实路径解析已实现)或手动选 → ③ "应用同步"
- 顶部流程提示改写为 git 版
- 删除:广播清单、接收页锁窗收清单、计算差异等旧步骤的 DOM 与 JS 函数(`syncBroadcast`、`syncDiff`、`syncBuild`、`onSyncManifest`)
- `ui.css`:同步面板样式基本可复用(`.sync-columns`/`.sync-role-card`/`.sync-step`/`.sync-drop-zone`),按新 DOM 微调

### 5. 测试 — `tests/test_foldersync.py`

- 新增 `GitChangedFilesTests`:`git init` 临时仓库、配置 user、提交基线,然后改文件/加新文件/删文件,断言 `git_changed_files` 的 send/delete 正确;`shutil.which("git")` 不存在时 `skipTest`
- 新增 `git_build_output` + `apply_sync`(partial)端到端:构建输出 → 应用到目标 → 断言变动文件落到同层级、删除文件进备份、partial 下 verify 不误报 extra
- 现有测试保持绿色(未删除被依赖的纯函数)

## 影响范围与风险

- 影响文件:`foldersync.py`、`app.py`、`ui.html`、`ui.js`、`ui.css`、`test_foldersync.py`
- 风险点:
  - 打包后 exe 调用 git:git 需在云端/宿主机 PATH 上(用户环境已确认有 git 2.53)
  - 子模块、`git status` 特殊状态(冲突 U 码)不在本次处理范围 —— 冲突文件按"存在即 send"处理,可接受
- 不改:QR 传输链路、发送/接收、协议(仅停用 app 层的 sync 广播)

## 验证

- `python -m pytest tests/ -q` 全绿
- 从源码启动,手动走一遍:选本仓库 → 生成变动文件夹 → 拖到"云端"栏 → 选目标仓库 → 应用同步 → 确认变动文件覆盖、`.gitignore` 噪音未被带入
- 通过后按需重新打包(版本号可留 1.5 或自行调整)
