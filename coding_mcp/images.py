"""Чтение и создание PNG, JPEG, GIF, WEBP и BMP. Pillow импортируется внутри функций."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coding_mcp.documents import _atomic_save
from coding_mcp.errors import DocumentError
from coding_mcp.jail import open_for_read_types, prepare_write_types, relative_name
from coding_mcp.limits import (
    MAX_COLS,
    MAX_IMAGE_EDGE,
    MAX_IMAGE_INLINE_BYTES,
    MAX_IMAGE_ITEMS,
    MAX_IMAGE_PIXELS,
)
from coding_mcp.runtime import clip, load_object

IMAGE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".bmp": "BMP",
}
IMAGE_SUFFIXES = frozenset(IMAGE_FORMATS)
_INLINE_FORMAT = {
    "PNG": "png",
    "JPEG": "jpeg",
    "GIF": "gif",
    "WEBP": "webp",
    "BMP": "bmp",
}
_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
)
_NAMED_COLORS = {
    "white": (255, 255, 255, 255),
    "black": (0, 0, 0, 255),
    "red": (220, 40, 40, 255),
    "green": (40, 150, 70, 255),
    "blue": (40, 90, 200, 255),
    "gray": (128, 128, 128, 255),
    "transparent": (0, 0, 0, 0),
}


@dataclass(frozen=True)
class ImagePayload:
    text: str
    data: bytes | None
    image_format: str | None


def read_image(root: Path, raw: str) -> ImagePayload:
    from PIL.Image import DecompressionBombError

    path = open_for_read_types(root, raw, IMAGE_SUFFIXES)
    image = _open_image(path)
    try:
        frames = int(getattr(image, "n_frames", 1) or 1)
        _reject_large(image.width, image.height, frames)
        pil_format = (image.format or "").upper()
        expected = IMAGE_FORMATS[path.suffix.lower()]
        if pil_format != expected:
            raise DocumentError("Содержимое не соответствует расширению.")
        mode = image.mode
        width, height = image.size
        try:
            image.load()
        except DecompressionBombError:
            raise DocumentError("Картинка превышает лимит размера.") from None
        except OSError:
            raise DocumentError("Не удалось прочитать картинку.") from None
    finally:
        image.close()

    lines = [
        f"Формат: {pil_format}",
        f"Режим: {mode}",
        f"Размер: {width}×{height}",
    ]
    if frames > 1:
        lines.append(f"Кадры: {frames}")
    size = path.stat().st_size
    lines.append(f"Байт: {size}")
    data: bytes | None = None
    inline = _INLINE_FORMAT[pil_format]
    if size <= MAX_IMAGE_INLINE_BYTES:
        data = path.read_bytes()
    else:
        lines.append("[файл слишком большой, чтобы вложить картинку в ответ]")
    return ImagePayload(text=clip("\n".join(lines)), data=data, image_format=inline if data else None)


def write_image(root: Path, raw: str, spec: dict | str) -> str:
    payload = load_object(spec)
    image = _build_image(root, payload)
    path = prepare_write_types(root, raw, IMAGE_SUFFIXES)
    pil_format = IMAGE_FORMATS[path.suffix.lower()]
    width, height = image.size

    def save(target: Path) -> None:
        prepared = _flatten(image, pil_format)
        options: dict[str, object] = {}
        if pil_format in {"JPEG", "WEBP"}:
            options["quality"] = _quality(payload.get("quality"))
        prepared.save(target, format=pil_format, **options)

    try:
        _atomic_save(path, save)
    finally:
        image.close()
    return f"Записан файл {relative_name(root, path)}, {width}×{height} {pil_format}"


def _build_image(root: Path, spec: dict):
    from PIL import Image as PILImage
    from PIL import ImageDraw

    source = spec.get("source")
    if source is not None:
        if not isinstance(source, str):
            raise DocumentError("source должен быть путём к картинке.")
        image = _open_image(open_for_read_types(root, source, IMAGE_SUFFIXES)).convert("RGBA")
        image = _resize(image, spec.get("width"), spec.get("height"))
    else:
        width = _int(spec.get("width"), "width", 1, MAX_IMAGE_EDGE)
        height = _int(spec.get("height"), "height", 1, MAX_IMAGE_EDGE)
        _reject_large(width, height, 1)
        background = _color(spec.get("background") or "#FFFFFF")
        image = PILImage.new("RGBA", (width, height), background)

    items = spec.get("items") or []
    if not isinstance(items, list):
        raise DocumentError("items должен быть списком.")
    if len(items) > MAX_IMAGE_ITEMS:
        raise DocumentError(f"Слишком много фигур, максимум {MAX_IMAGE_ITEMS}.")
    if items:
        draw = ImageDraw.Draw(image)
        font_cache: dict[int, object] = {}
        for item in items:
            _draw_item(draw, item, font_cache)
    return image


def _resize(image, width: object, height: object):
    from PIL import Image as PILImage

    if width is None and height is None:
        return image
    if width is not None and height is not None:
        target = (
            _int(width, "width", 1, MAX_IMAGE_EDGE),
            _int(height, "height", 1, MAX_IMAGE_EDGE),
        )
        _reject_large(target[0], target[1], 1)
        return image.resize(target, PILImage.Resampling.LANCZOS)
    if width is not None:
        target_width = _int(width, "width", 1, MAX_IMAGE_EDGE)
        target_height = max(1, round(image.height * target_width / image.width))
    else:
        target_height = _int(height, "height", 1, MAX_IMAGE_EDGE)
        target_width = max(1, round(image.width * target_height / image.height))
    if target_width > MAX_IMAGE_EDGE or target_height > MAX_IMAGE_EDGE:
        raise DocumentError("Картинка превышает лимит размера.")
    _reject_large(target_width, target_height, 1)
    return image.resize((target_width, target_height), PILImage.Resampling.LANCZOS)


def _draw_item(draw, item: object, font_cache: dict[int, object]) -> None:
    if not isinstance(item, dict):
        raise DocumentError("Фигура должна быть объектом.")
    kind = item.get("kind")
    if kind == "rectangle":
        box = _box(item)
        draw.rectangle(box, fill=_color(item.get("fill"), allow_none=True), outline=_color(item.get("outline"), allow_none=True), width=_stroke(item))
        return
    if kind == "ellipse":
        box = _box(item)
        draw.ellipse(box, fill=_color(item.get("fill"), allow_none=True), outline=_color(item.get("outline"), allow_none=True), width=_stroke(item))
        return
    if kind == "line":
        x1 = _int(item.get("x1"), "x1", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
        y1 = _int(item.get("y1"), "y1", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
        x2 = _int(item.get("x2"), "x2", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
        y2 = _int(item.get("y2"), "y2", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
        draw.line((x1, y1, x2, y2), fill=_color(item.get("fill") or "#000000"), width=_stroke(item))
        return
    if kind == "text":
        _draw_text(draw, item, font_cache)
        return
    if kind == "bars":
        _draw_bars(draw, item, font_cache)
        return
    raise DocumentError("Неизвестный вид фигуры.")


def _draw_text(draw, item: dict, font_cache: dict[int, object]) -> None:
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DocumentError("Текст картинки пустой.")
    if len(text) > 400:
        raise DocumentError("Слишком длинный текст картинки.")
    size = _int(item.get("size") or 24, "size", 8, 200)
    font = _font(size, text, font_cache)
    x = _int(item.get("x"), "x", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
    y = _int(item.get("y"), "y", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
    draw.text((x, y), text, fill=_color(item.get("fill") or "#000000"), font=font)


def _draw_bars(draw, item: dict, font_cache: dict[int, object]) -> None:
    x = _int(item.get("x"), "x", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
    y = _int(item.get("y"), "y", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
    width = _int(item.get("width"), "width", 1, MAX_IMAGE_EDGE)
    height = _int(item.get("height"), "height", 1, MAX_IMAGE_EDGE)
    raw_values = item.get("values")
    if not isinstance(raw_values, list) or not raw_values:
        raise DocumentError("У столбчатой диаграммы нет values.")
    if len(raw_values) > MAX_COLS:
        raise DocumentError("Слишком много столбцов диаграммы.")
    numbers: list[float] = []
    for value in raw_values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DocumentError("Значение диаграммы должно быть числом.")
        if value != value or value in {float("inf"), float("-inf")}:
            raise DocumentError("Недопустимое число в диаграмме.")
        numbers.append(float(value))
    labels = item.get("labels") or []
    if labels and (not isinstance(labels, list) or len(labels) != len(numbers)):
        raise DocumentError("Число подписей диаграммы не совпадает с values.")
    peak = max(abs(number) for number in numbers) or 1.0
    label_band = 18 if labels else 0
    plot_height = max(1, height - label_band)
    gap = 4
    bar_width = max(1, (width - gap * (len(numbers) + 1)) // len(numbers))
    fill = _color(item.get("fill") or "#336699")
    sample = " ".join(_short_label(label) for label in labels)
    font = _font(12, sample, font_cache)
    for index, number in enumerate(numbers):
        bar_height = max(1, int(abs(number) / peak * plot_height))
        left = x + gap + index * (bar_width + gap)
        top = y + plot_height - bar_height
        draw.rectangle((left, top, left + bar_width, y + plot_height), fill=fill)
        if labels:
            label = _short_label(labels[index])
            draw.text((left, y + plot_height + 2), label, fill=fill, font=font)


def _open_image(path: Path):
    from PIL import Image as PILImage
    from PIL import UnidentifiedImageError
    from PIL.Image import DecompressionBombError

    PILImage.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        image = PILImage.open(path)
    except DecompressionBombError:
        raise DocumentError("Картинка превышает лимит размера.") from None
    except UnidentifiedImageError:
        raise DocumentError("Не удалось прочитать картинку.") from None
    except OSError:
        raise DocumentError("Не удалось прочитать картинку.") from None
    return image


def _flatten(image, pil_format: str):
    from PIL import Image as PILImage

    if pil_format in {"JPEG", "BMP"}:
        if image.mode == "RGBA":
            background = PILImage.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            return background
        return image.convert("RGB")
    if pil_format == "GIF":
        return image.convert("P", palette=PILImage.Palette.ADAPTIVE)
    return image


def _font(size: int, text: str, cache: dict[int, object]):
    from PIL import ImageFont

    if size in cache:
        font = cache[size]
    else:
        font = None
        for candidate in _FONT_CANDIDATES:
            if candidate.is_file():
                font = ImageFont.truetype(str(candidate), size=size)
                break
        if font is None:
            font = ImageFont.load_default(size=size)
        cache[size] = font
    if any(ord(char) > 255 for char in text) and not any(path.is_file() for path in _FONT_CANDIDATES):
        raise DocumentError("Нет шрифта для этого текста.")
    return font


def _box(item: dict) -> tuple[int, int, int, int]:
    x = _int(item.get("x"), "x", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
    y = _int(item.get("y"), "y", -MAX_IMAGE_EDGE, MAX_IMAGE_EDGE)
    width = _int(item.get("width"), "width", 1, MAX_IMAGE_EDGE)
    height = _int(item.get("height"), "height", 1, MAX_IMAGE_EDGE)
    return (x, y, x + width, y + height)


def _stroke(item: dict) -> int:
    if "stroke" not in item or item.get("stroke") is None:
        return 1
    return _int(item.get("stroke"), "stroke", 1, 40)


def _quality(value: object) -> int:
    if value is None:
        return 90
    return _int(value, "quality", 30, 95)


def _color(value: object, allow_none: bool = False) -> tuple[int, int, int, int] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DocumentError("Цвет должен быть строкой #RRGGBB.")
    text = value.strip().lower()
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    if text.startswith("#"):
        hexpart = text[1:]
        if len(hexpart) == 3:
            hexpart = "".join(char * 2 for char in hexpart)
        if len(hexpart) == 6:
            hexpart += "ff"
        if len(hexpart) == 8 and all(char in "0123456789abcdef" for char in hexpart):
            return tuple(int(hexpart[index : index + 2], 16) for index in range(0, 8, 2))
    raise DocumentError("Цвет должен быть #RGB, #RRGGBB или #RRGGBBAA.")


def _int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentError(f"Поле {name} должно быть целым числом.")
    if isinstance(value, float) and not value.is_integer():
        raise DocumentError(f"Поле {name} должно быть целым числом.")
    number = int(value)
    if number < low or number > high:
        raise DocumentError(f"Поле {name} вне допустимого диапазона.")
    return number


def _reject_large(width: int, height: int, frames: int) -> None:
    if width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE or frames < 1:
        raise DocumentError("Картинка превышает лимит размера.")
    if width * height * frames > MAX_IMAGE_PIXELS:
        raise DocumentError("Картинка превышает лимит размера.")


def _short_label(value: object) -> str:
    text = "" if value is None else str(value)
    text = "".join(char for char in text if ord(char) >= 32)
    text = text.replace("\t", " ").strip()
    return text[:20]
