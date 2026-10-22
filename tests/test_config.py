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
        # Все три переключателя при первом запуске включены
        self.assertTrue(settings.auto_transcribe)
        self.assertTrue(settings.auto_copy)
        self.assertTrue(settings.timecodes)
        # Авто-стоп тоже включён, срок — пять секунд
        self.assertEqual(settings.auto_stop_seconds, config.DEFAULT_AUTO_STOP_SECONDS)

    def test_every_timeout_has_a_name_for_the_list(self) -> None:
        # Список в окне собирается по AUTO_STOP_CHOICES: срок без
        # названия уронил бы окно при сборке
        for seconds in config.AUTO_STOP_CHOICES:
            self.assertIn(seconds, config.AUTO_STOP_LABELS)

    def test_round_trip(self) -> None:
        settings = config.Settings()
        settings.recordings_dir = "C:/записи"
        settings.model = "small"
        settings.microphone = 3
        settings.last_block = "идея"
        settings.auto_transcribe = True
        settings.auto_copy = False
        settings.auto_stop_seconds = 10
        settings.save()

        again = config.Settings()

        self.assertEqual(again.recordings_dir, "C:/записи")
        self.assertEqual(again.model, "small")
        self.assertEqual(again.microphone, 3)
        self.assertEqual(again.last_block, "идея")
        self.assertTrue(again.auto_transcribe)
        self.assertFalse(again.auto_copy)
        self.assertEqual(again.auto_stop_seconds, 10)

    def test_disabled_auto_stop_survives(self) -> None:
        # Выключенный автостоп — это пустое значение срока
        settings = config.Settings()
        settings.auto_stop_seconds = None
        settings.save()

        again = config.Settings()

        self.assertIsNone(again.auto_stop_seconds)

    def test_nonsense_timeout_keeps_default(self) -> None:
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"auto_stop_seconds": "сколько хочешь"}', encoding="utf-8"
        )

        settings = config.Settings()

        self.assertEqual(settings.auto_stop_seconds, config.DEFAULT_AUTO_STOP_SECONDS)

    def test_boolean_timeout_keeps_default(self) -> None:
        # True — это тоже int в Python, но сроком в секундах он не бывает
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"auto_stop_seconds": true}', encoding="utf-8")

        settings = config.Settings()

        self.assertEqual(settings.auto_stop_seconds, config.DEFAULT_AUTO_STOP_SECONDS)

    def test_timeout_outside_the_list_keeps_default(self) -> None:
        # В окне список короткий и постоянный, поэтому срок вне его
        # заменяется на обычный, а не появляется лишним пунктом
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"auto_stop_seconds": 7}', encoding="utf-8")

        settings = config.Settings()

        self.assertEqual(settings.auto_stop_seconds, config.DEFAULT_AUTO_STOP_SECONDS)

    def test_timeout_from_the_list_survives(self) -> None:
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"auto_stop_seconds": 10}', encoding="utf-8")

        settings = config.Settings()

        self.assertEqual(settings.auto_stop_seconds, 10)

    def test_null_timeout_means_disabled(self) -> None:
        # Пустой срок — это выключенный автостоп
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"auto_stop_seconds": null}', encoding="utf-8")

        settings = config.Settings()

        self.assertIsNone(settings.auto_stop_seconds)

    def test_old_format_with_disabled_checkbox_stays_disabled(self) -> None:
        # Прежний формат хранил галочку и срок отдельно: выключенная
        # галочка не должна включиться сама
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"auto_stop": false, "auto_stop_seconds": 5}', encoding="utf-8"
        )

        settings = config.Settings()

        self.assertIsNone(settings.auto_stop_seconds)

    def test_old_format_with_enabled_checkbox_keeps_timeout(self) -> None:
        path = config.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"auto_stop": true, "auto_stop_seconds": 10}', encoding="utf-8"
        )

        settings = config.Settings()

        self.assertEqual(settings.auto_stop_seconds, 10)

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
