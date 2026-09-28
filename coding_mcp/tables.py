"""Чтение, расчёт и создание таблиц через pandas. Библиотека импортируется внутри функций."""

from __future__ import annotations

import ast
import operator
from pathlib import Path

from coding_mcp.documents import _atomic_save, _sheet_name, _strip_controls, _write_cell
from coding_mcp.errors import DocumentError
from coding_mcp.jail import open_for_read_types, prepare_write_types, relative_name, resolve_in_root
from coding_mcp.limits import (
    MAX_CELL_CHARS,
    MAX_COLS,
    MAX_EXPR_CHARS,
    MAX_ROWS,
    MAX_SHEETS,
    MAX_TABLE_OPS,
    MAX_VALUE_COUNTS,
    TABLE_PREVIEW_ROWS,
)
from coding_mcp.runtime import clip, load_list, load_object

READ_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xls"})
WRITE_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx"})
_AGG = frozenset({"sum", "mean", "min", "max", "count", "median", "nunique"})
_CMP = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "contains", "not_contains"})
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def read_table(root: Path, raw: str, sheet: str | None = None, header: bool = True) -> str:
    frame, note = _load(root, raw, sheet, header)
    return clip(_render(frame, Path(raw).name, note))


def analyze_table(
    root: Path,
    raw: str,
    spec: dict | str | None = None,
    sheet: str | None = None,
    header: bool = True,
) -> str:
    frame, note = _load(root, raw, sheet, header)
    options = _analyze_options(spec)
    if options["auto_counts"]:
        options["value_counts"] = _auto_value_counts(frame)
    if not any((options["missing"], options["describe"], options["value_counts"], options["corr"], options["groupby"])):
        raise DocumentError("Не задана ни одна операция анализа.")
    lines = [f"## {Path(raw).name}", f"Строк: {len(frame)}", f"Столбцов: {len(frame.columns)}"]
    if note:
        lines.append(note)
    if options["missing"]:
        lines.extend(_missing_lines(frame))
    if options["describe"]:
        lines.extend(_describe_lines(frame))
    for column in options["value_counts"]:
        lines.extend(_count_lines(frame, column))
    if options["corr"]:
        lines.extend(_corr_lines(frame))
    if options["groupby"] is not None:
        lines.extend(_groupby_lines(frame, options["groupby"]))
    return clip("\n".join(lines).rstrip())


def calculate_table(
    root: Path,
    raw: str,
    operations: list | str,
    sheet: str | None = None,
    header: bool = True,
    output: str | None = None,
) -> str:
    if output is not None:
        checked = resolve_in_root(root, output)
        if checked.suffix.lower() not in WRITE_SUFFIXES:
            shown = ", ".join(sorted(WRITE_SUFFIXES))
            raise DocumentError(f"Ожидается файл {shown}.")
    steps = _operations(operations)
    frame, note = _load(root, raw, sheet, header)
    result = _apply(frame, steps)
    saved = ""
    if output is not None:
        saved = write_frame(root, output, result, sheet_name=_output_sheet(sheet))
    text = _render(result, Path(raw).name, "")
    if note:
        text = f"{note}\n\n{text}"
    if saved:
        text = f"{saved}\n\n{text}"
    return clip(text)


def write_table(root: Path, raw: str, content: dict | str) -> str:
    path = prepare_write_types(root, raw, WRITE_SUFFIXES)
    frames = _frames_from_content(load_object(content), path.suffix.lower())
    _write_frames(path, frames)
    return f"Записан файл {relative_name(root, path)}"


def write_frame(root: Path, raw: str, frame, sheet_name: str = "Лист1") -> str:
    path = prepare_write_types(root, raw, WRITE_SUFFIXES)
    _write_frames(path, [(sheet_name, frame)])
    return f"Записан файл {relative_name(root, path)}"


