"""AirScan-QR 文件夹增量同步 (纯逻辑, 不含 GUI / 传输).

角色与通道 (见 docs/superpowers/plans/2026-07-24-folder-sync.md):
- 云桌面 = 目标: 扫描本地文件夹生成小体量"清单", 走 QR 弱通道广播给宿主机。
- 宿主机 = 源: 收下云端清单, 与本地源文件夹做 diff (相对路径 + 大小 + mtime),
  生成一个"输出文件夹" (变更文件按原结构摆放 + .airscan-sync/plan.json)。
- 用户把输出文件夹整份粘贴 (RDP 强通道) 到云端, 云端"应用同步":
  覆盖变更文件 + 把多余文件移进备份 + 按清单校正 mtime + 校验一致性。

本模块只做纯文件系统与数据结构操作, 便于单测; QR 传输复用 protocol/sender/receiver。
"""
import fnmatch
import gzip
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime

MANIFEST_VERSION = 1

# 同步元数据在输出文件夹里的固定位置。
SYNC_DIR = ".airscan-sync"
PLAN_NAME = "plan.json"
PLAN_PATH = f"{SYNC_DIR}/{PLAN_NAME}"

# 应用同步时, 多余文件移入此备份目录 (相对目标根), 不物理删除。
BACKUP_DIR = ".airscan-sync-backup"

# 扫描默认忽略的目录名与通配 (代码同步场景常见噪音)。
DEFAULT_IGNORE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".idea", ".vscode",
    ".airscan-sync", ".airscan-sync-backup", "dist", "build",
    ".pytest_cache", ".mypy_cache", "venv", ".venv",
})
DEFAULT_IGNORE_GLOBS = ("*.pyc", "*.pyo", "*.tmp", "*.swp", "*~")

# Windows 下运行 git 时不弹出控制台窗口。
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git_exe() -> str:
    return shutil.which("git") or "git"


def _run_git(repo_root: str, *args) -> str:
    """在 repo_root 里跑 git, 返回 stdout (bytes 解码为 utf-8)。失败抛异常。"""
    proc = subprocess.run(
        [_git_exe(), "-C", repo_root, *args],
        capture_output=True,
        creationflags=_CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"git {' '.join(args)} 失败 (code {proc.returncode})")
    return proc.stdout.decode("utf-8", "replace")


def is_git_repo(repo_root: str) -> bool:
    """repo_root 是否在一个 git 工作区内。"""
    try:
        out = _run_git(repo_root, "rev-parse", "--is-inside-work-tree")
        return out.strip() == "true"
    except Exception:
        return False


def git_changed_files(repo_root: str) -> dict:
    """基于 git status 返回工作区相对 HEAD 的变动。

    返回 {"send": [rel...], "delete": [rel...]} 均升序、正斜杠。
    - send: 当前存在于工作区的变动文件 (修改/新增/未跟踪/重命名后的新名)
    - delete: 已从工作区删除的文件 (含重命名的旧名)
    尊重 .gitignore (git status 默认不列被忽略文件)。-uall 展开未跟踪目录里的每个文件。
    """
    repo_root = os.path.abspath(repo_root)
    # -z: NUL 分隔且不转义路径 (中文/空格安全); --porcelain: 稳定机器格式。
    raw = _run_git(repo_root, "status", "--porcelain", "-z", "-uall")
    fields = raw.split("\0")
    send, delete = set(), set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        if not entry:
            i += 1
            continue
        # 每条: "XY <path>"; XY 为两字符状态码, 随后空格, 再路径。
        status = entry[:2]
        path = entry[3:]
        i += 1
        # 重命名/复制 (R/C): 紧跟一个 NUL 字段是原路径。
        if status and status[0] in ("R", "C"):
            orig = fields[i] if i < len(fields) else ""
            i += 1
            if orig:
                # 原名在工作区已不存在 (重命名) -> 删除旧名。
                full_orig = os.path.join(repo_root, orig.replace("/", os.sep))
                if not os.path.exists(full_orig):
                    delete.add(_norm_rel(orig))
        # 按磁盘是否存在归类: 存在=需推送, 不存在=已删除。
        full = os.path.join(repo_root, path.replace("/", os.sep))
        if os.path.exists(full):
            if os.path.isfile(full):
                send.add(_norm_rel(path))
        else:
            delete.add(_norm_rel(path))
    return {"send": sorted(send), "delete": sorted(delete)}


