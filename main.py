# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Smart Voice Recorder — точка входа.

Запуск:
    poetry run python main.py

Приложение пишет голосовые заметки и расшифровывает их локально,
моделью Whisper. Интернет для расшифровки не нужен: модель
скачивается один раз и дальше берётся из кэша на диске.
"""

import sys


def main() -> int:
    from src.main_window import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