def _load(root: Path, raw: str, sheet: str | None, header: bool):
    if not isinstance(header, bool):
        raise DocumentError("header должен быть true или false.")
    path = open_for_read_types(root, raw, READ_SUFFIXES)
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        if sheet is not None:
            raise DocumentError("Лист задаётся только для Excel.")
        frame = _read_separated(path, suffix, header)
        note = ""
    else:
        frame, note = _read_excel(path, suffix, sheet, header)
    frame, column_note = _limit_frame(frame)
    truncated = ""
    if len(frame) > MAX_ROWS:
        frame = frame.iloc[:MAX_ROWS].copy()
        truncated = f"[строки после {MAX_ROWS} пропущены]"
    notes = " ".join(part for part in (note, column_note, truncated) if part)
    return frame, notes


def _read_separated(path: Path, suffix: str, header: bool):
    import pandas as pd

    separator, decimal = _separator(path, suffix)
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return pd.read_csv(
                path,
                sep=separator,
                decimal=decimal,
                header=0 if header else None,
                nrows=MAX_ROWS + 1,
                encoding=encoding,
            )
        except UnicodeError as exc:
            last_error = exc
            continue
        except pd.errors.EmptyDataError:
            raise DocumentError("Таблица пустая.") from None
        except pd.errors.ParserError:
            raise DocumentError("Не удалось прочитать таблицу.") from None
    raise DocumentError("Не удалось прочитать таблицу.") from last_error


def _read_excel(path: Path, suffix: str, sheet: str | None, header: bool):
    import pandas as pd

    engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
    try:
        book = pd.ExcelFile(path, engine=engine)
    except Exception:
        raise DocumentError("Не удалось прочитать таблицу.") from None
    try:
        names = [str(name) for name in book.sheet_names]
        if not names:
            raise DocumentError("В книге нет листов.")
        if sheet is None:
            selected = names[0]
        else:
            if not isinstance(sheet, str) or not sheet.strip():
                raise DocumentError("Имя листа пустое.")
            if sheet not in names:
                raise DocumentError("Лист не найден.")
            selected = sheet
        frame = book.parse(sheet_name=selected, header=0 if header else None, nrows=MAX_ROWS + 1)
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("Не удалось прочитать таблицу.") from None
    finally:
        book.close()
    others = [name for name in names[:MAX_SHEETS] if name != selected]
    note = f"Лист: {selected}"
    if others:
        note = f"{note}. Другие листы: {', '.join(others)}"
    if len(names) > MAX_SHEETS:
        note = f"{note}. [листы после первых {MAX_SHEETS} пропущены]"
    return frame, note


def _separator(path: Path, suffix: str) -> tuple[str, str]:
    if suffix == ".tsv":
        return "\t", "."
    line = _sample_line(path)
    if line.count(";") > line.count(","):
        return ";", ","
    return ",", "."


def _sample_line(path: Path) -> str:
    raw = path.read_bytes()[:8192]
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        lines = [line for line in text.splitlines() if line.strip()]
        if lines:
            return lines[0]
    return ""


def _limit_frame(frame):
    note = ""
    if len(frame.columns) > MAX_COLS:
        frame = frame.iloc[:, :MAX_COLS].copy()
        note = f"[столбцы после первых {MAX_COLS} пропущены]"
    columns = [_column_name(column, index) for index, column in enumerate(frame.columns)]
    if len(set(columns)) != len(columns):
        raise DocumentError("Имена столбцов повторяются.")
    frame = frame.copy()
    frame.columns = columns
    return frame, note


def _column_name(value: object, index: int) -> str:
    if value is None:
        return f"column_{index + 1}"
    text = _strip_controls(str(value)).replace("\n", " ").replace("\t", " ").strip()
    if not text:
        return f"column_{index + 1}"
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS]
    return text


