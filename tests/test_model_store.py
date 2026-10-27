# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты model_store: подсчёт скачанного для прогресса загрузки."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from src import model_store
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


class GetModelTest(unittest.TestCase):
    def test_get_waits_for_background_load(self) -> None:
        """get() не создаёт вторую копию, пока первая ещё грузится."""
        store = model_store.ModelStore()
        created: list[str] = []
        started = threading.Event()

        def fake_create(name: str) -> str:
            created.append(name)
            started.set()
            time.sleep(0.2)
            return f"model:{name}"

        with mock.patch.object(model_store, "_create", fake_create):
            store.load_async("medium")
            self.assertTrue(started.wait(timeout=5))
            model = store.get("medium")

        self.assertEqual(model, "model:medium")
        self.assertEqual(created, ["medium"], "модель создана дважды")

    def test_get_replaces_model_of_another_size(self) -> None:
        """Смена модели в списке не игнорируется молча."""
        store = model_store.ModelStore()

        with mock.patch.object(model_store, "_create", lambda name: f"model:{name}"):
            self.assertEqual(store.get("tiny"), "model:tiny")
            self.assertEqual(store.get("small"), "model:small")

    def test_get_reuses_already_loaded_model(self) -> None:
        store = model_store.ModelStore()
        created: list[str] = []

        def fake_create(name: str) -> str:
            created.append(name)
            return f"model:{name}"

        with mock.patch.object(model_store, "_create", fake_create):
            store.get("base")
            store.get("base")

        self.assertEqual(created, ["base"])
