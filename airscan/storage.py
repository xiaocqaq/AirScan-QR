"""接收文件的默认目录、命名冲突与流式保存。"""
import os
import re
from pathlib import Path


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_COPY_BUFFER_SIZE = 1024 * 1024


def default_download_dir(home=None) -> Path:
    directory = Path(home) / "Downloads" if home else Path.home() / "Downloads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def set_clipboard(text: str):
    import win32clipboard

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text)
    finally:
        win32clipboard.CloseClipboard()


def sanitize_filename(name: str) -> str:
    leaf = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _INVALID_FILENAME.sub("_", leaf).strip().rstrip(".")
    if not cleaned:
        return "received.bin"
    if Path(cleaned).stem.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def unique_destination(directory: Path, name: str) -> Path:
    directory = Path(directory)
    safe_name = sanitize_filename(name)
    candidate = directory / safe_name
    if not candidate.exists():
        return candidate
    path = Path(safe_name)
    counter = 1
    while True:
        candidate = directory / f"{path.stem} ({counter}){path.suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_received_file(source, file_size: int, name: str, directory=None) -> Path:
    target_dir = Path(directory) if directory else default_download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(target_dir, name)
    remaining = file_size
    try:
        with open(source, "rb") as src, open(destination, "xb") as dst:
            while remaining:
                chunk = src.read(min(_COPY_BUFFER_SIZE, remaining))
                if not chunk:
                    raise IOError("接收临时文件长度不足")
                dst.write(chunk)
                remaining -= len(chunk)
    except Exception:
        if destination.exists():
            os.remove(destination)
        raise
    return destination
