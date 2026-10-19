from app.screenshots.image_dimensions import read_image_dimensions_from_bytes


def test_reads_png_dimensions_from_bytes() -> None:
    data = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x07\x80"
        b"\x00\x00\x14\x50"
        b"\x08\x02\x00\x00\x00"
    )

    assert read_image_dimensions_from_bytes(data) == (1920, 5200)


def test_reads_jpeg_dimensions_from_bytes() -> None:
    data = (
        b"\xff\xd8"
        b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xc0\x00\x11\x08"
        b"\x20\xf8"
        b"\x05\xa0"
        b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xd9"
    )

    assert read_image_dimensions_from_bytes(data) == (1440, 8440)


def test_returns_none_for_unsupported_bytes() -> None:
    assert read_image_dimensions_from_bytes(b"not-an-image") is None
