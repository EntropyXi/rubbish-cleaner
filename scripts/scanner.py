"""Read-only junk inventory and classification with checkpoint/resume support."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import psutil


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib import core, platform


IS_WINDOWS = platform.IS_WINDOWS

RISK_ACTION_MAP = {
    "SAFE": "delete",
    "CAUTION": "quarantine",
    "ASK": "ask",
    "ELEVATED": "report-only",
}

CATEGORY_RISK_MAP = {
    "root-temps": "SAFE",
    "root-logs": "SAFE",
    "duplicate-archives": "ASK",
    "empty-dirs": "SAFE",
    "recycle-bin": "ASK",
    "root-suspicious": "CAUTION",
    "app-caches": "SAFE",
    "browser-caches": "SAFE",
    "gpu-shader": "SAFE",
    "dev-caches": "SAFE",
    "ide-caches": "SAFE",
    "crash-dumps": "SAFE",
    "thumbnail-cache": "SAFE",
    "user-temp": "SAFE",
    "elevated-system": "ELEVATED",
}

CATEGORY_LIST = [
    {
        "id": category,
        "risk": risk,
        "action": RISK_ACTION_MAP[risk],
    }
    for category, risk in CATEGORY_RISK_MAP.items()
]

_WINDOWS_ORDER = list(CATEGORY_RISK_MAP)
_POSIX_ORDER = [
    "root-temps",
    "dev-caches",
    "user-temp",
    "browser-caches",
    "ide-caches",
    "crash-dumps",
    "thumbnail-cache",
    "recycle-bin",
]
_WINDOWS_USER_CATEGORIES = {
    "browser-caches",
    "gpu-shader",
    "dev-caches",
    "ide-caches",
    "crash-dumps",
    "thumbnail-cache",
    "user-temp",
}
_POSIX_USER_CATEGORIES = set(_POSIX_ORDER) - {"root-temps"}
_CSV_HEADER = "Category|Risk|Path|SizeBytes|FileCount|Action"
_WATCHED_PROCESSES = {
    "chrome",
    "msedge",
    "wechat",
    "wechatapp",
    "weixin",
    "wegame",
    "steam",
    "pip",
    "npm",
}

# FM4: category -> owner-process specs. A category whose owner process is
# running is skipped ENTIRELY (never killed, never partially cleaned) at both
# scan time and clean time. "jetbrains*" is a prefix match. Categories absent
# here (root-temps, empty-dirs, ...) have no owner and are never gated.
CATEGORY_OWNER_PROCESSES = {
    "browser-caches": ["chrome", "msedge"],
    "app-caches": ["wechat", "weixin", "wechatapp"],
    "gpu-shader": [
        "steam",
        "wegame",
        "epicgameslauncher",
        "battle.net",
        "valorant",
        "cs2",
        "dota2",
        "overwatch",
        "apexlegends",
        "fortnite",
        "minecraft",
    ],
    "dev-caches": ["pip", "npm", "python", "node"],
    "ide-caches": ["jetbrains*", "zotero", "code"],
    "crash-dumps": [],
}

_OWNER_DISPLAY = {
    "chrome": "Chrome",
    "msedge": "Edge",
    "wechat": "微信",
    "weixin": "微信",
    "wechatapp": "微信",
    "steam": "Steam",
    "wegame": "WeGame",
    "epicgameslauncher": "Epic Games",
    "battle.net": "Battle.net",
    "valorant": "Valorant",
    "cs2": "CS2",
    "dota2": "Dota 2",
    "overwatch": "Overwatch",
    "apexlegends": "Apex Legends",
    "fortnite": "Fortnite",
    "minecraft": "Minecraft",
    "pip": "pip",
    "npm": "npm",
    "python": "Python",
    "node": "Node.js",
    "jetbrains*": "JetBrains IDE",
    "zotero": "Zotero",
    "code": "VS Code",
}

_CATEGORY_DISPLAY = {
    "browser-caches": "浏览器缓存",
    "app-caches": "应用缓存",
    "gpu-shader": "GPU 着色器缓存",
    "dev-caches": "开发工具缓存",
    "ide-caches": "IDE 缓存",
    "crash-dumps": "崩溃转储",
}

# FM5: execution action enum. Cache categories clean only their CONTENTS and
# keep the directory; empty-dirs removes only verified-empty directories.
# Everything else keeps the single-path delete/quarantine behavior.
CATEGORY_ACTION_MAP = {
    "app-caches": "clean_contents",
    "browser-caches": "clean_contents",
    "gpu-shader": "clean_contents",
    "dev-caches": "clean_contents",
    "ide-caches": "clean_contents",
    "crash-dumps": "clean_contents",
    "empty-dirs": "remove_if_empty",
}

# FM7: path semantic validation. A static-map (per-app-path-map) cache path
# whose sampled content carries a data-file suffix looks like live user data
# rather than junk — it is upgraded to CAUTION (quarantine), never SAFE/delete.
# Explicitly NOT data-like: .json/.xml/.ini/.conf/.bak.
_DATA_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".sqlitedb", ".db-shm", ".db-wal", ".index", ".dat"}
_FM7_SIGNATURE_CATEGORIES = {
    "app-caches",
    "browser-caches",
    "gpu-shader",
    "dev-caches",
    "ide-caches",
    "crash-dumps",
    "thumbnail-cache",
}
_FM7_CAUTION_MESSAGE = "路径语义可疑，请人工确认"

# FM0: conservative default posture. Only age-gated temp/logs and verified
# empty dirs run by default; app-owned caches and crash-dumps are opt-in via
# an explicit --categories list.
_CONSERVATIVE_DEFAULT_CATEGORIES = {"root-temps", "root-logs", "empty-dirs", "user-temp"}

_DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / ".omo" / "evidence" / "python-migration"


def _case_key(value: object) -> str:
    return os.fspath(value).casefold()


def _is_traversal_link(path: Path) -> bool:
    try:
        # Validate every existing ancestor before looking at the final
        # component.  A regular-looking final directory may still be reached
        # through a junction/symlink ancestor and must fail closed.
        core._assert_no_traversal_components(os.fspath(path))
        return path.is_symlink() or core.is_junction(os.fspath(path))
    except (OSError, ValueError):
        return True


def _list_entries(directory: Path, want_directories: bool) -> list[Path]:
    entries: list[Path] = []
    try:
        if _is_traversal_link(directory):
            return entries
        with os.scandir(directory) as iterator:
            for entry in iterator:
                try:
                    if entry.is_symlink() or _is_traversal_link(Path(entry.path)):
                        continue
                    matches = entry.is_dir(follow_symlinks=False)
                    if matches == want_directories:
                        entries.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return entries
    return sorted(entries, key=_case_key)


def _list_files(directory: Path) -> list[Path]:
    return _list_entries(directory, False)


def _list_directories(directory: Path) -> list[Path]:
    return _list_entries(directory, True)


def _file_size(path: Path) -> int:
    try:
        return int(os.stat(path, follow_symlinks=False).st_size)
    except OSError:
        return 0


def _is_older_than(path: Path, cutoff: datetime) -> bool:
    try:
        return os.path.getmtime(path) < cutoff.timestamp()
    except OSError:
        return False


def _is_system_owned(path: Path) -> bool:
    """True when the OS owns the file (SYSTEM on Windows, uid 0 on POSIX).

    Windows uses a two-tier check: the FILE_ATTRIBUTE_SYSTEM flag first (cheap,
    catches files the filesystem itself marks system-owned), then the file's
    security-descriptor owner ACL.  A root file whose owner ACL cannot even be
    read (ERROR_ACCESS_DENIED) is treated as system-owned — live data shows
    C:\\DumpStack.log has Attributes=Archive (0x20), not System (0x4), yet its
    owner query returns ERROR_ACCESS_DENIED.  Any unexpected error returns
    False so the scan never crashes.
    """
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    if not IS_WINDOWS:
        return st.st_uid == 0
    if (int(getattr(st, "st_file_attributes", 0)) & 0x4) != 0:
        return True
    # Tier 2: read the file's owner ACL (SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION).
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.windll.advapi32
    except (ImportError, AttributeError, OSError):
        return False
    try:
        get_owner = advapi32.GetNamedSecurityInfoW
        get_owner.argtypes = [
            wintypes.LPCWSTR,  # pObjectName
            wintypes.DWORD,  # ObjectType (SE_FILE_OBJECT = 0x1)
            wintypes.DWORD,  # SecurityInfo (OWNER_SECURITY_INFORMATION = 0x1)
            ctypes.POINTER(ctypes.c_void_p),  # ppsidOwner
            ctypes.POINTER(ctypes.c_void_p),  # ppsidGroup
            ctypes.POINTER(ctypes.c_void_p),  # ppDacl
            ctypes.POINTER(ctypes.c_void_p),  # ppSacl
            ctypes.POINTER(ctypes.c_void_p),  # ppSecurityDescriptor
        ]
        get_owner.restype = wintypes.DWORD
        owner = ctypes.c_void_p()
        status = get_owner(
            os.fspath(path),
            0x1,  # SE_FILE_OBJECT
            0x1,  # OWNER_SECURITY_INFORMATION
            ctypes.byref(owner),
            None,
            None,
            None,
            None,
        )
    except Exception:
        return False
    if status == 5:  # ERROR_ACCESS_DENIED -> unreadable owner ACL -> system-owned
        return True
    if status != 0:  # any other unexpected Win32 error
        return False
    # The ACL was readable: NT AUTHORITY\\SYSTEM's well-known SID is S-1-5-18.
    try:
        to_string = advapi32.ConvertSidToStringSidW
        to_string.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        to_string.restype = wintypes.BOOL
        sid_text = wintypes.LPWSTR()
        if not to_string(owner, ctypes.byref(sid_text)):
            return False
        try:
            return (sid_text.value or "").upper() == "S-1-5-18"
        finally:
            if sid_text.value:
                local_free = ctypes.windll.kernel32.LocalFree
                local_free.argtypes = [wintypes.HLOCAL]
                local_free.restype = wintypes.HLOCAL
                local_free(sid_text)
    except Exception:
        return False


# v2.1.1: installer/uninstaller/updater artifacts are exempt from user-temp even
# past the 7-day gate.  Exact suffix match, or a whole-word installer keyword in
# the casefolded full filename.  'install.log' IS exempt by design (the '.' is a
# word boundary); generic junk such as installer_data.tmp / wctCDFA.tmp is NOT.
_INSTALLER_SUFFIXES = frozenset({".exe", ".msi", ".msu", ".msp", ".cab"})
_INSTALLER_PATTERNS = re.compile(r"\b(setup|install|unins|uninstall|updater)\b")


def _is_installer_artifact(path: Path) -> bool:
    return (
        path.suffix.casefold() in _INSTALLER_SUFFIXES
        or _INSTALLER_PATTERNS.search(path.name.casefold()) is not None
    )


def _checkpoint_payload(state: dict[str, Any], category: str, last_path: str) -> dict[str, Any]:
    return {
        "drive": state["drive"],
        "completedCategories": list(state["completedCategories"]),
        "currentCategory": category,
        "lastPath": last_path,
        "totalBytesSoFar": int(state["totalBytesSoFar"]),
        "timestamp": datetime.now().astimezone().isoformat(),
    }


def _write_checkpoint(state: dict[str, Any], category: str, last_path: str) -> None:
    payload = _checkpoint_payload(state, category, last_path)
    try:
        state["path"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        print(f"WARNING: checkpoint write failed (continuing scan): {error}", file=sys.stderr)


def _tick_file(state: dict[str, Any], category: str, path: Path, size: int) -> None:
    state["lastPath"] = os.fspath(path)
    state["totalBytesSoFar"] = int(state["totalBytesSoFar"]) + int(size)
    state["fileCounter"] = int(state["fileCounter"]) + 1
    if int(state["fileCounter"]) % 500 == 0:
        _write_checkpoint(state, category, os.fspath(path))


def _complete_category(state: dict[str, Any], category: str) -> None:
    if category not in state["completedCategories"]:
        state["completedCategories"].append(category)
    _write_checkpoint(state, category, str(state["lastPath"]))


def _resume_files(paths: Iterable[Path], category: str, resume_state: Optional[dict[str, Any]]) -> list[Path]:
    ordered = sorted(paths, key=_case_key)
    if not resume_state:
        return ordered
    if category != str(resume_state.get("currentCategory", "")):
        return ordered
    last_path = str(resume_state.get("lastPath", ""))
    if not last_path:
        return ordered
    threshold = last_path.casefold()
    try:
        last_parent = os.path.normcase(os.path.abspath(os.path.dirname(last_path)))
    except (OSError, TypeError):
        last_parent = ""
    filtered: list[Path] = []
    for path in ordered:
        try:
            parent = os.path.normcase(os.path.abspath(os.fspath(path.parent)))
        except (OSError, TypeError):
            parent = ""
        # A category may enumerate several independent roots (for example
        # Windows\\Temp then Windows\\Prefetch).  Only apply the ordinal
        # checkpoint cut to the root that actually produced lastPath; all
        # other roots are unscanned work and must remain eligible.
        if parent != last_parent or os.fspath(path).casefold() >= threshold:
            filtered.append(path)
    return filtered


def _dir_stats(path: Path, context: dict[str, Any], category: str) -> tuple[int, int]:
    if _is_traversal_link(path) or not path.is_dir():
        return 0, 0
    size = 0
    count = 0
    stack = [path]
    while stack:
        directory = stack.pop()
        if _is_traversal_link(directory):
            continue
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(list(iterator), key=lambda entry: entry.path.casefold())
        except OSError:
            continue
        for entry in entries:
            child = Path(entry.path)
            try:
                if entry.is_symlink() or _is_traversal_link(child):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(child)
                    continue
                item_size = int(entry.stat(follow_symlinks=False).st_size)
            except OSError:
                continue
            size += item_size
            count += 1
            _tick_file(context["checkpoint"], category, child, item_size)
    return size, count


def _find_dirs_named(root: Path, name: str) -> list[Path]:
    if _is_traversal_link(root) or not root.is_dir():
        return []
    found: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        for child in _list_directories(directory):
            if child.name.casefold() == name.casefold():
                found.append(child)
            else:
                stack.append(child)
    return sorted(found, key=_case_key)


def _candidate(
    category: str,
    path: object,
    size: int,
    count: int,
    *,
    risk: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    risk = risk or CATEGORY_RISK_MAP[category]
    row = {
        "Category": category,
        "Risk": risk,
        "Path": os.fspath(path),
        "SizeBytes": int(size),
        "FileCount": int(count),
        "Action": RISK_ACTION_MAP[risk],
    }
    if reason:
        row["Reason"] = reason
    return row


def _path_claim_key(path: object) -> str:
    """FM6: canonical key for single-ownership claims (case-insensitive on
    Windows via normcase, case-sensitive on POSIX)."""
    return os.path.normcase(os.fspath(path))


def _claimed(context: dict[str, Any]) -> set[str]:
    return context.setdefault("claimed", set())


def _add_candidate(context: dict[str, Any], category: str, path: object, size: int, count: int) -> None:
    """FM6: append a candidate only if its path has not already been claimed by
    an earlier category. Each path belongs to EXACTLY ONE category."""
    claimed = _claimed(context)
    key = _path_claim_key(path)
    if key in claimed:
        return
    claimed.add(key)
    context["rows"].append(_candidate(category, path, size, count))


def _content_signature_data_like(path: Path, sample_cap: int = 20) -> bool:
    """FM7: sample the FIRST ``sample_cap`` entries of a directory via
    ``os.scandir`` order (if the dir has fewer, all are checked). Return True
    when any sampled entry carries a data-file suffix. Never follows links and
    never traverses deeper than the single scanned level."""
    if _is_traversal_link(path) or not path.is_dir():
        return False
    try:
        with os.scandir(path) as iterator:
            for index, entry in enumerate(iterator):
                if index >= sample_cap:
                    break
                try:
                    suffix = os.path.splitext(entry.name)[1].casefold()
                except (OSError, ValueError):
                    continue
                if suffix in _DATA_SUFFIXES:
                    return True
    except OSError:
        return False
    return False


def _add_file(context: dict[str, Any], category: str, path: Path) -> None:
    size = _file_size(path)
    _add_candidate(context, category, path, size, 1)


def _add_directory(context: dict[str, Any], category: str, path: Path) -> None:
    claimed = _claimed(context)
    key = _path_claim_key(path)
    if key in claimed:
        return
    # FM7: a static-map cache path whose content signature is data-like is
    # escalated to CAUTION (quarantine) so it is never delete/clean_contents'd.
    if category in _FM7_SIGNATURE_CATEGORIES and _content_signature_data_like(path):
        size, count = _dir_stats(path, context, category)
        context["rows"].append(_candidate(category, path, size, count, risk="CAUTION", reason=_FM7_CAUTION_MESSAGE))
        claimed.add(key)
        print(f"WARNING: {_FM7_CAUTION_MESSAGE}: {path} (risk escalated to CAUTION)", file=sys.stderr)
        return
    size, count = _dir_stats(path, context, category)
    context["rows"].append(_candidate(category, path, size, count))
    claimed.add(key)


def _scan_root_temps(context: dict[str, Any]) -> None:
    category = "root-temps"
    directories: list[Path]
    if IS_WINDOWS:
        directories = [context["root"] / name for name in ("Temp", "tmp", "temp")]
    else:
        directories = [context["system_temp"]]
    seen: set[str] = set()
    files: list[Path] = []
    for directory in directories:
        if not directory.is_dir() or _is_traversal_link(directory):
            continue
        try:
            actual = os.fspath(directory.resolve()).casefold()
        except OSError:
            actual = os.fspath(directory.absolute()).casefold()
        if actual in seen:
            continue
        seen.add(actual)
        files.extend(_list_files(directory))
    for path in _resume_files(files, category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if _is_older_than(path, context["cutoff"]):
            _add_candidate(context, category, path, size, 1)


def _scan_root_logs(context: dict[str, Any]) -> None:
    category = "root-logs"
    for path in _resume_files(_list_files(context["root"]), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        lower_name = path.name.casefold()
        # FM6: root-logs drops *.tmp — .tmp files belong to root-temps (age
        # gate + delete-time recheck), never to the un-gated root-logs scan.
        if path.suffix.casefold() in {".log"} or ("_install" in lower_name and lower_name.endswith(".log")):
            # v2.1.1: OS-owned root logs (SYSTEM/uid-0) are never candidates —
            # an elevated run must not delete them.
            if _is_system_owned(path):
                continue
            _add_candidate(context, category, path, size, 1)


def _scan_duplicate_archives(context: dict[str, Any]) -> None:
    category = "duplicate-archives"
    for path in _resume_files(_list_files(context["root"]), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if path.suffix.casefold() in {".zip", ".rar", ".7z"} and (context["root"] / path.stem).is_dir():
            _add_candidate(context, category, path, size, 1)


def _scan_empty_dirs(context: dict[str, Any]) -> None:
    category = "empty-dirs"
    skipped = {"$recycle.bin", "system volume information", ".claude"}
    for path in _list_directories(context["root"]):
        if path.name.casefold() in skipped:
            continue
        if core.is_dir_empty(os.fspath(path)):
            _add_candidate(context, category, path, 0, 0)


def _scan_recycle_bin(context: dict[str, Any]) -> None:
    if IS_WINDOWS:
        path = context["root"] / "$RECYCLE.BIN"
    else:
        path = context["home"] / ".local" / "share" / "Trash"
    if path.is_dir() and not _is_traversal_link(path):
        _add_directory(context, "recycle-bin", path)


def _scan_root_suspicious(context: dict[str, Any]) -> None:
    category = "root-suspicious"
    excluded = {path.name.casefold() for path in _list_directories(context["root"])}
    for program_files in (context["root"] / "Program Files", context["root"] / "Program Files (x86)"):
        excluded.update(path.name.casefold() for path in _list_directories(program_files))
    for path in _resume_files(_list_files(context["root"]), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if path.suffix.casefold() not in {".dll", ".exe"}:
            continue
        if path.stem.casefold() not in excluded:
            _add_candidate(context, category, path, size, 1)


def _scan_app_caches(context: dict[str, Any]) -> None:
    category = "app-caches"
    root = context["root"]
    direct = [root / "anaconda3" / "pkgs" / "cache", root / "Ubisoft Game Launcher" / "cache"]
    for path in direct:
        if path.is_dir() and not _is_traversal_link(path):
            _add_directory(context, category, path)
    wegame = root / "Wegame"
    for child in _list_directories(wegame):
        for name in ("tiny_cache", "cache"):
            path = child / name
            if path.is_dir() and not _is_traversal_link(path):
                _add_directory(context, category, path)
    wechat = root / "WeiXin" / "xwechat_files"
    for path in _find_dirs_named(wechat, "cache"):
        _add_directory(context, category, path)
    steam = root / "SteamLibrary" / "steamapps" / "common"
    for path in _list_directories(steam):
        if core.is_dir_empty(os.fspath(path)):
            _add_candidate(context, category, path, 0, 0)


def _scan_browser_caches(context: dict[str, Any]) -> None:
    category = "browser-caches"
    if IS_WINDOWS:
        for browser in (Path("Google") / "Chrome", Path("Microsoft") / "Edge"):
            user_data = context["local_app_data"] / browser / "User Data"
            for path in (
                user_data / "Default" / "Cache",
                user_data / "Default" / "Code Cache",
                user_data / "Default" / "GPUCache",
                user_data / "Crashpad" / "reports",
            ):
                if path.is_dir() and not _is_traversal_link(path):
                    _add_directory(context, category, path)
        return
    cache = context["user_cache"]
    for browser in ("google-chrome", "microsoft-edge"):
        default = cache / browser / "Default"
        for name in ("Cache", "Code Cache", "GPUCache"):
            path = default / name
            if path.is_dir() and not _is_traversal_link(path):
                _add_directory(context, category, path)
    firefox = cache / "mozilla" / "firefox"
    for profile in _list_directories(firefox):
        path = profile / "cache2"
        if path.is_dir() and not _is_traversal_link(path):
            _add_directory(context, category, path)


def _scan_gpu_shader(context: dict[str, Any]) -> None:
    for relative in (Path("NVIDIA") / "DXCache", Path("NVIDIA") / "GLCache", Path("D3DSCache")):
        path = context["local_app_data"] / relative
        if path.is_dir() and not _is_traversal_link(path):
            _add_directory(context, "gpu-shader", path)


def _scan_dev_caches(context: dict[str, Any]) -> None:
    category = "dev-caches"
    if IS_WINDOWS:
        paths = [
            context["local_app_data"] / "pip" / "cache",
            context["local_app_data"] / "npm-cache",
        ]
        paths.extend(context["home"] / ".cache" / name for name in ("torch", "huggingface", "opencode", "codex-runtimes", "pkg"))
    else:
        paths = [context["user_cache"] / name for name in ("pip", "npm", "torch", "huggingface", "opencode", "codex-runtimes")]
    for path in paths:
        if path.is_dir() and not _is_traversal_link(path):
            _add_directory(context, category, path)


def _scan_ide_caches(context: dict[str, Any]) -> None:
    category = "ide-caches"
    if IS_WINDOWS:
        jetbrains = context["local_app_data"] / "JetBrains"
        for product in _list_directories(jetbrains):
            names = ["caches", "log"]
            if product.name.casefold() in {"toolbox", "toolbox-dev"}:
                names.extend(["cache", "logs"])
            for name in names:
                path = product / name
                if path.is_dir() and not _is_traversal_link(path):
                    _add_directory(context, category, path)
        profiles = context["app_data"] / "Zotero" / "Zotero" / "Profiles"
        for profile in _list_directories(profiles):
            for name in ("cache2", "startupCache", "shader-cache"):
                path = profile / name
                if path.is_dir() and not _is_traversal_link(path):
                    _add_directory(context, category, path)
        jedi = context["local_app_data"] / "Jedi" / "Jedi"
        files: list[Path] = []
        for directory in _list_directories(jedi):
            files.extend(path for path in _list_files(directory) if path.suffix.casefold() == ".pkl")
        for path in _resume_files(files, category, context["resume_state"]):
            size = _file_size(path)
            _tick_file(context["checkpoint"], category, path, size)
            _add_candidate(context, category, path, size, 1)
        return
    cache = context["user_cache"]
    for product in _list_directories(cache / "JetBrains"):
        for name in ("caches", "log"):
            path = product / name
            if path.is_dir() and not _is_traversal_link(path):
                _add_directory(context, category, path)
    vscode = context["home"] / ".config" / "Code"
    zotero = cache / "zotero"
    for path in [vscode / name for name in ("Cache", "CachedData", "logs")] + [
        zotero / name for name in ("cache2", "startupCache", "shader-cache")
    ]:
        if path.is_dir() and not _is_traversal_link(path):
            _add_directory(context, category, path)


def _scan_crash_dumps(context: dict[str, Any]) -> None:
    category = "crash-dumps"
    if IS_WINDOWS:
        crash_dumps = context["local_app_data"] / "CrashDumps"
        if crash_dumps.is_dir() and not _is_traversal_link(crash_dumps):
            _add_directory(context, category, crash_dumps)
        for path in _list_directories(context["local_app_data"]):
            if path.name.casefold() == "crashpad":
                _add_directory(context, category, path)
        return
    path = context["posix_crash_dir"]
    if path.is_dir() and not _is_traversal_link(path):
        _add_directory(context, category, path)


def _scan_thumbnail_cache(context: dict[str, Any]) -> None:
    category = "thumbnail-cache"
    if not IS_WINDOWS:
        path = context["user_cache"] / "thumbnails"
        if path.is_dir() and not _is_traversal_link(path):
            _add_directory(context, category, path)
        return
    explorer = context["local_app_data"] / "Microsoft" / "Windows" / "Explorer"
    for path in _resume_files(_list_files(explorer), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        name = path.name.casefold()
        if (name.startswith("thumbcache_") or name.startswith("iconcache_")) and name.endswith(".db"):
            _add_candidate(context, category, path, size, 1)


def _scan_user_temp(context: dict[str, Any]) -> None:
    category = "user-temp"
    directory = context["local_app_data"] / "Temp" if IS_WINDOWS else context["user_cache"]
    for path in _resume_files(_list_files(directory), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if _is_older_than(path, context["cutoff"]):
            # v2.1.1: installer/uninstaller/updater artifacts stay exempt even
            # past the 7-day gate — a stale installer is not junk.
            if _is_installer_artifact(path):
                continue
            _add_candidate(context, category, path, size, 1)


def _scan_elevated_system(context: dict[str, Any]) -> None:
    category = "elevated-system"
    root = context["root"]
    temp = root / "Windows" / "Temp"
    for path in _resume_files(_list_files(temp), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if _is_older_than(path, context["cutoff"]):
            _add_candidate(context, category, path, size, 1)
    prefetch = root / "Windows" / "Prefetch"
    for path in _resume_files(_list_files(prefetch), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if path.suffix.casefold() == ".pf" and path.name.casefold() != "layout.ini":
            _add_candidate(context, category, path, size, 1)
    distribution = root / "Windows" / "SoftwareDistribution"
    if distribution.is_dir() and not _is_traversal_link(distribution):
        _add_directory(context, category, distribution)
    updates = root / "Windows" / "Logs" / "WindowsUpdate"
    for path in _resume_files(_list_files(updates), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if path.suffix.casefold() == ".etl" and _is_older_than(path, context["cutoff"]):
            _add_candidate(context, category, path, size, 1)
    cbs = root / "Windows" / "Logs" / "CBS"
    for path in _resume_files(_list_files(cbs), category, context["resume_state"]):
        size = _file_size(path)
        _tick_file(context["checkpoint"], category, path, size)
        if path.name.casefold().startswith("cbspersist_") and path.suffix.casefold() == ".cab":
            _add_candidate(context, category, path, size, 1)
    _add_candidate(context, category, "DISM StartComponentCleanup (no /ResetBase)", 0, 0)


_SCANNERS = {
    "root-temps": _scan_root_temps,
    "root-logs": _scan_root_logs,
    "duplicate-archives": _scan_duplicate_archives,
    "empty-dirs": _scan_empty_dirs,
    "recycle-bin": _scan_recycle_bin,
    "root-suspicious": _scan_root_suspicious,
    "app-caches": _scan_app_caches,
    "browser-caches": _scan_browser_caches,
    "gpu-shader": _scan_gpu_shader,
    "dev-caches": _scan_dev_caches,
    "ide-caches": _scan_ide_caches,
    "crash-dumps": _scan_crash_dumps,
    "thumbnail-cache": _scan_thumbnail_cache,
    "user-temp": _scan_user_temp,
    "elevated-system": _scan_elevated_system,
}


def _parse_categories(value: object) -> Optional[list[str]]:
    if value is None:
        return None
    parts: list[str] = []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    for item in values:
        parts.extend(part.strip() for part in str(item).split(",") if part.strip())
    unknown = [category for category in parts if category not in CATEGORY_RISK_MAP]
    if unknown:
        raise ValueError(f"Unknown category/categories: {', '.join(unknown)}")
    return parts


def _applicable_categories(categories: Optional[list[str]], is_user_drive: bool, include_elevated: bool) -> list[str]:
    order = _WINDOWS_ORDER if IS_WINDOWS else _POSIX_ORDER
    selected = set(categories) if categories is not None else set(_CONSERVATIVE_DEFAULT_CATEGORIES)
    user_categories = _WINDOWS_USER_CATEGORIES if IS_WINDOWS else _POSIX_USER_CATEGORIES
    result: list[str] = []
    for category in order:
        if category not in selected:
            continue
        if category in user_categories and not is_user_drive:
            continue
        if category == "elevated-system" and not include_elevated:
            continue
        result.append(category)
    return result


def _read_checkpoint(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "completedCategories": [str(item) for item in data.get("completedCategories", [])],
        "currentCategory": str(data.get("currentCategory", "")),
        "lastPath": str(data.get("lastPath", "")),
        "totalBytesSoFar": int(data.get("totalBytesSoFar", 0)),
    }


def _drive_id(drive: str) -> str:
    return drive.rstrip("\\/").rstrip(":").upper() if IS_WINDOWS else "ROOT"


def _latest_resume_dir(out_dir: Path, drive: str) -> Path:
    prefix = f"{_drive_id(drive)}-"
    candidates = [
        path
        for path in out_dir.iterdir()
        if path.is_dir() and path.name.startswith(prefix) and (path / "scan-checkpoint.json").is_file()
    ] if out_dir.is_dir() else []
    if not candidates:
        raise ValueError("No checkpoint found for resume")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        columns = line.split("|")
        if len(columns) != 6:
            continue
        try:
            size = int(columns[3])
            count = int(columns[4])
        except ValueError:
            continue
        rows.append(
            {
                "Category": columns[0],
                "Risk": columns[1],
                "Path": columns[2],
                "SizeBytes": size,
                "FileCount": count,
                "Action": columns[5],
            }
        )
    return rows


def _csv_field(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", " ")


def _write_outputs(run_dir: Path, new_rows: list[dict[str, Any]], evaluated: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    csv_path = run_dir / "candidates.csv"
    existing = _load_existing_rows(csv_path)
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in existing + new_rows:
        key = f"{row['Category']}|{row['Path']}".casefold()
        if key in seen:
            continue
        seen.add(key)
        combined.append(row)
    lines = [_CSV_HEADER]
    for row in combined:
        lines.append(
            "|".join(
                _csv_field(row[column])
                for column in ("Category", "Risk", "Path", "SizeBytes", "FileCount", "Action")
            )
        )
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report_path = run_dir / "scan-report.json"
    old_report: dict[str, Any] = {}
    if report_path.is_file():
        try:
            old_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old_report = {}
    report: dict[str, Any] = {}
    category_order = list(dict.fromkeys(evaluated + [row["Category"] for row in combined] + list(old_report)))
    for category in category_order:
        if category not in CATEGORY_RISK_MAP:
            continue
        category_rows = [row for row in combined if row["Category"] == category]
        if not category_rows and category in old_report:
            category_rows = list(old_report[category].get("candidates", []))
        report[category] = {
            "name": category,
            "risk": CATEGORY_RISK_MAP[category],
            "candidates": category_rows,
        }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return combined, report


def _snapshot_process_stems(process_iter: Any = None) -> set[str]:
    """Return the lowercased stem of every currently running process name."""
    stems: set[str] = set()
    iter_func = process_iter or psutil.process_iter
    try:
        processes = iter_func(["name"])
        for process in processes:
            try:
                name = str(process.info.get("name") or "")
            except (psutil.Error, OSError):
                continue
            stems.add(Path(name).stem.casefold())
    except (psutil.Error, OSError):
        pass
    return stems


def _process_spec_matches(spec: str, stems: set[str]) -> bool:
    """Whether a running stem satisfies an owner spec (``jetbrains*`` prefix)."""
    if spec.endswith("*"):
        prefix = spec[:-1].casefold()
        return any(stem.startswith(prefix) for stem in stems)
    return spec.casefold() in stems


def _owners_running(category: str, stems: set[str]) -> list[str]:
    """Return the owner-process specs of *category* that are currently running."""
    owners = CATEGORY_OWNER_PROCESSES.get(category) or []
    return [spec for spec in owners if _process_spec_matches(spec, stems)]


def _fm4_skip_message(category: str, owners: Sequence[str]) -> str:
    display = "、".join(sorted({_OWNER_DISPLAY.get(spec, spec.title()) for spec in owners}))
    category_display = _CATEGORY_DISPLAY.get(category, category)
    return f"检测到 {display} 运行中，{category_display}清理已跳过。关闭后重跑该类别即可。"


def _process_names() -> list[str]:
    return sorted(stem for stem in _snapshot_process_stems() if stem in _WATCHED_PROCESSES)


def scan(drive: str, **kwargs: Any) -> dict[str, Any]:
    """Scan one drive without deleting or moving any item."""
    root_override = kwargs.get("root_path")
    volume = kwargs.get("volume")
    if volume is None and root_override is None:
        volume = platform.resolve_fixed_drive(drive)
        if volume is None:
            raise ValueError(f"Drive '{drive}' is not an available fixed local volume")
    if root_override is not None:
        root = Path(root_override)
        if not root.is_dir() or _is_traversal_link(root):
            raise ValueError(f"Scan root is not a real directory: {root}")
        usage = psutil.disk_usage(os.fspath(root))
        free_bytes = int(volume.get("FreeBytes", usage.free)) if volume else int(usage.free)
        total_bytes = int(volume.get("TotalBytes", usage.total)) if volume else int(usage.total)
    else:
        root = Path(str(volume["Root"]))
        free_bytes = int(volume["FreeBytes"])
        total_bytes = int(volume["TotalBytes"])

    categories = _parse_categories(kwargs.get("categories"))
    include_elevated = bool(kwargs.get("include_elevated", False))
    if "is_user_drive" in kwargs:
        is_user_drive = bool(kwargs["is_user_drive"])
    elif IS_WINDOWS:
        is_user_drive = Path.home().drive == drive.upper()
    else:
        is_user_drive = drive == "/"
    applicable = _applicable_categories(categories, is_user_drive, include_elevated)

    out_dir = Path(kwargs.get("out_dir") or _DEFAULT_OUT_DIR)
    resume = bool(kwargs.get("resume", False))
    explicit_run_dir = kwargs.get("run_dir")
    if explicit_run_dir is not None:
        run_dir = Path(explicit_run_dir)
        if resume and not (run_dir / "scan-checkpoint.json").is_file():
            raise ValueError("No checkpoint found for resume")
        run_dir.mkdir(parents=True, exist_ok=True)
    elif resume:
        run_dir = _latest_resume_dir(out_dir, drive)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir = out_dir / f"{_drive_id(drive)}-{timestamp}"
        run_dir.mkdir(parents=False, exist_ok=False)

    checkpoint_path = run_dir / "scan-checkpoint.json"
    resume_state = _read_checkpoint(checkpoint_path) if resume else None
    completed = list(resume_state["completedCategories"]) if resume_state else []
    checkpoint = {
        "path": checkpoint_path,
        "drive": drive,
        "completedCategories": completed,
        "currentCategory": str(resume_state.get("currentCategory", "")) if resume_state else "",
        "lastPath": str(resume_state.get("lastPath", "")) if resume_state else "",
        "totalBytesSoFar": int(resume_state.get("totalBytesSoFar", 0)) if resume_state else 0,
        "fileCounter": 0,
    }

    preflight = [
        f"BASELINE_FREE_BYTES={free_bytes}",
        f"TOTAL_BYTES={total_bytes}",
        f"PROCESSES={','.join(_process_names())}",
    ]
    (run_dir / "preflight.txt").write_text("\n".join(preflight) + "\n", encoding="utf-8")

    home = Path(kwargs.get("home_dir") or Path.home())
    local_app_data = Path(kwargs.get("local_app_data") or os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    app_data = Path(kwargs.get("app_data") or os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    cache_value = kwargs.get("user_cache_dir") or platform.get_user_cache_dir() or home / ".cache"
    system_temp_value = kwargs.get("system_temp_dir") or platform.get_system_temp_dir()
    context = {
        "root": root,
        "home": home,
        "local_app_data": local_app_data,
        "app_data": app_data,
        "user_cache": Path(cache_value),
        "system_temp": Path(system_temp_value),
        "posix_crash_dir": Path(kwargs.get("posix_crash_dir") or "/var/crash"),
        "cutoff": datetime.now() - timedelta(days=7),
        "rows": [],
        "claimed": set(),
        "checkpoint": checkpoint,
        "resume_state": resume_state,
    }

    evaluated: list[str] = []
    dry_run = bool(kwargs.get("dry_run", False))
    running_stems = _snapshot_process_stems(process_iter=kwargs.get("process_iter"))
    completed_set = {str(category).casefold() for category in completed}
    for category in applicable:
        if category.casefold() in completed_set:
            continue
        # FM4 scan-time gate: an owner process running now skips the whole
        # category so its candidates are never generated (and never reach
        # clean_contents later). Never gates elevated-system.
        owners = [] if category == "elevated-system" else _owners_running(category, running_stems)
        if owners:
            print(f"SKIP: {_fm4_skip_message(category, owners)}")
            continue
        evaluated.append(category)
        checkpoint["fileCounter"] = 0
        _SCANNERS[category](context)
        _complete_category(checkpoint, category)

    combined_rows, report = _write_outputs(run_dir, context["rows"], applicable)
    if dry_run:
        for row in combined_rows:
            print(f"DRY-RUN: {row['Category']} | {row['SizeBytes']} bytes | {row['Path']} | {row['Action']}")
    return {
        "drive": drive,
        "run_dir": os.fspath(run_dir),
        "is_user_drive": is_user_drive,
        "rows": combined_rows,
        "report": report,
        "evaluated": evaluated,
    }


def _split_values(values: Optional[Sequence[str]]) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _subprocess_args(drive: str, arguments: argparse.Namespace, out_dir: Path) -> list[str]:
    command = [sys.executable, os.fspath(Path(__file__).resolve()), "-Drive", drive, "-OutDir", os.fspath(out_dir)]
    if arguments.IncludeElevated:
        command.append("-IncludeElevated")
    if arguments.Categories:
        command.extend(["-Categories", arguments.Categories])
    if arguments.Resume:
        command.append("-Resume")
    if getattr(arguments, "dry_run", False):
        command.append("--dry-run")
    return command


def _candidate_totals(run_dir: Optional[Path]) -> tuple[int, int]:
    if run_dir is None:
        return 0, 0
    rows = _load_existing_rows(run_dir / "candidates.csv")
    return len(rows), sum(int(row["SizeBytes"]) for row in rows)


def _latest_drive_run(out_dir: Path, drive: str) -> Optional[Path]:
    prefix = f"{_drive_id(drive)}-"
    candidates = [path for path in out_dir.iterdir() if path.is_dir() and path.name.startswith(prefix)] if out_dir.is_dir() else []
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def _run_many(drives: list[str], arguments: argparse.Namespace) -> int:
    out_dir = Path(arguments.OutDir or _DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    multi_dir = out_dir / f"multidrive-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    multi_dir.mkdir()

    def launch(drive: str) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(_subprocess_args(drive, arguments, out_dir), check=False)

    if arguments.Parallel:
        workers = max(1, min(len(drives), int(arguments.Throttle)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(launch, drives))
    else:
        results = [launch(drive) for drive in drives]

    lines = ["Drive|RunDir|ExitCode|Status|Candidates|CandidateBytes"]
    failed = False
    for drive, result in zip(drives, results):
        run_dir = _latest_drive_run(out_dir, drive)
        count, size = _candidate_totals(run_dir)
        status = "OK" if result.returncode == 0 else "FAIL"
        failed = failed or result.returncode != 0
        lines.append(
            "|".join(
                [drive, os.fspath(run_dir) if run_dir else "", str(result.returncode), status, str(count), str(size)]
            )
        )
    (multi_dir / "drives.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"MULTI-DRIVE SCAN COMPLETE: {len(drives)} drive(s) scanned.")
    print(f"OUTPUT: {multi_dir}")
    return 1 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, prefix_chars="-")
    parser.add_argument("-Drive")
    parser.add_argument("-Drives", nargs="+")
    parser.add_argument("-OutDir")
    parser.add_argument("-IncludeElevated", action="store_true")
    parser.add_argument("-Categories")
    parser.add_argument("-Resume", action="store_true")
    parser.add_argument("-Parallel", action="store_true")
    parser.add_argument("-Throttle", type=int, default=4)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print a per-file preview of every candidate without deleting anything",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    drives = _split_values(arguments.Drives)
    if bool(arguments.Drive) == bool(drives):
        parser.error("specify exactly one of -Drive or -Drives")
    try:
        if drives:
            return _run_many(drives, arguments)
        result = scan(
            arguments.Drive,
            out_dir=arguments.OutDir,
            include_elevated=arguments.IncludeElevated,
            categories=arguments.Categories,
            resume=arguments.Resume,
            dry_run=getattr(arguments, "dry_run", False),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"SCAN COMPLETE: {len(result['rows'])} candidate(s) across {len(result['report'])} category/categories.")
    print(f"OUTPUT: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
