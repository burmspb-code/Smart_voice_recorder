# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты model_store: подсчёт скачанного для прогресса загрузки."""

import tempfile
import unittest
from pathlib import Path

from src.model_store import _blob_bytes


class BlobBytesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_counts_real_files_skips_snapshot_duplicates(self) -> None:
        blobs = self.root / "blobs"
        blobs.mkdir(parents=True)
        (self.root / "snapshots" / "main").mkdir(parents=True)
        (self.root / "trees").mkdir()
        (self.root / "refs").mkdir()
        (blobs / "a.bin").write_bytes(b"x" * 10)

        # Временный файл докачки лежит в blobs/.cache — он тоже считаем
        (blobs / ".cache").mkdir()
        (blobs / ".cache" / "part.tmp").write_bytes(b"x" * 5)

        # Дубль в snapshots (режим копирования) и служебные trees
        # прогресс удваивать не должны
        (self.root / "snapshots" / "main" / "a.bin").write_bytes(b"x" * 10)
        (self.root / "trees" / "t").write_bytes(b"x" * 2)
        (self.root / "refs" / "r").write_bytes(b"x" * 1)

        self.assertEqual(_blob_bytes(self.root), 16)

    def test_skips_symlinks(self) -> None:
        target = self.root / "blobs" / "real.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * 7)
        link = self.root / "blobs" / "link.bin"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks не поддержаны")
        link.touch()

        self.assertEqual(_blob_bytes(self.root), 7)

    def test_empty_cache_is_zero(self) -> None:
        (self.root / "blobs").mkdir(parents=True)

        self.assertEqual(_blob_bytes(self.root), 0)