def _analyze_options(spec: dict | str | None) -> dict:
    if spec is None:
        return {
            "missing": True,
            "describe": True,
            "value_counts": [],
            "corr": False,
            "groupby": None,
            "auto_counts": True,
        }
    payload = load_object(spec)
    groupby = payload.get("groupby")
    if groupby is not None and not isinstance(groupby, dict):
        raise DocumentError("groupby должен быть объектом.")
    counts = payload.get("value_counts") or []
    if isinstance(counts, str):
        counts = [counts]
    if not isinstance(counts, list) or not all(isinstance(item, str) for item in counts):
        raise DocumentError("value_counts должен быть списком столбцов.")
    return {
        "missing": bool(payload.get("missing", False)),
        "describe": bool(payload.get("describe", False)),
        "value_counts": counts,
        "corr": bool(payload.get("corr", False)),
        "groupby": groupby,
        "auto_counts": False,
    }


def _missing_lines(frame) -> list[str]:
    lines = ["", "## пропуски"]
    for column in frame.columns:
        lines.append(f"{column}\t{int(frame[column].isna().sum())}")
    return lines


def _describe_lines(frame) -> list[str]:
    import pandas as pd

    numeric = frame.select_dtypes(include="number")
    numeric = numeric.drop(columns=[column for column in numeric.columns if numeric[column].dtype == bool], errors="ignore")
    lines = ["", "## describe"]
    if numeric.empty:
        lines.append("[числовых столбцов нет]")
        return lines
    for column in numeric.columns:
        series = pd.to_numeric(numeric[column], errors="coerce")
        lines.append(
            f"{column}\tcount={int(series.count())}\tmean={_num(series.mean())}\t"
            f"min={_num(series.min())}\tmax={_num(series.max())}\tmedian={_num(series.median())}"
        )
    return lines


def _count_lines(frame, column: str) -> list[str]:
    _require_column(frame, column)
    counts = frame[column].value_counts(dropna=False).head(MAX_VALUE_COUNTS)
    lines = ["", f"## value_counts {column}"]
    for key, count in counts.items():
        lines.append(f"{_preview_cell(key)}\t{int(count)}")
    return lines


def _corr_lines(frame) -> list[str]:
    import pandas as pd

    numeric = frame.select_dtypes(include="number")
    lines = ["", "## corr"]
    if numeric.shape[1] < 2:
        lines.append("[для корреляции нужно хотя бы два числовых столбца]")
        return lines
    corr = numeric.corr(numeric_only=True).round(4)
    header = "\t" + "\t".join(str(column) for column in corr.columns)
    lines.append(header)
    for name, row in corr.iterrows():
        values = "\t".join("" if pd.isna(value) else _num(value) for value in row.tolist())
        lines.append(f"{name}\t{values}")
    return lines


def _groupby_lines(frame, spec: dict) -> list[str]:
    grouped = _aggregate(frame, spec)
    lines = ["", "## groupby"]
    lines.append(_preview_header(grouped))
    for _, row in grouped.head(TABLE_PREVIEW_ROWS).iterrows():
        lines.append(_preview_row(row.tolist()))
    if len(grouped) > TABLE_PREVIEW_ROWS:
        lines.append(f"[показаны первые {TABLE_PREVIEW_ROWS} групп из {len(grouped)}]")
    return lines


def _operations(content: list | str) -> list[dict]:
    parsed = load_list(content)
    if not parsed:
        raise DocumentError("Нужен непустой список operations.")
    if len(parsed) > MAX_TABLE_OPS:
        raise DocumentError(f"Слишком много операций, максимум {MAX_TABLE_OPS}.")
    steps: list[dict] = []
    for step in parsed:
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            raise DocumentError("Операция должна быть объектом с полем op.")
        steps.append(step)
    return steps


def _apply(frame, steps: list[dict]):
    result = frame
    for step in steps:
        result = _apply_step(result, step)
        if len(result) > MAX_ROWS:
            raise DocumentError(f"Слишком много строк, максимум {MAX_ROWS}.")
        if len(result.columns) > MAX_COLS:
            raise DocumentError(f"Слишком много столбцов, максимум {MAX_COLS}.")
    return result


