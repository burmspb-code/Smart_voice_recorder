# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты записи падений: причина должна оставаться на диске."""

import os
import sys
import tempfile
import unittest

from src import crash_log


class CrashLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_appdata = os.environ.get("APPDATA")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["APPDATA"] = self._tmp.name
        # Print в консоль на время теста не нужен
        self._old_hook = sys.__excepthook__
        sys.__excepthook__ = lambda *args: None

    def tearDown(self) -> None:
        sys.__excepthook__ = self._old_hook
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata
        self._tmp.cleanup()

    def test_exception_lands_in_the_file(self) -> None:
        try:
            raise ValueError("поломка")
        except ValueError:
            crash_log._hook(*sys.exc_info())

        text = crash_log.log_path().read_text(encoding="utf-8")

        self.assertIn("ValueError: поломка", text)
        self.assertIn("Traceback", text)

    def test_errors_are_appended_not_overwritten(self) -> None:
        for message in ("первая", "вторая"):
            try:
                raise RuntimeError(message)
            except RuntimeError:
                crash_log._hook(*sys.exc_info())

        text = crash_log.log_path().read_text(encoding="utf-8")

        self.assertIn("первая", text)
        self.assertIn("вторая", text)
