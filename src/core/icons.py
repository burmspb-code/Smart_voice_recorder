# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Значки приложения.

Иконки лежат готовыми файлами в src/assets и берутся по имени.
В системе их нет: тема иконок есть только у Linux, а Windows вернёт
пустую картинку.

Файлы по 512 пикселей, а на кнопке нужны двадцать. Уменьшаем сами
и помечаем тройной плотностью пикселей — на мониторе с масштабированием
125-150% иначе значок превращается в кашу.
"""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap

ASSETS = Path(__file__).resolve().parent.parent / "assets"

ICON_PATH = ASSETS / "icon.png"

# Во сколько раз рисуем больше нужного
DENSITY = 3


def app_icon() -> QIcon:
    """Иконка приложения из файла. Пустая, если файла нет."""
    return QIcon(str(ICON_PATH)) if ICON_PATH.is_file() else QIcon()


def icon(name: str, size: int = 20) -> QIcon:
    """Значок из src/assets по имени, без расширения.

    Пока иконки нет, возвращается пустая — кнопка просто останется
    без значка и не сломается.
    """
    path = ASSETS / f"{name}.png"
    if not path.is_file():
        return QIcon()

    image = QImage(str(path))
    if image.isNull():
        return QIcon()

    big = QSize(size * DENSITY, size * DENSITY)
    scaled = image.scaled(
        big,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(DENSITY)

    # QIcon берёт именно QPixmap, из QImage его напрямую не сделать
    return QIcon(QPixmap.fromImage(scaled))
