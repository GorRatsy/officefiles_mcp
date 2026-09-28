"""Ограничение времени вызова. Зависший разбор не блокирует ответ tool."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from coding_mcp.errors import DocumentError
from coding_mcp.limits import CALL_TIMEOUT_SECONDS, MAX_OUTPUT_CHARS

T = TypeVar("T")


def clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n\n[вывод обрезан по лимиту]"


def run_bounded(func: Callable[[], T], timeout: float = CALL_TIMEOUT_SECONDS) -> T:
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coding-mcp")
    future = pool.submit(func)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        raise DocumentError("Превышено время обработки файла.") from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
