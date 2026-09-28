import os
from pathlib import Path

import pytest

from coding_mcp.errors import DocumentError
from coding_mcp.jail import load_root, resolve_in_root, scrub_environment


def test_relative_path_stays_inside(tmp_path: Path) -> None:
    root = tmp_path / "work"
    nested = root / "out"
    nested.mkdir(parents=True)
    target = nested / "a.txt"
    target.write_text("ok", encoding="utf-8")

    resolved = resolve_in_root(root, "out/a.txt")

    assert resolved == target.resolve()
    assert resolved.read_text(encoding="utf-8") == "ok"


def test_parent_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")

    with pytest.raises(DocumentError, match="вне рабочей директории"):
        resolve_in_root(root, "../secret.txt")


def test_sibling_prefix_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    sibling = tmp_path / "work-evil"
    sibling.mkdir()
    secret = sibling / "a.txt"
    secret.write_text("no", encoding="utf-8")

    with pytest.raises(DocumentError, match="вне рабочей директории"):
        resolve_in_root(root, str(secret))


def test_absolute_path_inside_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("ok", encoding="utf-8")

    assert resolve_in_root(root, str(target)) == target.resolve()


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    link = root / "leak.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("симлинки недоступны")

    with pytest.raises(DocumentError, match="вне рабочей директории"):
        resolve_in_root(root, "leak.txt")


def test_missing_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DocumentError):
        load_root(str(tmp_path / "missing"))


def test_scrub_removes_secret_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://example.invalid")
    monkeypatch.setenv("BITRIX_MCP_PAT", "secret")
    monkeypatch.setenv("MCP_USER_WORKDIR", "kept")
    monkeypatch.setenv("PATH", "kept-path")

    removed = scrub_environment()

    assert removed >= 3
    assert "OPENAI_API_KEY" not in os.environ
    assert "BITRIX_WEBHOOK_URL" not in os.environ
    assert os.environ["MCP_USER_WORKDIR"] == "kept"
    assert os.environ["PATH"] == "kept-path"
    upper_names = [name.upper() for name in os.environ]
    for marker in ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "WEBHOOK", "_KEY", "_DSN", "_PAT"):
        assert not any(marker in name for name in upper_names)
