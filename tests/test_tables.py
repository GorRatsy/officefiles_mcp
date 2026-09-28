from pathlib import Path

import pytest

from coding_mcp.errors import DocumentError
from coding_mcp.limits import MAX_ROWS
from coding_mcp.tables import analyze_table, calculate_table, read_table, write_table


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    return root


def test_csv_roundtrip(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = {"columns": ["Имя", "Сумма"], "rows": [["Анна", 2], ["Борис", 3.5]]}

    assert write_table(root, "out/people.csv", content) == "Записан файл out/people.csv"
    text = read_table(root, "out/people.csv")

    assert "Строк: 2" in text
    assert "Имя" in text
    assert "Анна\t2" in text
    assert "Борис\t3.5" in text


def test_write_table_accepts_json_string(tmp_path: Path) -> None:
    root = _root(tmp_path)

    write_table(root, "one.csv", '{"columns":["a"],"rows":[[1]]}')

    assert "a" in read_table(root, "one.csv")


def test_xlsx_sheet_roundtrip(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = {
        "sheets": [
            {"name": "Продажи", "columns": ["Имя", "Сумма"], "rows": [["Анна", 2], ["Борис", 5]]},
            {"name": "Пусто", "columns": ["Имя"], "rows": []},
        ]
    }

    write_table(root, "book.xlsx", content)
    text = read_table(root, "book.xlsx", sheet="Продажи")

    assert "Лист: Продажи" in text
    assert "Анна\t2" in text
    assert "Другие листы: Пусто" in text


def test_semicolon_and_cp1251_csv(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "ru.csv").write_text("Имя;Сумма\nАнна;1,5\n", encoding="utf-8")
    (root / "cp.csv").write_bytes("Имя;Сумма\nАнна;2\n".encode("cp1251"))

    assert "Анна\t1.5" in read_table(root, "ru.csv")
    assert "Анна\t2" in read_table(root, "cp.csv")


def test_xls_read_table(tmp_path: Path) -> None:
    import xlwt

    root = _root(tmp_path)
    book = xlwt.Workbook()
    sheet = book.add_sheet("Данные")
    sheet.write(0, 0, "Имя")
    sheet.write(0, 1, "Сумма")
    sheet.write(1, 0, "Анна")
    sheet.write(1, 1, 7)
    book.save(str(root / "old.xls"))

    text = read_table(root, "old.xls", sheet="Данные")

    assert "Анна\t7" in text


def test_analyze_groupby_and_describe(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_table(
        root,
        "sales.csv",
        {"columns": ["Имя", "Сумма"], "rows": [["Анна", 2], ["Анна", 4], ["Борис", 3]]},
    )

    text = analyze_table(
        root,
        "sales.csv",
        {"describe": True, "groupby": {"by": ["Имя"], "agg": {"Сумма": "sum"}}},
    )

    assert "mean=" in text
    assert "Анна" in text
    assert "6" in text
    assert "Борис" in text


def test_calculate_derive_filter_and_write(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_table(root, "sales.csv", {"columns": ["Имя", "Сумма"], "rows": [["Анна", 2], ["Борис", 5]]})

    text = calculate_table(
        root,
        "sales.csv",
        [
            {"op": "derive", "column": "vat", "expr": "Сумма * 0.2"},
            {"op": "filter", "column": "vat", "cmp": "gt", "value": 0.5},
        ],
        output="out/vat.csv",
    )

    assert text.startswith("Записан файл out/vat.csv")
    assert "Борис" in text
    assert "Анна" not in text.split("первые строки", maxsplit=1)[-1]
    saved = (root / "out" / "vat.csv").read_text(encoding="utf-8-sig")
    assert "Борис" in saved
    assert "1" in saved


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo hi')",
        "(Сумма).__class__.__bases__",
        "Сумма[0]",
        "(lambda: 1)()",
    ],
)
def test_derive_rejects_unsafe_expr(tmp_path: Path, expr: str) -> None:
    root = _root(tmp_path)
    write_table(root, "sales.csv", {"columns": ["Сумма"], "rows": [[2]]})

    with pytest.raises(DocumentError, match="выражен"):
        calculate_table(
            root,
            "sales.csv",
            [{"op": "derive", "column": "x", "expr": expr}],
            output="out.csv",
        )
    assert not (root / "out.csv").exists()


def test_write_rejects_too_many_rows(tmp_path: Path) -> None:
    root = _root(tmp_path)
    rows = [[index] for index in range(MAX_ROWS + 1)]

    with pytest.raises(DocumentError, match="Слишком много строк"):
        write_table(root, "big.csv", {"columns": ["n"], "rows": rows})
    assert not (root / "big.csv").exists()


def test_table_outside_root_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(DocumentError, match="вне рабочей директории"):
        write_table(root, str(tmp_path / "x.csv"), {"columns": ["a"], "rows": [[1]]})
