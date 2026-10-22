# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты значков: цвет остаётся и на отключённой кнопке.

Кнопки «стоп» и «отменить» в покое отключены, но значки на них цветные
и должны такими оставаться: серый disabled-вид Qt превращает их
в неразличимые пятна.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from src.core.icons import icon

_app = QApplication.instance() or QApplication([])


class DisabledIconTest(unittest.TestCase):
    """Отключённое состояние рисует тот же кадр, что и обычное."""

    def test_disabled_looks_like_normal(self) -> None:
        for name in ("play", "pause", "stop", "cancel"):
            with self.subTest(name=name):
                picture = icon(name, 28)
                normal = picture.pixmap(QSize(28, 28), QIcon.Mode.Normal)
                disabled = picture.pixmap(QSize(28, 28), QIcon.Mode.Disabled)

                self.assertFalse(normal.isNull(), f"значок {name} не нарисовался")
                self.assertEqual(
                    normal.toImage(),
                    disabled.toImage(),
                    "на отключённой кнопке значок обесцвечивается",
                )


if __name__ == "__main__":
    unittest.main()
