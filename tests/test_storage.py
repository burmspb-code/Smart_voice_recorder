# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты чистой логики хранения записей — без Qt и без железа."""

import os
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest import mock

import storage


def make_wav(path: Path, seconds: float) -> None:
    """Настоящий wav-файл заданной длины: заголовок читается без звука."""
    frames = int(8000 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * frames)


class FormatDurationTest(unittest.TestCase):
    def test_short_and_long(self) -> None:
        self.assertEqual(storage.format_duration(7), "0:07")
        self.assertEqual(storage.format_duration(754), "12:34")

    def test_seconds_round_down(self) -> None:
        self.assertEqual(storage.format_duration(7.9), "0:07")

    def test_pad_keeps_minutes_as_two_digits(self) -> None:
        self.assertEqual(storage.format_duration(7, pad=True), "00:07")
        self.assertEqual(storage.format_duration(754, pad=True), "12:34")


class SanitizeTest(unittest.TestCase):
    def test_forbidden_chars_become_underscores(self) -> None:
        self.assertEqual(storage.sanitize('идея: "книга"'), "идея_ _книга_")

    def test_empty_name_stays_empty(self) -> None:
        self.assertEqual(storage.sanitize("   "), "")


class BlockDirTest(unittest.TestCase):
    def test_block_name_is_sanitized(self) -> None:
        root = Path("C:/recordings")
        self.assertEqual(storage.block_dir(root, "a:b"), Path("C:/recordings") / "a_b")

    def test_unnamed_records_get_no_block_folder(self) -> None:
        root = Path("C:/recordings")
        self.assertEqual(storage.block_dir(root, ""), root / storage.NO_BLOCK)


class ParseWhenTest(unittest.TestCase):
    def test_full_timestamp(self) -> None:
        when = storage.parse_when("2026-10-09_18-30-27")
        self.assertEqual(when, datetime(2026, 10, 9, 18, 30, 27))

    def test_numeric_suffix_is_dropped(self) -> None:
        when = storage.parse_when("2026-10-09_18-30-27_2")
        self.assertEqual(when, datetime(2026, 10, 9, 18, 30, 27))

    def test_garbage_gives_none(self) -> None:
        self.assertIsNone(storage.parse_when("заметка"))


class UniquePathTest(unittest.TestCase):
    def test_conflicting_names_get_numeric_suffix(self) -> None:
        class FixedNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 1, 1, 12, 0, 0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(storage, "datetime", FixedNow):
                first = storage.unique_path(root, "блок")
                # Путь резервируется, файл пишет уже рекордер:
                # занимаем его сами, чтобы второй вызов в ту же секунду
                # ушёл на имя с номером
                first.write_bytes(b"\x00")
                second = storage.unique_path(root, "блок")

        self.assertEqual(first.name, "2026-01-01_12-00-00.wav")
        self.assertEqual(second.name, "2026-01-01_12-00-00_2.wav")


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_records_newest_first_by_file_time(self) -> None:
        folder = self.root / "блок"
        folder.mkdir(parents=True)
        older = folder / "2026-10-09_18-30-27.wav"
        newer = folder / "2026-10-10_09-00-00.wav"
        make_wav(older, 2.0)
        make_wav(newer, 1.0)
        # Порядок — по времени файла, а не по имени
        os.utime(older, (0, 1_600_000_000))
        os.utime(newer, (0, 1_700_000_000))

        found = storage.scan(self.root)

        self.assertEqual([r.name for r in found], [newer.name, older.name])
        self.assertEqual(found[0].duration, 1.0)  # из заголовка wav
        self.assertEqual(found[0].block, "блок")
        self.assertFalse(found[0].has_text)

    def test_text_file_marks_record_as_done(self) -> None:
        folder = self.root / "блок"
        folder.mkdir(parents=True)
        wav = folder / "a.wav"
        make_wav(wav, 1.0)
        wav.with_suffix(".txt").write_text("текст", encoding="utf-8")

        self.assertTrue(storage.scan(self.root)[0].has_text)
        self.assertEqual(storage.pending(self.root), [])

    def test_files_in_root_have_no_block(self) -> None:
        make_wav(self.root / "a.wav", 1.0)

        found = storage.scan(self.root)

        self.assertEqual(found[0].block, "")
        self.assertEqual(found[0].block_folder, storage.NO_BLOCK)

    def test_blocks_collected_once(self) -> None:
        for block in ("идея", "идея", "дом"):
            folder = self.root / storage.sanitize(block)
            folder.mkdir(parents=True, exist_ok=True)
            make_wav(folder / "a.wav", 1.0)

        self.assertEqual(sorted(storage.blocks(self.root)), ["дом", "идея"])
