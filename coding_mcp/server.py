"""Stdio MCP. Чтение и создание файлов рабочей директории, картинок и таблиц."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from mcp.server.fastmcp import FastMCP, Image

from coding_mcp.documents import read_docx, read_pdf, read_xls, read_xlsx, write_docx, write_xlsx
from coding_mcp.errors import DocumentError
from coding_mcp.images import read_image, write_image
from coding_mcp.jail import load_root, scrub_environment
from coding_mcp.runtime import run_bounded
from coding_mcp.tables import analyze_table, calculate_table, read_table, write_table

INSTRUCTIONS = """\
Рабочая директория уже выбрана. Передавай только относительные пути внутри неё, например report.pdf или out/table.xlsx.
Путь снаружи каталога отклоняется.
read_pdf — текст PDF по страницам. Скан без текстового слоя читается как пустая страница.
read_docx — абзацы и таблицы DOCX. Картинки пропускаются.
write_docx — записать DOCX. Строка "# Заголовок" становится заголовком, "## " и "### " тоже. Остальные непустые строки — абзацы.
read_xlsx — листы XLSX, ячейки через табуляцию. Формула возвращается текстом, без пересчёта значения.
write_xlsx — записать XLSX. content: {"sheets":[{"name":"Лист1","rows":[["Имя",1],["Анна",2]]}]}.
read_xls — чтение старого XLS. Записи XLS нет.
read_image — показать PNG, JPEG, GIF, WEBP или BMP: размер, режим и сама картинка.
write_image — создать картинку или перекодировать source. spec: {"width":640,"height":480,"background":"#FFFFFF","items":[{"kind":"rectangle","x":10,"y":10,"width":100,"height":40,"fill":"#336699"},{"kind":"text","x":20,"y":60,"text":"Отчёт","size":32}]}.
read_table — прочитать CSV, TSV, XLSX или XLS через pandas: размер, типы, пропуски и первые строки.
analyze_table — сводка pandas. spec может содержать describe, missing, value_counts, corr и groupby: {"by":["region"],"agg":{"amount":"sum"}}.
calculate_table — шаги filter, derive, sort, select, aggregate, rename, fillna, dropna, head. derive считает только арифметику по столбцам, например amount * 0.2. output сохраняет CSV, TSV или XLSX.
write_table — создать CSV, TSV или XLSX. content: {"columns":["Имя","Сумма"],"rows":[["Анна",2]]}.
Не поддерживаются doc, ppt, pptx, список каталога, сеть и произвольный код.\
"""

TOOL_NAMES = (
    "read_pdf",
    "read_docx",
    "write_docx",
    "read_xlsx",
    "write_xlsx",
    "read_xls",
    "read_image",
    "write_image",
    "read_table",
    "analyze_table",
    "calculate_table",
    "write_table",
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

    @mcp.tool(name="read_image", structured_output=False)
    def read_image_tool(path: str):
        """Показать PNG, JPEG, GIF, WEBP или BMP. path — относительный путь, например photo.png."""

        def run():
            payload = read_image(root, path)
            if payload.data is None or payload.image_format is None:
                return payload.text
            return payload.text, Image(data=payload.data, format=payload.image_format)

        return _call(run)

    @mcp.tool(name="write_image")
    def write_image_tool(path: str, spec: dict | str) -> str:
        """Создать PNG, JPEG, GIF, WEBP или BMP. spec — width, height, background и items (rectangle, ellipse, line, text, bars). source копирует существующую картинку."""

        return _call(lambda: write_image(root, path, spec))

    @mcp.tool(name="read_table")
    def read_table_tool(path: str, sheet: str | None = None, header: bool = True) -> str:
        """Прочитать CSV, TSV, XLSX или XLS через pandas: число строк, типы столбцов, пропуски и первые строки."""

        return _call(lambda: read_table(root, path, sheet, header))

    @mcp.tool(name="analyze_table")
    def analyze_table_tool(
        path: str,
        spec: dict | str | None = None,
        sheet: str | None = None,
        header: bool = True,
    ) -> str:
        """Посчитать сводку pandas: describe, пропуски, value_counts, corr или groupby. Без spec — пропуски и describe."""

        return _call(lambda: analyze_table(root, path, spec, sheet, header))

    @mcp.tool(name="calculate_table")
    def calculate_table_tool(
        path: str,
        operations: list | str,
        sheet: str | None = None,
        header: bool = True,
        output: str | None = None,
    ) -> str:
        """Преобразовать таблицу шагами filter, derive, sort, select, aggregate, rename, fillna, dropna, head. output — куда записать CSV, TSV или XLSX."""

        return _call(lambda: calculate_table(root, path, operations, sheet, header, output))

    @mcp.tool(name="write_table")
    def write_table_tool(path: str, content: dict | str) -> str:
        """Создать CSV, TSV или XLSX. content — {"columns":["Имя","Сумма"],"rows":[["Анна",2]]} или sheets для нескольких листов XLSX."""

        return _call(lambda: write_table(root, path, content))

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
