from pathlib import Path

import pytest

from coding_mcp.errors import DocumentError
from coding_mcp.images import read_image, write_image


def test_png_roundtrip_keeps_pixel(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    spec = {
        "width": 10,
        "height": 10,
        "background": "#000000",
        "items": [{"kind": "rectangle", "x": 0, "y": 0, "width": 4, "height": 4, "fill": "#FF0000"}],
    }

    assert write_image(root, "out/pic.png", spec) == "Записан файл out/pic.png, 10×10 PNG"
    payload = read_image(root, "out/pic.png")

    assert "Формат: PNG" in payload.text
    assert "Размер: 10×10" in payload.text
    assert payload.image_format == "png"
    assert payload.data is not None
    assert payload.data.startswith(b"\x89PNG")

    from PIL import Image

    with Image.open(root / "out" / "pic.png") as image:
        assert image.getpixel((1, 1))[:3] == (255, 0, 0)
        assert image.getpixel((9, 8))[:3] == (0, 0, 0)


def test_shapes_and_text_are_written(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    spec = {
        "width": 40,
        "height": 30,
        "background": "#FFFFFF",
        "items": [
            {"kind": "ellipse", "x": 5, "y": 5, "width": 10, "height": 8, "fill": "#00AA00"},
            {"kind": "line", "x1": 0, "y1": 0, "x2": 20, "y2": 20, "fill": "#0000FF"},
            {"kind": "text", "x": 2, "y": 2, "text": "A", "size": 12, "fill": "#111111"},
        ],
    }

    write_image(root, "shapes.png", spec)

    from PIL import Image

    with Image.open(root / "shapes.png") as image:
        assert image.size == (40, 30)


def test_common_formats_open(tmp_path: Path) -> None:
    from PIL import Image

    root = tmp_path / "work"
    root.mkdir()
    spec = {"width": 6, "height": 4, "background": "#336699"}
    expected = {
        "photo.jpg": "JPEG",
        "photo.jpeg": "JPEG",
        "anim.gif": "GIF",
        "mark.webp": "WEBP",
        "scan.bmp": "BMP",
    }
    for name, fmt in expected.items():
        write_image(root, name, spec)
        with Image.open(root / name) as image:
            assert image.format == fmt
            assert image.size == (6, 4)


def test_source_is_resized_into_new_format(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    write_image(root, "a.png", {"width": 8, "height": 8, "background": "#FF0000"})

    written = write_image(root, "b.jpg", {"source": "a.png", "width": 4, "height": 4})

    assert written == "Записан файл b.jpg, 4×4 JPEG"
    from PIL import Image

    with Image.open(root / "b.jpg") as image:
        red, green, blue = image.getpixel((0, 0))[:3]
        assert red > 200
        assert green < 40
        assert blue < 40


def test_bars_are_written(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    spec = {
        "width": 80,
        "height": 40,
        "background": "#FFFFFF",
        "items": [{"kind": "bars", "x": 0, "y": 0, "width": 80, "height": 40, "values": [1, 3], "labels": ["A", "B"], "fill": "#000000"}],
    }

    write_image(root, "chart.png", spec)

    from PIL import Image

    with Image.open(root / "chart.png") as image:
        assert image.getpixel((50, 10))[:3] == (0, 0, 0)


def test_image_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()

    with pytest.raises(DocumentError, match="вне рабочей директории"):
        write_image(root, str(tmp_path / "x.png"), {"width": 2, "height": 2})


def test_huge_canvas_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()

    with pytest.raises(DocumentError, match="диапазона"):
        write_image(root, "big.png", {"width": 9000, "height": 10})
    assert not (root / "big.png").exists()


def test_too_many_pixels_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "work"
    root.mkdir()
    write_image(root, "small.png", {"width": 20, "height": 20, "background": "#FFFFFF"})
    monkeypatch.setattr("coding_mcp.images.MAX_IMAGE_PIXELS", 10)

    with pytest.raises(DocumentError, match="лимит размера"):
        read_image(root, "small.png")


def test_wrong_image_extension_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    (root / "note.txt").write_text("no", encoding="utf-8")

    with pytest.raises(DocumentError, match="Ожидается файл"):
        read_image(root, "note.txt")