def git_build_output(repo_root: str, out_root: str) -> dict:
    """宿主机: 用 git 变动生成待粘贴的输出文件夹 (partial plan)。

    - send 文件按相对路径复制进 out_root (保留目录结构)。
    - 写 .airscan-sync/plan.json: 删除清单 + 变动文件的 size/mtime 清单 + partial 标记。
    返回摘要 dict。
    """
    repo_root = os.path.abspath(repo_root)
    diff = git_changed_files(repo_root)
    # 只对 send 文件建 size/mtime 清单 (供云端校正 mtime + 部分校验)。
    manifest = {}
    for rel in diff["send"]:
        full = os.path.join(repo_root, rel.replace("/", os.sep))
        try:
            st = os.stat(full)
        except OSError:
            continue
        manifest[rel] = (st.st_size, st.st_mtime_ns)
    root_name = os.path.basename(os.path.normpath(repo_root))
    summary = build_output_folder(repo_root, out_root, diff, manifest,
                                  root_name=root_name, partial=True)
    return summary


def _norm_rel(path: str) -> str:
    """统一相对路径为正斜杠, 便于跨端一致比较与 JSON 序列化。"""
    return path.replace("\\", "/")


def _is_ignored(rel_path: str, name: str, ignore_globs) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in ignore_globs)


def scan_folder(root: str, ignore_dirs=None, ignore_globs=None) -> dict:
    """扫描文件夹, 返回 {相对路径: (size, mtime_ns)}。

    相对路径统一用正斜杠; 忽略指定目录与通配文件。
    """
    ignore_dirs = set(ignore_dirs if ignore_dirs is not None else DEFAULT_IGNORE_DIRS)
    ignore_globs = tuple(ignore_globs if ignore_globs is not None else DEFAULT_IGNORE_GLOBS)
    root = os.path.abspath(root)
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # 就地裁剪要进入的子目录 (os.walk 允许修改 dirnames)。
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        for name in filenames:
            if _is_ignored(dirpath, name, ignore_globs):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = _norm_rel(os.path.relpath(full, root))
            result[rel] = (st.st_size, st.st_mtime_ns)
    return result


def manifest_sha1(manifest: dict) -> str:
    """对清单内容 (与顺序无关) 求稳定 SHA-1, 用于两端一致性校验。"""
    h = hashlib.sha1()
    for rel in sorted(manifest):
        size, mtime_ns = manifest[rel]
        h.update(rel.encode("utf-8"))
        h.update(str(size).encode("ascii"))
        h.update(str(mtime_ns).encode("ascii"))
    return h.hexdigest()


def serialize_manifest(manifest: dict, root_name: str) -> bytes:
    """清单序列化为 gzip(JSON) 字节, 供 QR 承载 (小体量)。"""
    payload = {
        "v": MANIFEST_VERSION,
        "root": root_name,
        "files": [[rel, size, mtime_ns]
                  for rel, (size, mtime_ns) in sorted(manifest.items())],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw)


def deserialize_manifest(blob: bytes):
    """还原 serialize_manifest 的字节, 返回 (root_name, manifest)。"""
    raw = gzip.decompress(blob)
    payload = json.loads(raw.decode("utf-8"))
    manifest = {row[0]: (int(row[1]), int(row[2])) for row in payload.get("files", [])}
    return payload.get("root", ""), manifest


def diff_manifests(source: dict, target: dict) -> dict:
    """比对源(宿主机)与目标(云端)清单。

    - send: 源存在, 且(目标缺失 或 大小/mtime 不同) -> 需要推送。
    - delete: 目标存在, 源缺失 -> 云端多余, 需删除。
    返回 {"send": [rel...], "delete": [rel...]} 均升序。
    """
    send = []
    for rel, sig in source.items():
        target_sig = target.get(rel)
        if target_sig is None or target_sig != sig:
            send.append(rel)
    delete = [rel for rel in target if rel not in source]
    return {"send": sorted(send), "delete": sorted(delete)}


