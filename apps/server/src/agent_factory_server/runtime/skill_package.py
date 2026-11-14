"""Skill package (zip) validation and unpacking for factory-hosted skills.

Zip is the transfer format; storage is per-file rows. Scripts are stored but never
executed by the platform.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_FILES = 200
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_PATH_DEPTH = 8
PAGE_CHARS = 16000

DOC_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml"})
SCRIPT_EXTENSIONS = frozenset(
    {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1"}
)
_IGNORE_TOP_DIRS = frozenset({"__MACOSX"})
_IGNORE_NAMES = frozenset({".DS_Store"})
_SYMLINK_MODE = 0o120000
_FILE_TYPE_MASK = 0o170000


class PackageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def classify_path(path: str, mode: int = 0) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if mode & 0o111:
        return "script"
    if suffix in SCRIPT_EXTENSIONS:
        return "script"
    if suffix in DOC_EXTENSIONS:
        return "doc"
    return "asset"


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        return {}, text

    meta: dict[str, str] = {}
    block = lines[1:end]
    index = 0
    while index < len(block):
        line = block[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            style = value[0]
            collected: list[str] = []
            while index < len(block):
                nxt = block[index]
                if nxt.strip() and nxt[:1] not in (" ", "\t"):
                    break
                collected.append(nxt.strip())
                index += 1
            while collected and not collected[-1]:
                collected.pop()
            meta[key] = ("\n" if style == "|" else " ").join(collected).strip()
        else:
            meta[key] = _strip_quotes(value)
    return meta, "\n".join(lines[end + 1 :])


def _safe_rel_path(raw_name: str) -> str | None:
    name = (raw_name or "").replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute():
        return None
    parts = [part for part in path.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    if any(":" in part for part in parts):
        return None
    if len(parts) > MAX_PATH_DEPTH:
        return None
    return "/".join(parts)


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def unpack_skill_zip(raw: bytes) -> dict[str, Any]:
    """Validate and unpack a skill zip into memory.

    Returns:
      {
        name, description, version, warnings,
        files: [{path, kind, is_binary, text_content?, blob?, bytes, sha256, mode}]
      }
    """
    if not raw:
        raise PackageError("empty_upload", "empty zip upload")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise PackageError("invalid_zip", f"not a valid zip file: {exc}") from exc

    members: list[tuple[str, str, int, int]] = []
    total_bytes = 0
    compressed_bytes = 0

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            raw_mode = (info.external_attr >> 16) & 0xFFFF
            if raw_mode & _FILE_TYPE_MASK == _SYMLINK_MODE:
                raise PackageError(
                    "symlink_forbidden",
                    f"package contains a symlink: {info.filename!r}",
                )
            rel = _safe_rel_path(info.filename)
            if rel is None:
                raise PackageError("unsafe_path", f"unsafe path in package: {info.filename!r}")
            parts = rel.split("/")
            if parts[0] in _IGNORE_TOP_DIRS or PurePosixPath(rel).name in _IGNORE_NAMES:
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise PackageError(
                    "file_too_large",
                    f"{rel} is {info.file_size} bytes; per-file limit is {MAX_FILE_BYTES}",
                )
            total_bytes += info.file_size
            compressed_bytes += info.compress_size
            members.append((info.filename, rel, info.file_size, raw_mode & 0o777))

        if not members:
            raise PackageError("empty_package", "package contains no files")
        if len(members) > MAX_FILES:
            raise PackageError(
                "too_many_files",
                f"package has {len(members)} files; limit is {MAX_FILES}",
            )
        if total_bytes > MAX_TOTAL_BYTES:
            raise PackageError(
                "package_too_large",
                f"unpacked size {total_bytes} bytes exceeds {MAX_TOTAL_BYTES}",
            )
        ratio = total_bytes / max(compressed_bytes, 1)
        if ratio > MAX_COMPRESSION_RATIO:
            raise PackageError(
                "suspicious_compression",
                f"compression ratio {ratio:.0f}x looks like a zip bomb",
            )

        first_parts = {rel.split("/")[0] for _, rel, _, _ in members}
        prefix = ""
        if len(first_parts) == 1:
            only = next(iter(first_parts))
            if f"{only}/SKILL.md" in {rel for _, rel, _, _ in members}:
                prefix = f"{only}/"

        files: list[dict[str, Any]] = []
        skill_md_text = ""
        for zip_name, rel, _size, mode in members:
            pkg_path = rel[len(prefix) :] if prefix else rel
            if not pkg_path:
                continue
            data = archive.read(zip_name)
            is_binary = _looks_binary(data)
            kind = classify_path(pkg_path, mode)
            entry: dict[str, Any] = {
                "path": pkg_path,
                "kind": kind,
                "is_binary": is_binary,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mode": f"{mode:03o}" if mode else None,
            }
            if is_binary:
                entry["blob"] = data
                entry["text_content"] = None
            else:
                entry["text_content"] = data.decode("utf-8")
                entry["blob"] = None
            if pkg_path == "SKILL.md":
                if is_binary:
                    raise PackageError("unreadable_skill_md", "SKILL.md must be UTF-8 text")
                skill_md_text = entry["text_content"] or ""
            files.append(entry)

        if not any(f["path"] == "SKILL.md" for f in files):
            raise PackageError("missing_skill_md", "package must contain SKILL.md at its root")

    meta, _ = parse_frontmatter(skill_md_text)
    declared = str(meta.get("name") or "").strip()
    dir_name = prefix.rstrip("/") if prefix else ""
    name = declared or dir_name
    if not SKILL_NAME_RE.match(name or ""):
        raise PackageError(
            "invalid_name",
            f"skill name must match {SKILL_NAME_RE.pattern}; got {name!r}",
        )

    description = str(meta.get("description") or "").strip()
    warnings: list[str] = []
    if declared and dir_name and declared != dir_name:
        warnings.append(
            f"frontmatter name {declared!r} differs from directory {dir_name!r}; "
            f"installing as {declared!r}"
        )
    if not description:
        warnings.append(
            "frontmatter has no description; catalog entry will be empty"
        )

    return {
        "name": name,
        "description": description,
        "version": str(meta.get("version") or "").strip() or None,
        "warnings": warnings,
        "total_bytes": sum(int(f["bytes"]) for f in files),
        "files": files,
    }
