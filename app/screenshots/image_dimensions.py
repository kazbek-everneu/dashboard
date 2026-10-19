from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOI = b"\xff\xd8"
JPEG_SOS = 0xDA
JPEG_EOI = 0xD9
JPEG_STANDALONE_MARKERS = {
    0x01,
    *range(0xD0, 0xD8),
}
JPEG_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3,
    0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB,
    0xCD, 0xCE, 0xCF,
}


def read_image_dimensions_from_file(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as stream:
            return read_image_dimensions_from_stream(stream)
    except OSError:
        return None


def read_image_dimensions_from_bytes(data: bytes) -> tuple[int, int] | None:
    return read_image_dimensions_from_stream(_BytesReader(data))


def read_image_dimensions_from_stream(stream: BinaryIO) -> tuple[int, int] | None:
    signature = stream.read(8)
    if signature == PNG_SIGNATURE:
        return _read_png_dimensions(stream)

    if signature[:2] == JPEG_SOI:
        return _read_jpeg_dimensions(stream)

    return None


def _read_png_dimensions(stream: BinaryIO) -> tuple[int, int] | None:
    chunk = stream.read(16)
    if len(chunk) < 16:
        return None
    if chunk[4:8] != b"IHDR":
        return None
    width = int.from_bytes(chunk[8:12], "big")
    height = int.from_bytes(chunk[12:16], "big")
    return (width, height) if width > 0 and height > 0 else None


def _read_jpeg_dimensions(stream: BinaryIO) -> tuple[int, int] | None:
    while True:
        marker_prefix = _read_until_marker(stream)
        if marker_prefix is None:
            return None

        marker = stream.read(1)
        if len(marker) != 1:
            return None

        marker_code = marker[0]
        if marker_code in JPEG_STANDALONE_MARKERS:
            continue
        if marker_code in {JPEG_SOS, JPEG_EOI}:
            return None

        segment_length_bytes = stream.read(2)
        if len(segment_length_bytes) != 2:
            return None
        segment_length = int.from_bytes(segment_length_bytes, "big")
        if segment_length < 2:
            return None

        payload_length = segment_length - 2
        if marker_code in JPEG_SOF_MARKERS:
            payload = stream.read(payload_length)
            if len(payload) != payload_length or payload_length < 5:
                return None
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            return (width, height) if width > 0 and height > 0 else None

        if stream.seekable():
            stream.seek(payload_length, 1)
        else:
            skipped = stream.read(payload_length)
            if len(skipped) != payload_length:
                return None


def _read_until_marker(stream: BinaryIO) -> bytes | None:
    while True:
        byte = stream.read(1)
        if len(byte) != 1:
            return None
        if byte == b"\xff":
            return byte


class _BytesReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        start = self._offset
        end = min(len(self._data), self._offset + size)
        self._offset = end
        return self._data[start:end]

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._offset = max(0, offset)
        elif whence == 1:
            self._offset = max(0, self._offset + offset)
        elif whence == 2:
            self._offset = max(0, len(self._data) + offset)
        else:
            raise ValueError("invalid whence")
        return self._offset
