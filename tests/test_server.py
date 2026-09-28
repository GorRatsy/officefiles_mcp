import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from coding_mcp.limits import (
    CALL_TIMEOUT_SECONDS,
    MAX_CELL_CHARS,
    MAX_COLS,
    MAX_FILE_BYTES,
    MAX_INPUT_CHARS,
    MAX_OUTPUT_CHARS,
    MAX_PDF_PAGES,
    MAX_ROWS,
    MAX_SHEETS,
    MAX_UNCOMPRESSED_BYTES,
)
from coding_mcp.server import TOOL_NAMES, create_server

ROOT = Path(__file__).resolve().parents[1]


def test_documented_limits() -> None:
    assert MAX_FILE_BYTES == 20 * 1024 * 1024
    assert MAX_UNCOMPRESSED_BYTES == 80 * 1024 * 1024
    assert MAX_OUTPUT_CHARS == 80_000
    assert MAX_INPUT_CHARS == 1_000_000
    assert MAX_CELL_CHARS == 2_000
    assert MAX_SHEETS == 10
    assert MAX_ROWS == 2_000
    assert MAX_COLS == 40
    assert MAX_PDF_PAGES == 50
    assert CALL_TIMEOUT_SECONDS == 20.0


def test_registered_tools_match_documentation(tmp_path: Path) -> None:
    server = create_server(tmp_path)
    names = tuple(server._tool_manager.list_tools())
    tool_names = tuple(tool.name for tool in names)
    assert tool_names == TOOL_NAMES


def test_office_libraries_are_not_imported_at_startup() -> None:
    code = (
        "import coding_mcp.server, sys\n"
        "names = {'pypdf', 'docx', 'openpyxl', 'xlrd'}\n"
        "found = [name for name in sys.modules if name.split('.')[0] in names]\n"
        "print(','.join(found))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.stdout.strip() == ""


def test_process_exits_without_root(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("MCP_USER_WORKDIR", None)
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "coding_mcp", "--root", str(tmp_path / "missing")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 2
    assert "coding-mcp:" in completed.stderr
    assert completed.stdout == ""


def test_stdio_lists_only_documented_tools(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env["MCP_USER_WORKDIR"] = str(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "coding_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        initialized = _read_response(process, 1)
        assert initialized["id"] == 1
        assert "serverInfo" in initialized["result"]
        _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _read_response(process, 2)
        names = tuple(tool["name"] for tool in listed["result"]["tools"])
        assert names == TOOL_NAMES
    finally:
        process.kill()
        process.wait(timeout=5)


def _send(process: subprocess.Popen[str], message: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def _read_response(process: subprocess.Popen[str], request_id: int) -> dict:
    deadline = threading.Timer(10, process.kill)
    deadline.start()
    assert process.stdout is not None
    try:
        while True:
            line = process.stdout.readline()
            if not line:
                stderr = process.stderr.read() if process.stderr is not None else ""
                pytest.fail(f"сервер закрыл stdout: {stderr}")
            message = json.loads(line)
            if message.get("id") == request_id:
                return message
    finally:
        deadline.cancel()