def _apply_step(frame, step: dict):
    op = step["op"]
    if op == "filter":
        return _filter(frame, step)
    if op == "derive":
        return _derive(frame, step)
    if op == "sort":
        columns = _name_list(step.get("column") if "column" in step else step.get("columns"), "column")
        for column in columns:
            _require_column(frame, column)
        ascending = step.get("ascending", True)
        if not isinstance(ascending, bool):
            raise DocumentError("ascending должен быть true или false.")
        return frame.sort_values(columns, ascending=ascending, kind="mergesort")
    if op == "select":
        columns = _name_list(step.get("columns"), "columns")
        for column in columns:
            _require_column(frame, column)
        return frame.loc[:, columns].copy()
    if op == "aggregate":
        return _aggregate(frame, step)
    if op == "rename":
        mapping = step.get("columns")
        if not isinstance(mapping, dict) or not mapping:
            raise DocumentError("rename требует объект columns.")
        renamed = {}
        for key, value in mapping.items():
            old = _as_column(key)
            new = _as_column(value)
            _require_column(frame, old)
            renamed[old] = new
        result = frame.rename(columns=renamed)
        if result.columns.duplicated().any():
            raise DocumentError("Имена столбцов повторяются.")
        return result
    if op == "fillna":
        if "value" not in step:
            raise DocumentError("fillna требует поле value.")
        value = _write_cell(step.get("value"))
        if "column" in step:
            column = _as_column(step.get("column"))
            _require_column(frame, column)
            result = frame.copy()
            result[column] = result[column].fillna(value)
            return result
        return frame.fillna(value)
    if op == "dropna":
        columns = step.get("columns")
        subset = None
        if columns is not None:
            subset = _name_list(columns, "columns")
            for column in subset:
                _require_column(frame, column)
        how = step.get("how") or "any"
        if how not in {"any", "all"}:
            raise DocumentError("how должен быть any или all.")
        return frame.dropna(subset=subset, how=how)
    if op == "head":
        count = _whole_int(step.get("n"), "n", 1, MAX_ROWS)
        return frame.head(count).copy()
    raise DocumentError("Неизвестная операция.")


def _filter(frame, step: dict):
    column = _as_column(step.get("column"))
    _require_column(frame, column)
    cmp = step.get("cmp")
    if cmp not in _CMP and cmp not in {"empty", "not_empty"}:
        raise DocumentError("Неизвестное сравнение.")
    series = frame[column]
    if cmp == "empty":
        mask = series.isna() | (series.astype(str).str.strip() == "")
    elif cmp == "not_empty":
        mask = ~(series.isna() | (series.astype(str).str.strip() == ""))
    elif cmp in {"contains", "not_contains"}:
        value = step.get("value")
        if not isinstance(value, str):
            raise DocumentError("Для contains нужно текстовое value.")
        if len(value) > MAX_CELL_CHARS:
            raise DocumentError("Слишком длинное значение.")
        found = series.astype(str).str.contains(value, regex=False, na=False)
        mask = found if cmp == "contains" else ~found
    else:
        value = _filter_value(step.get("value"))
        if cmp in {"gt", "ge", "lt", "le"}:
            series = _numeric_series(series)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise DocumentError("Для сравнения нужно число.")
        mask = _compare(series, cmp, value)
    return frame.loc[mask].copy()


def _compare(series, cmp: str, value: object):
    if value is None and cmp == "eq":
        return series.isna()
    if value is None and cmp == "ne":
        return series.notna()
    if cmp == "eq":
        return series == value
    if cmp == "ne":
        return series != value
    if cmp == "gt":
        return series > value
    if cmp == "ge":
        return series >= value
    if cmp == "lt":
        return series < value
    return series <= value


def _derive(frame, step: dict):
    column = _as_column(step.get("column"))
    expr = step.get("expr")
    if not isinstance(expr, str) or not expr.strip():
        raise DocumentError("Выражение пустое.")
    result = frame.copy()
    result[column] = _eval_expr(frame, expr)
    return result


