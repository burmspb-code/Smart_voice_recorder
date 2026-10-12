# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты настроек: чтение и запись JSON в профиле пользователя."""

import os
import tempfile
import unittest
from pathlib import Path

from src import config


class SettingsTest(unittest.TestCase):
    """Настройки лежат в %APPDATA% — на время теста подменяем её.

    Без этого тесты читали бы и правили настоящий файл настроек.
    """

    def setUp(self) -> None:
        self._old_appdata = os.environ.get("APPDATA")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata
        self._tmp.cleanup()

    def test_defaults_when_no_file(self) -> None:
        settings = config.Settings()
        self.assertIsNone(settings.microphone)
        self.assertEqual(settings.last_block, "")
        self.assertFalse(settings.auto_transcribe)
        self.assertTrue(settings.auto_copy)

    def test_round_trip(self) -> None:
        settings = config.Settings()
        settings.recordings_dir = "C:/записи"
        settings.model = "small"
        settings.microphone = 3
        settings.last_block = "идея"
        settings.auto_transcribe = True
        settings.auto_copy = False
        settings.save()

        again = config.Settings()

        self.assertEqual(again.recordings_dir, "C:/записи")
        self.assertEqual(again.model, "small")
        self.assertEqual(again.microphone, 3)
        self.assertEqual(again.last_block, "идея")
        self.assertTrue(again.auto_transcribe)
        self.assertFalse(again.auto_copy)

    def test_broken_file_keeps_defaults(self) -> None:
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("это не json", encoding="utf-8")

        settings = config.Settings()

        self.assertEqual(settings.model, config.DEFAULT_MODEL)
        self.assertIsNone(settings.microphone)


class LooksNestedTest(unittest.TestCase):
    def test_nested_under_current_root(self) -> None:
        root = Path.home() / "записи"
        self.assertTrue(config.looks_nested(root / "блок", root))
        self.assertFalse(config.looks_nested(root, root))

    def test_outside_root_is_not_nested(self) -> None:
        root = Path.home() / "записи"
        self.assertFalse(config.looks_nested(Path.home() / "другое", root))

    def test_compares_with_the_root_given_not_default(self) -> None:
        # Папка лежит внутри корня пользователя — сравнение идёт от него,
        # а не от папки записей по умолчанию у проекта
        root = Path.home() / "мои заметки"
        self.assertTrue(config.looks_nested(root / "идея", root))
