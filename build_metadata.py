"""Build-time metadata helpers."""

import re
from pathlib import Path


_PRODUCT_VERSION = re.compile(
    r'StringStruct\(\s*["\']ProductVersion["\']\s*,\s*["\']([^"\']+)["\']\s*\)'
)
_VALID_VERSION = re.compile(r"\d+(?:\.\d+){2,3}")


def product_version(version_file):
    text = Path(version_file).read_text(encoding="utf-8")
    match = _PRODUCT_VERSION.search(text)
    if not match:
        raise ValueError(f"ProductVersion not found in {version_file}")
    version = match.group(1)
    if not _VALID_VERSION.fullmatch(version):
        raise ValueError(f"Invalid ProductVersion: {version}")
    return version


def executable_name(product_name, version_file):
    return f"{product_name}-{product_version(version_file)}"