def _aggregate(frame, spec: dict):
    by = _name_list(spec.get("by"), "by")
    for column in by:
        _require_column(frame, column)
    agg = spec.get("agg")
    if not isinstance(agg, dict) or not agg:
        raise DocumentError("aggregate требует объект agg.")
    prepared: dict[str, list[str]] = {}
    for key, value in agg.items():
        column = _as_column(key)
        _require_column(frame, column)
        functions = value if isinstance(value, list) else [value]
        if not functions or not all(isinstance(item, str) and item in _AGG for item in functions):
            raise DocumentError("Агрегация должна быть sum, mean, min, max, count, median или nunique.")
        if column not in by and any(item != "count" for item in functions):
            frame = frame.copy()
            frame[column] = _numeric_series(frame[column])
        prepared[column] = functions
    try:
        grouped = frame.groupby(by, dropna=False).agg(prepared)
    except (TypeError, ValueError):
        raise DocumentError("Не удалось посчитать агрегацию.") from None
    if getattr(grouped.columns, "nlevels", 1) > 1:
        grouped.columns = [_flat_column(column) for column in grouped.columns]
    grouped = grouped.reset_index()
    if len(grouped) > MAX_ROWS:
        raise DocumentError(f"Слишком много строк, максимум {MAX_ROWS}.")
    return grouped


def _flat_column(column: object) -> str:
    if isinstance(column, tuple):
        parts = [str(part) for part in column if part != ""]
        return "_".join(parts)
    return str(column)


def _eval_expr(frame, expr: str):
    """Считает арифметику по столбцам. Произвольный код и обращения к атрибутам отклоняются."""

    import pandas as pd

    if len(expr) > MAX_EXPR_CHARS:
        raise DocumentError("Слишком длинное выражение.")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise DocumentError("Некорректное выражение.") from None
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise DocumentError("Слишком сложное выражение.")
    try:
        value = _eval_node(tree.body, frame)
    except DocumentError:
        raise
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, FloatingPointError):
        raise DocumentError("Не удалось вычислить выражение.") from None
    if not isinstance(value, pd.Series):
        value = pd.Series([value] * len(frame), index=frame.index)
    numeric = pd.to_numeric(value, errors="coerce")
    if ((numeric == float("inf")) | (numeric == float("-inf"))).any():
        raise DocumentError("Результат выражения не конечный.")
    return value


def _eval_node(node: ast.AST, frame):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or node.value is None:
            raise DocumentError("В выражении допустимы числа и имена столбцов.")
        if isinstance(node.value, (int, float)):
            if isinstance(node.value, float) and (node.value != node.value or node.value in {float("inf"), float("-inf")}):
                raise DocumentError("Недопустимое число в выражении.")
            return node.value
        raise DocumentError("В выражении допустимы числа и имена столбцов.")
    if isinstance(node, ast.Name):
        if node.id not in frame.columns:
            raise DocumentError("Столбец в выражении не найден.")
        return frame[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_node(node.operand, frame)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left, frame)
        right = _eval_node(node.right, frame)
        if isinstance(node.op, ast.Pow):
            _guard_pow(right)
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.Call):
        if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
            raise DocumentError("Функция в выражении не поддерживается.")
        if not isinstance(node.func, ast.Name) or node.func.id not in {"abs", "round"}:
            raise DocumentError("Функция в выражении не поддерживается.")
        args = [_eval_node(arg, frame) for arg in node.args]
        return _call_func(node.func.id, args)
    raise DocumentError("Недопустимое выражение.")


