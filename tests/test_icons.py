# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты значков: цвет на отключённой кнопке и размер рисунка.

Кнопки «стоп» и «отменить» в покое отключены, но значки на них цветные
и должны такими оставаться: серый disabled-вид Qt превращает их
в неразличимые пятна.

Файлы значков приходят с разным количеством прозрачных полей, и без
обрезки рисунок занимал бы лишь часть отведённого ему места — именно
поэтому значки однажды «уменьшились», хотя размер на кнопке не менялся.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication

from src.core.icons import icon

_app = QApplication.instance() or QApplication([])

# Размер для проверки: любой, лишь бы один и тот же у всех значков
BOX = 28

ICONS = ("play", "pause", "stop", "cancel", "open", "block", "microphone", "models")


def _ink(image: QImage) -> tuple[int, int]:
    """Габариты непрозрачного рисунка в логических точках."""
    left = top = 10**6
    right = bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 8:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)

    if right < 0:
        return 0, 0

    ratio = image.devicePixelRatio()
    return (
        round((right - left + 1) / ratio),
        round((bottom - top + 1) / ratio),
    )


class DisabledIconTest(unittest.TestCase):
    """Отключённое состояние рисует тот же кадр, что и обычное."""

    def test_disabled_looks_like_normal(self) -> None:
        for name in ICONS:
            with self.subTest(name=name):
                picture = icon(name, BOX)
                normal = picture.pixmap(QSize(BOX, BOX), QIcon.Mode.Normal)
                disabled = picture.pixmap(QSize(BOX, BOX), QIcon.Mode.Disabled)

                self.assertFalse(normal.isNull(), f"значок {name} не нарисовался")
                self.assertEqual(
                    normal.toImage(),
                    disabled.toImage(),
                    "на отключённой кнопке значок обесцвечивается",
                )


class IconSizeTest(unittest.TestCase):
    """Прозрачные поля файла не должны уменьшать значок."""

    def test_picture_fills_the_box(self) -> None:
        for name in ICONS:
            with self.subTest(name=name):
                picture = icon(name, BOX)
                image = picture.pixmap(QSize(BOX, BOX), QIcon.Mode.Normal).toImage()
                biggest = max(_ink(image))

                self.assertGreaterEqual(
                    biggest,
                    BOX - 2,
                    f"значок {name} занимает {biggest} из {BOX} пикселей",
                )


if __name__ == "__main__":
    unittest.main()
