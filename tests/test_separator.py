# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты разделителей в окне: метка в оформлении, а не текст.

Разделитель обязан занимать в тексте ноль символов: он попадает
в буфер обмена и в файл, а там нужна только речь.
"""

import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src import main_window as mw
from src import model_store

_app = QApplication.instance() or QApplication([])


def _silence(path: Path, seconds: float = 0.1) -> Path:
    """Настоящий пустой wav: таблица записей открывает файлы волной."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


class _WindowCase(unittest.TestCase):
    """Окно без проверки микрофонов и без загрузки модели."""

    @classmethod
    def setUpClass(cls) -> None:
        mw.candidate_devices = lambda: []
        mw.MainWindow._start_device_probe = lambda self: None
        mw.ModelStore.load_async = lambda self, *a, **k: None
        model_store.is_cached = lambda name: True

    def setUp(self) -> None:
        # Настройки уводим в отдельную папку: переключатели пишут
        # в файл при смене состояния, и тест не должен трогать
        # настоящие настройки пользователя
        self._appdata = tempfile.TemporaryDirectory()
        self._old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._appdata.name

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.window = mw.MainWindow()
        self.window.recordings = self.root
        self.window.auto_copy_box.setChecked(False)

    def tearDown(self) -> None:
        # Закрытие несохранённого окна открывает модальный диалог,
        # а в offscreen-режиме на нём процесс встаёт насмерть
        self.window.dirty = False
        self.window.close()
        self._tmp.cleanup()
        self._appdata.cleanup()
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata

    def append(self, name: str, text: str) -> Path:
        wav = _silence(self.root / f"{name}.wav")
        self.window._append_text(wav, text)
        return wav


class SeparatorTextTest(_WindowCase):
    """Разделитель не оставляет в тексте ни одного символа."""

    def test_marker_found_for_every_block(self) -> None:
        self.append("первая", "речь один")
        self.append("вторая", "речь два")

        self.assertEqual(len(self.window._separator_blocks()), 2)

    def test_no_characters_without_timecodes(self) -> None:
        self.window.timecodes_box.setChecked(False)

        self.append("первая", "речь один")
        self.append("вторая", "речь два")

        text = self.window.text_edit.toPlainText()

        self.assertNotIn("─", text)
        self.assertNotIn(mw.SEPARATOR_MARK, text)
        # Между кусками — пустая строка и полоса под ней: полоса
        # занимает ещё одну строку, но ни одного своего символа
        self.assertEqual(text.strip(), "речь один\n\n\nречь два")

    def test_title_visible_with_timecodes(self) -> None:
        self.window.timecodes_box.setChecked(True)

        self.append("первая", "речь один")

        self.assertIn("─", self.window.text_edit.toPlainText())

    def test_grey_bar_visible_in_both_modes(self) -> None:
        for timecodes in (True, False):
            with self.subTest(timecodes=timecodes):
                self.window.text_edit.clear()
                self.window.timecodes_box.setChecked(timecodes)

                self.append("запись", "речь")

                marks = self.window._separator_blocks()
                self.assertEqual(len(marks), 1)
                background = marks[0].blockFormat().background()
                self.assertNotEqual(
                    background.style(),
                    Qt.BrushStyle.NoBrush,
                    f"разделитель без заливки при timecodes={timecodes}",
                )

    def test_marker_never_leaks_into_text(self) -> None:
        self.window.timecodes_box.setChecked(False)

        self.append("первая", "речь один")

        copied = self.window.text_edit.toPlainText()
        self.assertNotIn(mw.SEPARATOR_MARK, copied)


class SeparatorSaveTest(_WindowCase):
    """Правки из окна раскладываются по файлам по меткам разделителей."""

    def test_saves_each_block_to_its_own_file(self) -> None:
        self.window.timecodes_box.setChecked(False)

        first = self.append("первая", "речь один")
        second = self.append("вторая", "речь два")

        # Правим последний кусок прямо в окне
        cursor = self.window.text_edit.textCursor()
        cursor.movePosition(mw.QTextCursor.MoveOperation.End)
        cursor.insertText(" дополнено")

        self.window._save_blocks()

        # Правка попала в свой файл, а не в чужой
        self.assertEqual(
            first.with_suffix(".txt").read_text(encoding="utf-8"),
            "речь один",
        )
        self.assertEqual(
            second.with_suffix(".txt").read_text(encoding="utf-8"),
            "речь два дополнено",
        )

    def test_lost_markers_warn_instead_of_glueing(self) -> None:
        self.window.timecodes_box.setChecked(False)

        first = self.append("первая", "речь один")
        second = self.append("вторая", "речь два")
        # Файлы создаёт распознавание, в тесте делаем это вручную
        first.with_suffix(".txt").write_text("речь один", encoding="utf-8")
        second.with_suffix(".txt").write_text("речь два", encoding="utf-8")

        # Пользователь переписал окно целиком: меток больше нет
        self.window.text_edit.setPlainText("всё смешалось")
        self.window.dirty = True

        self.window._save_blocks()

        self.assertIn("Разделители", self.window.status.text())
        # Файлы не тронуты: склеивать разные записи в одну нельзя
        self.assertEqual(
            first.with_suffix(".txt").read_text(encoding="utf-8"), "речь один"
        )
        self.assertEqual(
            second.with_suffix(".txt").read_text(encoding="utf-8"), "речь два"
        )


if __name__ == "__main__":
    unittest.main()
