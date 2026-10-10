# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""Снимок окна для README. Запуск: poetry run make_screenshot.py"""

import os

os.environ.pop("QT_QPA_PLATFORM", None)

from pathlib import Path

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from src.core.styles import apply_locale, apply_theme
from src.main_window import MainWindow

app = QApplication([])
apply_locale(app)
apply_theme(app)
window = MainWindow()
window.show()


def pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def run():
    pump(400)

    block = next(
        (d.name for d in sorted(window.recordings.iterdir()) if d.is_dir()),
        "",
    )
    window.block_input.setText(block)
    window._on_block_entered()
    pump(200)

    if not window.zone_button.isChecked():
        window.zone_button.click()
    pump(700)

    # Распознанный текст в окне — чтобы снимок был живым
    target = Path("docs")
    target.mkdir(exist_ok=True)
    window.grab().save(str(target / "screenshot.png"))

    print("saved docs/screenshot.png")
    app.exit(0)


QTimer.singleShot(3000, run)
app.exec()
