# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""Запись необработанных ошибок в файл рядом с настройками.

Приложение графическое. Если запускать его двойным щелчком, консоли
нет, и traceback печатать некуда: падение выглядит как «программа
просто закрылась». Файл `crash.log` сохраняет причину, чтобы её можно
было прочитать и показать.

Ловим оба случая: ошибку в главном потоке (sys.excepthook) и ошибку
в рабочем потоке (threading.excepthook). Обычные сообщения об ошибках
в строке состояния сюда не попадают — они не падения.
"""

import sys
import threading
import traceback
from pathlib import Path

from src.config import settings_dir

LOG_NAME = "crash.log"


def log_path() -> Path:
    """Файл с описанием падений — рядом с настройками."""
    return settings_dir() / LOG_NAME


def _write(text: str) -> None:
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)
    except OSError:
        # Писать некуда — молчим: падение важнее, чем его запись
        pass


def _hook(exc_type, exc, tb) -> None:
    _write("".join(traceback.format_exception(exc_type, exc, tb)) + "\n")

    # Свой обработчик нужен и дальше: в консоли (если она есть)
    # traceback должен появиться как обычно
    if sys.__excepthook__ is not None:
        sys.__excepthook__(exc_type, exc, tb)


def install() -> None:
    """Ставит обработчики ошибок главного и фоновых потоков."""
    sys.excepthook = _hook
    threading.excepthook = lambda args: _hook(
        args.exc_type, args.exc_value, args.exc_traceback
    )