def build_output_folder(source_root: str, out_root: str, diff: dict,
                        source_manifest: dict, root_name: str = "",
                        partial: bool = False) -> dict:
    """在 out_root 生成待粘贴的输出文件夹。

    - diff["send"] 里的文件按相对路径复制进来 (保留目录结构)。
    - 写 .airscan-sync/plan.json: 删除清单 + 宿主机清单 + 清单 SHA-1。
    - partial=True: 清单只含变动文件 (git 模式), 云端校验不检查多余文件。
    返回摘要 dict。
    """
    source_root = os.path.abspath(source_root)
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)

    copied = 0
    for rel in diff["send"]:
        src = os.path.join(source_root, rel.replace("/", os.sep))
        dst = os.path.join(out_root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    plan = {
        "v": MANIFEST_VERSION,
        "root": root_name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "partial": bool(partial),
        "delete": list(diff["delete"]),
        "manifest": [[rel, size, mtime_ns]
                     for rel, (size, mtime_ns) in sorted(source_manifest.items())],
        "manifest_sha1": manifest_sha1(source_manifest),
    }
    plan_dir = os.path.join(out_root, SYNC_DIR)
    os.makedirs(plan_dir, exist_ok=True)
    with open(os.path.join(plan_dir, PLAN_NAME), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, separators=(",", ":"))

    return {
        "copied": copied,
        "delete_count": len(diff["delete"]),
        "out_root": out_root,
        "manifest_sha1": plan["manifest_sha1"],
        "partial": bool(partial),
    }


def read_plan(applied_root: str) -> dict:
    """从粘贴到云端的文件夹里读回 plan.json。"""
    plan_path = os.path.join(applied_root, SYNC_DIR, PLAN_NAME)
    with open(plan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _plan_manifest(plan: dict) -> dict:
    return {row[0]: (int(row[1]), int(row[2])) for row in plan.get("manifest", [])}


def apply_sync(applied_root: str, target_root: str) -> dict:
    """在云端应用同步: 粘贴进来的 applied_root -> 目标 target_root。

    步骤:
    1. 把 applied_root 里的变更文件 (除 .airscan-sync) 覆盖到 target_root。
    2. plan.delete 里的目标文件移入 target_root/.airscan-sync-backup (带备份, 不物理删)。
    3. 按宿主机清单校正每个文件 mtime (避免手动粘贴改时间导致下轮误判)。
    4. 重新扫描 target_root, 与宿主机清单比对, 返回一致性报告。
    """
    applied_root = os.path.abspath(applied_root)
    target_root = os.path.abspath(target_root)
    os.makedirs(target_root, exist_ok=True)
    plan = read_plan(applied_root)
    source_manifest = _plan_manifest(plan)

    # 1. 覆盖变更文件。
    applied = 0
    for dirpath, dirnames, filenames in os.walk(applied_root):
        # 不把同步元数据目录本身复制过去。
        if SYNC_DIR in dirnames:
            dirnames.remove(SYNC_DIR)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = _norm_rel(os.path.relpath(full, applied_root))
            if rel.startswith(SYNC_DIR + "/"):
                continue
            dst = os.path.join(target_root, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(full, dst)
            applied += 1

    # 2. 删除多余文件 -> 移入备份。
    backup_root = os.path.join(target_root, BACKUP_DIR)
    deleted = 0
    for rel in plan.get("delete", []):
        victim = os.path.join(target_root, rel.replace("/", os.sep))
        if not os.path.exists(victim):
            continue
        backup_dst = os.path.join(backup_root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(backup_dst), exist_ok=True)
        # 备份目标已存在时先清掉, 保证 move 成功。
        if os.path.exists(backup_dst):
            os.remove(backup_dst)
        shutil.move(victim, backup_dst)
        deleted += 1

    # 3. 按清单校正 mtime。
    corrected = 0
    for rel, (size, mtime_ns) in source_manifest.items():
        path = os.path.join(target_root, rel.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        try:
            st = os.stat(path)
            os.utime(path, ns=(st.st_atime_ns, mtime_ns))
            corrected += 1
        except OSError:
            pass

    # 4. 校验一致性。partial (git 变动) 清单只覆盖变动文件, 不能把目标里
    #    未变动的文件误判为 extra, 故 partial 时关闭 extra 检查。
    check_extra = not plan.get("partial", False)
    ok, mismatches = verify(target_root, source_manifest, check_extra=check_extra)
    return {
        "applied": applied,
        "deleted": deleted,
        "corrected": corrected,
        "ok": ok,
        "mismatches": mismatches,
        "expected_sha1": plan.get("manifest_sha1", ""),
        "actual_sha1": manifest_sha1(source_manifest) if ok else "",
    }


def verify(target_root: str, expected_manifest: dict, check_extra: bool = True):
    """重新扫描目标, 与期望清单比对。返回 (ok, mismatches)。

    mismatches: [{"path", "reason"}]  reason ∈ {missing, extra, size, mtime}。
    忽略 .airscan-sync-backup (备份目录不参与一致性判断)。
    check_extra=False 时不把清单外的文件报为 extra (partial/git 部分同步用:
    目标里大量未变动文件本就不在部分清单内, 不应误报)。
    """
    actual = scan_folder(target_root)
    mismatches = []
    for rel, sig in expected_manifest.items():
        actual_sig = actual.get(rel)
        if actual_sig is None:
            mismatches.append({"path": rel, "reason": "missing"})
        elif actual_sig[0] != sig[0]:
            mismatches.append({"path": rel, "reason": "size"})
        elif actual_sig[1] != sig[1]:
            mismatches.append({"path": rel, "reason": "mtime"})
    if check_extra:
        for rel in actual:
            if rel not in expected_manifest:
                mismatches.append({"path": rel, "reason": "extra"})
    return (not mismatches), mismatches