def _call_func(name: str, args: list):
    import pandas as pd

    if name == "abs":
        if len(args) != 1:
            raise DocumentError("abs принимает один аргумент.")
        value = args[0]
        if isinstance(value, pd.Series):
            return value.abs()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DocumentError("abs применяется к числу.")
        return abs(value)
    if len(args) not in {1, 2}:
        raise DocumentError("round принимает значение и необязательное число знаков.")
    digits = 0 if len(args) == 1 else args[1]
    if isinstance(digits, pd.Series) or isinstance(digits, bool) or not isinstance(digits, (int, float)):
        raise DocumentError("Число знаков округления должно быть числом.")
    if isinstance(digits, float) and not digits.is_integer():
        raise DocumentError("Число знаков округления должно быть числом.")
    digits = int(digits)
    if digits < 0 or digits > 8:
        raise DocumentError("Число знаков округления вне диапазона.")
    value = args[0]
    if isinstance(value, pd.Series):
        return value.round(digits)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError("round применяется к числу.")
    return round(value, digits)


def _guard_pow(right: object) -> None:
    import pandas as pd

    if isinstance(right, pd.Series):
        values = right.tolist()
    else:
        values = [right]
    if len(values) > MAX_ROWS:
        raise DocumentError("Степень должна быть от 0 до 6.")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DocumentError("Степень должна быть небольшим целым числом.")
        if isinstance(value, float) and not float(value).is_integer():
            raise DocumentError("Степень должна быть небольшим целым числом.")
        if int(value) < 0 or int(value) > 6:
            raise DocumentError("Степень должна быть от 0 до 6.")


def _frames_from_content(payload: dict, suffix: str) -> list[tuple[str, object]]:
    if "sheets" in payload:
        sheets = payload.get("sheets")
        if not isinstance(sheets, list) or not sheets:
            raise DocumentError("Нужен непустой список sheets.")
        if len(sheets) > MAX_SHEETS:
            raise DocumentError(f"Слишком много листов, максимум {MAX_SHEETS}.")
        if suffix != ".xlsx" and len(sheets) != 1:
            raise DocumentError("В CSV и TSV можно записать только один лист.")
        ready = []
        seen: set[str] = set()
        for sheet in sheets:
            if not isinstance(sheet, dict):
                raise DocumentError("Описание листа должно быть объектом.")
            name = _sheet_name(sheet.get("name"))
            if name in seen:
                raise DocumentError("Имена листов повторяются.")
            seen.add(name)
            ready.append((name, _frame_from_table(sheet)))
        return ready
    name = "Лист1"
    if suffix == ".xlsx" and payload.get("name") is not None:
        name = _sheet_name(payload.get("name"))
    return [(name, _frame_from_table(payload))]


def _frame_from_table(payload: dict):
    import pandas as pd

    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not columns:
        raise DocumentError("Нужен непустой список columns.")
    if len(columns) > MAX_COLS:
        raise DocumentError(f"Слишком много столбцов, максимум {MAX_COLS}.")
    if not isinstance(rows, list):
        raise DocumentError("Нужен список rows.")
    if len(rows) > MAX_ROWS:
        raise DocumentError(f"Слишком много строк, максимум {MAX_ROWS}.")
    names: list[str] = []
    for column in columns:
        if not isinstance(column, str) or not column.strip():
            raise DocumentError("Имя столбца пустое.")
        name = _strip_controls(column).strip()
        if not name or len(name) > MAX_CELL_CHARS:
            raise DocumentError("Недопустимое имя столбца.")
        names.append(name)
    if len(set(names)) != len(names):
        raise DocumentError("Имена столбцов повторяются.")
    cleaned: list[list[object]] = []
    width = len(names)
    for row in rows:
        if not isinstance(row, list):
            raise DocumentError("Строка таблицы должна быть списком ячеек.")
        if len(row) > width:
            raise DocumentError("Длина строки больше числа столбцов.")
        values = [_write_cell(value) for value in row]
        if len(values) < width:
            values.extend([None] * (width - len(values)))
        cleaned.append(values)
    return pd.DataFrame(cleaned, columns=names)


def _write_frames(path: Path, frames: list[tuple[str, object]]) -> None:
    suffix = path.suffix.lower()

    def save(target: Path) -> None:
        if suffix == ".xlsx":
            _write_xlsx(target, frames)
            return
        _write_separated(target, frames[0][1], "\t" if suffix == ".tsv" else ",")

    _atomic_save(path, save)


