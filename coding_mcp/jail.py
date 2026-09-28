"""Все файловые пути приводятся к корню MCP_USER_WORKDIR."""

from __future__ import annotations

import os
from pathlib import Path

from coding_mcp.errors import DocumentError
from coding_mcp.limits import MAX_FILE_BYTES

_SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "WEBHOOK", "_KEY", "_DSN", "_PAT")


def scrub_environment() -> int:
    """Убрать из процесса переменные, похожие на секреты worker. Вернуть число удалённых."""

    removed = 0
    for name in list(os.environ):
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_MARKERS):
            os.environ.pop(name, None)
            removed += 1
    return removed


def load_root(raw: str | None) -> Path:
    if not raw or not raw.strip():
        raise DocumentError("Не задан корень рабочей директории.")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise DocumentError("Корень рабочей директории не является каталогом.")
    return root


def resolve_in_root(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise DocumentError("Путь пустой.")
    if "\x00" in raw:
        raise DocumentError("Недопустимый путь.")

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate

    _reject_odd_parts(candidate)
    try:
        resolved = candidate.resolve()
    except OSError:
        raise DocumentError("Недопустимый путь.") from None

    if not _is_inside(root, resolved):
        raise DocumentError("Путь вне рабочей директории.")
    return resolved


def open_for_read(root: Path, raw: str, suffix: str) -> Path:
    path = resolve_in_root(root, raw)
    if path.suffix.lower() != suffix:
        raise DocumentError(f"Ожидается файл {suffix}.")
    if not path.is_file():
        raise DocumentError("Файл не найден.")
    try:
        size = path.stat().st_size
    except OSError:
        raise DocumentError("Файл не найден.") from None
    if size == 0:
        raise DocumentError("Файл пустой.")
    if size > MAX_FILE_BYTES:
        raise DocumentError("Файл превышает лимит размера.")
    return path


def prepare_write(root: Path, raw: str, suffix: str) -> Path:
    path = resolve_in_root(root, raw)
    if path.suffix.lower() != suffix:
        raise DocumentError(f"Ожидается файл {suffix}.")
    if path.exists() and not path.is_file():
        raise DocumentError("Путь указывает на каталог.")
    parent = path.parent
    if not _is_inside(root, parent):
        raise DocumentError("Путь вне рабочей директории.")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise DocumentError("Не удалось создать каталог.") from None
    resolved = resolve_in_root(root, raw)
    if resolved.exists() and not resolved.is_file():
        raise DocumentError("Путь указывает на каталог.")
    return resolved


def relative_name(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _reject_odd_parts(candidate: Path) -> None:
    for part in candidate.parts:
        if part in {"", ".", ".."}:
            continue
        if ":" in part and not _is_drive(part):
            raise DocumentError("Недопустимый путь.")
        if part.endswith(" ") or part.endswith("."):
            raise DocumentError("Недопустимый путь.")


def _is_drive(part: str) -> bool:
    plain = part.rstrip("\\/")
    return len(plain) == 2 and plain[1] == ":" and plain[0].isalpha()


def _is_inside(root: Path, candidate: Path) -> bool:
    root_norm = os.path.normcase(str(root))
    candidate_norm = os.path.normcase(str(candidate))
    try:
        return os.path.commonpath([root_norm, candidate_norm]) == root_norm
    except ValueError:
        return False
