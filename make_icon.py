# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Сборка значка приложения: src/assets/icon.png → src/assets/icon.ico.

Зачем нужен отдельный файл .ico, если есть .png: PyInstaller вшивает
значок в сам .exe из формата ICO, и именно этот значок видит Проводник.
Один PNG там не годится — Windows ждёт набор размеров, иначе мелкие
значки (16 и 32 пикселя) получаются мыльными.

Запуск (после правки icon.png):

    poetry run python make_icon.py

Размеры выбраны под то, где Windows показывает значок: 16 — панель
задач и списки файлов, 24/32 — панель задач при масштабе 125–150 %,
48 — крупные значки Проводника, 256 — плитки и свойства файла.

Каждый размер лежит в .ico несжатым BMP: так его читают все версии
Windows и PyInstaller. PNG внутри ICO поддерживается только начиная
с Vista и не для всех размеров.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "src" / "assets"
SOURCE = ASSETS / "icon.png"
TARGET = ASSETS / "icon.ico"

# Размеры значка в пикселях. Порядок по возрастанию: так удобнее
# читать готовый файл, хотя Windows порядок не требует.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _bmp_entry(image: QImage) -> bytes:
    """Один значок в виде BMP: заголовок, пиксели, маска прозрачности.

    Формат ICO хранит картинку как DIB: строки идут снизу вверх,
    порядок каналов BGRA, а после пикселей лежит однобитная маска.
    В 32-битных значках прозрачность берётся из альфы, но маску
    всё равно пишут — без неё часть программ рисует чёрный фон.
    """
    width = image.width()
    height = image.height()
    pixels = image.convertToFormat(QImage.Format.Format_ARGB32)

    header = bytearray()
    header += (40).to_bytes(4, "little")  # размер заголовка BITMAPINFOHEADER
    header += width.to_bytes(4, "little", signed=True)
    header += (height * 2).to_bytes(4, "little", signed=True)  # пиксели + маска
    header += (1).to_bytes(2, "little")  # число плоскостей
    header += (32).to_bytes(2, "little")  # бит на пиксель
    header += (0).to_bytes(4, "little")  # сжатие: без него
    header += (width * height * 4).to_bytes(4, "little")
    header += (0).to_bytes(4, "little") * 4  # разрешение и палитра не нужны

    rows = bytearray()
    raw = pixels.constBits()
    raw.setsize(pixels.sizeInBytes())
    data = bytes(raw)
    stride = pixels.bytesPerLine()
    for y in range(height - 1, -1, -1):
        start = y * stride
        rows += data[start : start + width * 4]

    # Маска: 1 бит на пиксель, строка добивается до кратности четырём
    mask_row = ((width + 31) // 32) * 4
    mask = bytes(mask_row * height)

    return bytes(header) + bytes(rows) + mask


def _frames(source: QImage) -> list[tuple[int, bytes]]:
    """Нарезает исходный PNG на все нужные размеры."""
    frames = []
    for size in SIZES:
        scaled = source.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        frames.append((size, _bmp_entry(scaled)))
    return frames


def _container(frames: list[tuple[int, bytes]]) -> bytes:
    """Собирает ICO: заголовок, оглавление, затем сами картинки."""
    head = bytearray()
    head += (0).to_bytes(2, "little")  # зарезервировано
    head += (1).to_bytes(2, "little")  # тип: 1 — значок
    head += len(frames).to_bytes(2, "little")

    offset = len(head) + 16 * len(frames)
    body = bytearray()
    for size, data in frames:
        # 256 в этом поле записывается нулём: в байт не влезает
        edge = 0 if size >= 256 else size
        head += bytes((edge, edge, 0, 0))  # ширина, высота, палитра, резерв
        head += (1).to_bytes(2, "little")  # плоскости
        head += (32).to_bytes(2, "little")  # бит на пиксель
        head += len(data).to_bytes(4, "little")
        head += offset.to_bytes(4, "little")
        body += data
        offset += len(data)

    return bytes(head) + bytes(body)


def build() -> Path:
    """Пересобирает .ico из .png и возвращает путь к результату."""
    # QImage читает PNG и без окна, но плагины изображений надёжнее
    # поднимаются при существующем приложении. Ссылку держим, иначе
    # Qt может уничтожить объект до конца работы.
    _app = QGuiApplication.instance() or QGuiApplication([])

    source = QImage(str(SOURCE))
    if source.isNull():
        raise SystemExit(f"не удалось прочитать {SOURCE}")

    frames = _frames(source)
    TARGET.write_bytes(_container(frames))

    total = sum(len(data) for _, data in frames)
    print(f"{TARGET} — {len(SIZES)} размеров, {total / 1024:.0f} КБ картинок")
    return TARGET


if __name__ == "__main__":
    build()
