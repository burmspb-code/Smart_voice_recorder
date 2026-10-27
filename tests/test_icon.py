# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""Значок приложения: файл .ico и его подключение к сборке."""

import struct
import unittest
from pathlib import Path

import make_icon

ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "src" / "assets" / "icon.ico"
SPEC = ROOT / "SmartVoiceRecorder.spec"


def _entries(data: bytes) -> list[dict]:
    """Разбирает оглавление ICO: по записи на каждый размер."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        raise ValueError("это не файл значка")

    result = []
    for index in range(count):
        width, height, _, _, planes, bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index
        )
        result.append(
            {
                # 256 не влезает в байт и записывается нулём
                "width": width or 256,
                "height": height or 256,
                "planes": planes,
                "bpp": bpp,
                "size": size,
                "offset": offset,
            }
        )
    return result


def _pixel(data: bytes, entry: dict, x: int, y: int) -> tuple[int, int, int, int]:
    """Цвет пикселя из BMP-записи значка: BGRA, строки снизу вверх."""
    start = entry["offset"] + 40  # пропускаем BITMAPINFOHEADER
    row = entry["height"] - 1 - y  # в DIB первая строка — нижняя
    offset = start + (row * entry["width"] + x) * 4
    blue, green, red, alpha = data[offset : offset + 4]
    return red, green, blue, alpha


class IconFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = ICO.read_bytes()
        cls.entries = _entries(cls.data)

    def test_file_exists(self) -> None:
        self.assertTrue(ICO.is_file(), "нет src/assets/icon.ico — сборка без значка")

    def test_covers_taskbar_sizes(self) -> None:
        sizes = {entry["width"] for entry in self.entries}
        # 16 — списки файлов, 32 — панель задач, 48 — крупные значки,
        # 256 — плитки и свойства файла
        self.assertLessEqual({16, 32, 48, 256}, sizes)

    def test_entries_are_32bit(self) -> None:
        for entry in self.entries:
            with self.subTest(size=entry["width"]):
                self.assertEqual(entry["bpp"], 32)
                self.assertEqual(entry["planes"], 1)

    def test_entries_fit_inside_file(self) -> None:
        for entry in self.entries:
            with self.subTest(size=entry["width"]):
                end = entry["offset"] + entry["size"]
                self.assertLessEqual(end, len(self.data), "картинка обрезана")

    def test_alpha_survives_encoding(self) -> None:
        # У исходного рисунка углы прозрачные, середина белая. Если
        # перепутать порядок каналов или забыть маску, на панели задач
        # будет чёрный квадрат вместо значка
        entry = next(item for item in self.entries if item["width"] == 32)

        red, green, blue, alpha = _pixel(self.data, entry, 0, 0)
        self.assertEqual(alpha, 0, "угол не прозрачный")
        self.assertEqual((red, green, blue), (0, 0, 0))

        red, green, blue, alpha = _pixel(self.data, entry, 16, 16)
        self.assertEqual(alpha, 255, "середина прозрачная")
        self.assertEqual((red, green, blue), (255, 255, 255), "каналы перепутаны")


class BuildIconTest(unittest.TestCase):
    def test_spec_passes_icon_to_exe(self) -> None:
        # Ровно эта строка когда-то отсутствовала, и в собранном .exe
        # оставался значок PyInstaller
        spec = SPEC.read_text(encoding="utf-8")
        self.assertIn("icon='src/assets/icon.ico'", spec)

    def test_icon_path_in_spec_exists(self) -> None:
        self.assertTrue(ICO.is_file())

    def test_generator_sizes_match_file(self) -> None:
        sizes = {entry["width"] for entry in _entries(ICO.read_bytes())}
        self.assertEqual(sizes, set(make_icon.SIZES))


if __name__ == "__main__":
    unittest.main()
