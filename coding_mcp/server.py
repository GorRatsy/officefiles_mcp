"""Stdio MCP. В tools/list только чтение и запись файлов рабочей директории."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from mcp.server.fastmcp import FastMCP

from coding_mcp.documents import read_docx, read_pdf, read_xls, read_xlsx, write_docx, write_xlsx
from coding_mcp.errors import DocumentError
from coding_mcp.jail import load_root, scrub_environment
from coding_mcp.runtime import run_bounded

INSTRUCTIONS = """\
Рабочая директория уже выбрана. Передавай только относительные пути внутри неё, например report.pdf или out/table.xlsx.
Путь снаружи каталога отклоняется.
read_pdf — текст PDF по страницам. Скан без текстового слоя читается как пустая страница.
read_docx — абзацы и таблицы DOCX. Картинки пропускаются.
write_docx — записать DOCX. Строка "# Заголовок" становится заголовком, "## " и "### " тоже. Остальные непустые строки — абзацы.
read_xlsx — листы XLSX, ячейки через табуляцию. Формула возвращается текстом, без пересчёта значения.
write_xlsx — записать XLSX. content: {"sheets":[{"name":"Лист1","rows":[["Имя",1],["Анна",2]]}]}.
read_xls — чтение старого XLS. Записи XLS нет.
Не поддерживаются doc, ppt, pptx, список каталога, сеть и запуск кода.\
"""

TOOL_NAMES = (
    "read_pdf",
    "read_docx",
    "write_docx",
    "read_xlsx",
    "write_xlsx",
    "read_xls",
)

log = logging.getLogger("coding_mcp")


def create_server(root: Path) -> FastMCP:
    mcp = FastMCP("coding-mcp", instructions=INSTRUCTIONS)

    @mcp.tool(name="read_pdf")
    def read_pdf_tool(path: str) -> str:
        """Прочитать текст PDF. path — относительный путь внутри рабочей директории, например report.pdf."""

        return _call(lambda: read_pdf(root, path))

    @mcp.tool(name="read_docx")
    def read_docx_tool(path: str) -> str:
        """Прочитать абзацы и таблицы DOCX. path — относительный путь, например notes.docx."""

        return _call(lambda: read_docx(root, path))

    @mcp.tool(name="write_docx")
    def write_docx_tool(path: str, text: str) -> str:
        """Записать DOCX. path — относительный путь. text — строки: '# Заголовок' для заголовка, остальное абзацы."""

        return _call(lambda: write_docx(root, path, text))

    @mcp.tool(name="read_xlsx")
    def read_xlsx_tool(path: str, sheet: str | None = None) -> str:
        """Прочитать XLSX. path — относительный путь. sheet — имя листа; без него читаются первые листы."""

        return _call(lambda: read_xlsx(root, path, sheet))

    @mcp.tool(name="write_xlsx")
    def write_xlsx_tool(path: str, content: dict | str) -> str:
        """Записать XLSX. content — объект {"sheets":[{"name":"Лист1","rows":[["A",1]]}]} или такая же JSON-строка."""

        return _call(lambda: write_xlsx(root, path, content))

    @mcp.tool(name="read_xls")
    def read_xls_tool(path: str, sheet: str | None = None) -> str:
        """Прочитать старый XLS. path — относительный путь. sheet — имя листа; без него читаются первые листы. Записи нет."""

        return _call(lambda: read_xls(root, path, sheet))

    return mcp


def _call(func):
    try:
        return run_bounded(func)
    except DocumentError:
        raise
    except Exception:
        log.exception("сбой обработки файла")
        raise DocumentError("Не удалось обработать файл.") from None


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="coding-mcp %(message)s")
    parser = argparse.ArgumentParser(prog="coding-mcp")
    parser.add_argument(
        "--root",
        help="Каталог рабочей директории. По умолчанию берётся MCP_USER_WORKDIR.",
    )
    args = parser.parse_args(argv)
    raw = args.root or os.environ.get("MCP_USER_WORKDIR")
    if not raw:
        parser.error("укажите --root или переменную MCP_USER_WORKDIR")
    try:
        root = load_root(raw)
    except DocumentError as exc:
        print(f"coding-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    removed = scrub_environment()
    if removed:
        log.info("из окружения убраны переменные с секретами: %s", removed)
    os.chdir(root)
    log.info("stdio готов")
    create_server(root).run(transport="stdio")


if __name__ == "__main__":
    main()
