# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты рекордера: сброс звука на диск, сборка wav, отмена.

Железо не трогаем — логика проверяется напрямую: буфер, черновик,
длительность. Поток звука тут не открывается.
"""

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from src.recorder import SAMPLE_RATE, Recorder


class SpillTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_small_buffer_stays_in_memory(self) -> None:
        recorder = Recorder()
        recorder._spill_path = self.root / "a.raw"
        recorder._frames = [np.zeros(SAMPLE_RATE * 5, dtype=np.int16)]

        recorder._flush_pending()

        self.assertFalse(recorder._spill_path.exists())
        self.assertEqual(len(recorder._frames), 1)

    def test_overflow_goes_to_spill(self) -> None:
        recorder = Recorder()
        recorder._spill_path = self.root / "b.raw"
        recorder._frames = [np.zeros(SAMPLE_RATE * 40, dtype=np.int16)]

        recorder._flush_pending()

        self.assertTrue(recorder._spill_path.is_file())
        self.assertEqual(recorder._frames, [])
        self.assertAlmostEqual(recorder._spilled, 40.0)

    def test_save_combines_spill_and_tail(self) -> None:
        recorder = Recorder()
        recorder._spill_path = self.root / "c.raw"
        recorder._path = self.root / "c.wav"
        recorder._frames = [np.zeros(SAMPLE_RATE * 5, dtype=np.int16)]
        recorder._flush_pending(force=True)
        recorder._frames = [np.ones(SAMPLE_RATE * 3, dtype=np.int16)]
        recorder._stop.set()

        recorder._wait_worker()

        with wave.open(str(recorder._path), "rb") as wav:
            data = wav.readframes(wav.getnframes())
        samples = np.frombuffer(data, dtype=np.int16)
        self.assertEqual(len(samples), SAMPLE_RATE * 8)
        self.assertTrue((samples[: SAMPLE_RATE * 5] == 0).all())
        self.assertTrue((samples[SAMPLE_RATE * 5 :] == 1).all())
        self.assertFalse(recorder._spill_path.exists())

    def test_cancel_discards_everything(self) -> None:
        recorder = Recorder()
        recorder._spill_path = self.root / "d.raw"
        recorder._path = self.root / "d.wav"
        recorder._frames = [np.zeros(SAMPLE_RATE * 2, dtype=np.int16)]
        recorder._flush_pending(force=True)
        recorder._cancelled.set()
        recorder._stop.set()

        recorder._wait_worker()

        self.assertFalse(recorder._path.exists())
        self.assertFalse(recorder._spill_path.exists())
        self.assertEqual(recorder._spilled, 0.0)

    def test_silent_recording_does_not_create_file(self) -> None:
        emptied = []
        recorder = Recorder(on_empty=lambda: emptied.append(1))
        recorder._spill_path = self.root / "e.raw"
        recorder._path = self.root / "e.wav"
        recorder._stop.set()

        recorder._wait_worker()

        self.assertEqual(emptied, [1])
        self.assertFalse(recorder._path.exists())

    def test_duration_includes_spill(self) -> None:
        recorder = Recorder()
        recorder._frames = [np.zeros(SAMPLE_RATE * 3, dtype=np.int16)]
        recorder._spilled = 60.0

        self.assertAlmostEqual(recorder._duration(), 63.0)