def _write_xlsx(target: Path, frames: list[tuple[str, object]]) -> None:
    import pandas as pd

    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for name, frame in frames:
            frame.to_excel(writer, sheet_name=name, index=False)


def _write_separated(target: Path, frame, separator: str) -> None:
    frame.to_csv(target, index=False, sep=separator, encoding="utf-8-sig", lineterminator="\n", na_rep="")


def _render(frame, title: str, note: str) -> str:
    lines = [
        f"## {title}",
        f"Строк: {len(frame)}",
        f"Столбцов: {len(frame.columns)}",
    ]
    if note:
        lines.append(note)
    lines.append("")
    lines.append("## столбцы")
    for column in frame.columns:
        missing = int(frame[column].isna().sum())
        lines.append(f"{column}\t{frame[column].dtype}\tпропусков={missing}")
    lines.append("")
    lines.append("## первые строки")
    lines.append(_preview_header(frame))
    preview = frame.head(TABLE_PREVIEW_ROWS)
    for _, row in preview.iterrows():
        lines.append(_preview_row(row.tolist()))
    if len(frame) > TABLE_PREVIEW_ROWS:
        lines.append(f"[показаны первые {TABLE_PREVIEW_ROWS} строк из {len(frame)}]")
    return "\n".join(lines).rstrip()


def _preview_header(frame) -> str:
    return "\t".join(str(column) for column in frame.columns)


def _preview_row(values: list) -> str:
    return "\t".join(_preview_cell(value) for value in values)


def _preview_cell(value: object) -> str:
    import pandas as pd

    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _num(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    text = _strip_controls(str(value)).replace("\t", " ").replace("\n", " ")
    if len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS] + "…"
    return text


def _num(value: object) -> str:
    import pandas as pd

    if value is None or bool(pd.isna(value)):
        return ""
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return ""
    if number.is_integer():
        return str(int(number))
    return format(number, ".6g")


def _numeric_series(series):
    import pandas as pd

    converted = pd.to_numeric(series, errors="coerce")
    if int(converted.notna().sum()) == 0 and int(series.notna().sum()) > 0:
        raise DocumentError("Столбец не числовой.")
    return converted


def _require_column(frame, column: str) -> None:
    if column not in frame.columns:
        raise DocumentError("Столбец не найден.")


def _as_column(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocumentError("Имя столбца пустое.")
    name = _strip_controls(value).strip()
    if not name or len(name) > MAX_CELL_CHARS:
        raise DocumentError("Недопустимое имя столбца.")
    return name


def _name_list(value: object, field: str) -> list[str]:
    if isinstance(value, str):
        return [_as_column(value)]
    if not isinstance(value, list) or not value:
        raise DocumentError(f"Поле {field} должно быть столбцом или списком столбцов.")
    return [_as_column(item) for item in value]


def _whole_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError(f"{name} должно быть целым числом.")
    if isinstance(value, float) and not value.is_integer():
        raise DocumentError(f"{name} должно быть целым числом.")
    number = int(value)
    if number < low or number > high:
        raise DocumentError(f"{name} должно быть от {low} до {high}.")
    return number


def _filter_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise DocumentError("Недопустимое число.")
        if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
            raise DocumentError("Слишком длинное значение.")
        return value
    raise DocumentError("value должен быть строкой, числом, bool или null.")


def _output_sheet(sheet: str | None) -> str:
    if sheet is None:
        return "Лист1"
    return _sheet_name(sheet)


def _auto_value_counts(frame) -> list[str]:
    import pandas as pd

    picked: list[str] = []
    for column in frame.columns:
        if len(picked) >= 5:
            break
        series = frame[column]
        if not (pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series)):
            continue
        unique = int(series.nunique(dropna=False))
        if 0 < unique <= 30:
            picked.append(str(column))
    return picked
