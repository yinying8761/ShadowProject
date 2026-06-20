import os
import json
import fnmatch
from pathlib import Path

BLOCKED_PATHS_WIN = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData\Microsoft",
    r"C:\Recovery",
    r"C:\$Recycle.Bin",
]

BLOCKED_PATTERNS = [
    r"*\.ssh\*",
    r"*\.gnupg\*",
    r"*\AppData\Local\Google\Chrome\User Data\*",
    r"*\AppData\Local\Microsoft\Edge\User Data\*",
    r"*\AppData\Roaming\Mozilla\Firefox\Profiles\*",
    r"*\AppData\Roaming\Signal\*",
    r"*\AppData\Roaming\Telegram Desktop\*",
    r"*\.aws\credentials*",
    r"*\.docker\config.json*",
]

SENSITIVE_FILENAME_PATTERNS = [
    "*.pem",
    "*.key",
    "*id_rsa*",
    "*id_ed25519*",
    "*credentials*",
    "*secret*",
]


def _is_blocked(p: Path) -> bool:
    """Check if a resolved path falls within blocked system/sensitive areas."""
    s = str(p)
    for blocked in BLOCKED_PATHS_WIN:
        if s.lower().startswith(blocked.lower()):
            return True
    for pattern in BLOCKED_PATTERNS:
        if fnmatch.fnmatch(s, pattern):
            return True
    return False


def _is_sensitive_filename(p: Path) -> bool:
    """Check if filename matches sensitive patterns (warning only, not blocked)."""
    name = p.name.lower()
    for pattern in SENSITIVE_FILENAME_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _resolve_path(path: str) -> Path:
    """Resolve path. Block system directories."""
    p = Path(path).resolve()
    if _is_blocked(p):
        raise PermissionError(
            f"Access denied: path is in a protected system/sensitive area"
        )
    return p


async def read_file(path: str, encoding: str = "utf-8") -> str:
    """Read contents of a text file."""
    p = _resolve_path(path)
    if not p.exists():
        return json.dumps({"error": f"File not found: {path}"})
    if p.stat().st_size > 10 * 1024 * 1024:
        return json.dumps({"error": "File too large (>10MB)"})
    warning = None
    if _is_sensitive_filename(p):
        warning = f"Note: '{p.name}' may contain sensitive data"
    try:
        content = p.read_text(encoding=encoding)
        if warning:
            return json.dumps({"warning": warning, "content": content})
        return content
    except UnicodeDecodeError:
        return json.dumps({"error": f"Cannot read as {encoding}, may be binary"})


async def write_file(path: str, content: str, encoding: str = "utf-8") -> str:
    """Write content to a file. Creates parent directories."""
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding)
    return json.dumps({"success": True, "path": str(p), "size": p.stat().st_size})


async def list_directory(path: str = ".") -> str:
    """List files and directories in a path."""
    p = _resolve_path(path)
    if not p.exists():
        return json.dumps({"error": f"Not found: {path}"})
    if not p.is_dir():
        return json.dumps({"error": f"Not a directory: {path}"})
    entries = []
    try:
        for item in sorted(p.iterdir()):
            try:
                st = item.stat()
                entries.append({
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": st.st_size if item.is_file() else None,
                })
            except OSError:
                entries.append({"name": item.name, "type": "unknown"})
    except PermissionError:
        return json.dumps({"error": f"Permission denied: {path}"})
    return json.dumps({"path": str(p), "entries": entries})


async def search_files(root_path: str, pattern: str, max_results: int = 50) -> str:
    """Recursively search for files matching a glob pattern."""
    p = _resolve_path(root_path)
    if not p.exists():
        return json.dumps({"error": f"Not found: {root_path}"})
    matches = []
    try:
        for dirpath, dirnames, filenames in os.walk(p):
            if _is_blocked(Path(dirpath)):
                dirnames.clear()
                continue
            rel = Path(dirpath)
            for name in filenames + dirnames:
                if fnmatch.fnmatch(name, pattern):
                    matches.append(str(rel / name))
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
    except PermissionError:
        pass
    return json.dumps({"pattern": pattern, "results": matches[:max_results]})
