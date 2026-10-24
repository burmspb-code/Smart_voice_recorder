# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Значки приложения.

Иконки лежат готовыми файлами в src/assets и берутся по имени.
В системе их нет: тема иконок есть только у Linux, а Windows вернёт
пустую картинку.

Файлы большие (512–1024 пикселя), а на экране нужны 20–34. Уменьшаем
сами, предварительно срезав прозрачные поля, и помечаем тройной
плотностью пикселей — на мониторе с масштабированием 125-150% иначе
значок превращается в кашу.
"""

from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap

ASSETS = Path(__file__).resolve().parent.parent / "assets"

ICON_PATH = ASSETS / "icon.png"

# Во сколько раз рисуем больше нужного
DENSITY = 3


def _trim(image: QImage) -> QImage:
    """Обрезает прозрачные поля вокруг рисунка.

    Файлы приходят с разным количеством воздуха: в одном рисунок занимает
    95% кадра, в другом 75%. При уменьшении именно поля решают, каким
    значок станет на кнопке, поэтому они срезаются — рисунок должен
    занимать весь отведённый ему размер.
    """
    width, height = image.width(), image.height()
    if not width or not height:
        return image

    # Поля ищем по уменьшенной копии: перебирать миллион точек исходника
    # на каждый значок слишком долго, а по копии в 96 точек промах
    # около процента кадра
    probe = image.scaled(
        96,
        96,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )

    left = top = 10**6
    right = bottom = -1
    for y in range(probe.height()):
        for x in range(probe.width()):
            if probe.pixel(x, y) >> 24 > 8:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)

    if right < 0:
        return image

    # Обратно в точки исходника, с запасом в один шаг: срезать сам
    # рисунок нельзя, а лишний пиксель поля не заметен
    step_x = width / probe.width()
    step_y = height / probe.height()
    left = max(0, int((left - 1) * step_x))
    top = max(0, int((top - 1) * step_y))
    right = min(width - 1, int((right + 2) * step_x))
    bottom = min(height - 1, int((bottom + 2) * step_y))

    return image.copy(QRect(left, top, right - left, bottom - top))


def asset_path(name: str) -> Path:
    """Путь к файлу в src/assets с учётом заморозки PyInstaller."""
    return ASSETS / name


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
    scaled = _trim(image).scaled(
        big,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(DENSITY)

    # QIcon берёт именно QPixmap, из QImage его напрямую не сделать
    picture = QPixmap.fromImage(scaled)

    # На отключённой кнопке Qt сама обесцвечивает значок под серый
    # disabled-вид. Значки цветные, и цвет нужен всегда: тот же кадр
    # кладём и для отключённого состояния, иначе кнопка «стоп» и
    # «отменить» в покое выглядят серыми пятнами
    result = QIcon()
    result.addPixmap(picture, QIcon.Mode.Normal)
    result.addPixmap(picture, QIcon.Mode.Disabled)
    return result
