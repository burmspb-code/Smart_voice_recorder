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
from typing import ClassVar

import numpy as np

from src.recorder import (
    SAMPLE_RATE,
    Recorder,
    _distinctive,
    _group_candidates,
    _match_endpoint,
)


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


class PhysicalMicrophoneTest(unittest.TestCase):
    """Группировка микрофонов по физическим устройствам из реестра."""

    PHYSICAL: ClassVar[dict[str, str]] = {
        "{k20}": "Microphone HK 1080P K20Pro",
        "{amda}": "Microphone Array AMD Audio Device",
        "{rtk}": "Microphone Realtek High Definition Audio",
    }

    def test_distinctive_keeps_brand_words_only(self) -> None:
        self.assertEqual(_distinctive("Realtek High Definition Audio"), {"realtek"})
        self.assertEqual(_distinctive("Microphone Array AMD Audio Device"), {"amd"})

    def test_match_endpoint_finds_physical_device(self) -> None:
        self.assertEqual(
            _match_endpoint("Microphone (HK 1080P K20Pro)", self.PHYSICAL),
            "{k20}",
        )
        self.assertEqual(
            _match_endpoint("Microphone Array (AMD Audio Device)", self.PHYSICAL),
            "{amda}",
        )
        # Дубли WDM-KS с другими именами тоже опознаются
        self.assertEqual(
            _match_endpoint(
                "Microphone Array 1 (AMDAfdInstall Wave Microphone - 1)",
                self.PHYSICAL,
            ),
            "{amda}",
        )
        self.assertEqual(
            _match_endpoint("Microphone (Realtek HD Audio Mic input)", self.PHYSICAL),
            "{rtk}",
        )

    def test_group_keeps_one_entry_per_physical_device(self) -> None:
        devices = [
            (1, "Microphone (HK 1080P K20Pro)", 2),  # MME
            (16, "Microphone (HK 1080P K20Pro)", 0),  # WASAPI, лучший
            (17, "Microphone Array (AMD Audio Device)", 0),
            (18, "Microphone (Realtek High Definition Audio)", 0),
            (22, "Microphone (Realtek HD Audio Mic input)", 3),  # WDM-KS
            (23, "Microphone Array 1 (AMDAfdInstall Wave Microphone - 1)", 3),
            (29, "Headset (Redmi Buds 4 Pro)", 3),  # нет в реестре -> отброшен
        ]

        result = _group_candidates(devices, self.PHYSICAL)

        self.assertEqual(
            result,
            [
                (16, "Microphone (HK 1080P K20Pro)"),
                (17, "Microphone Array (AMD Audio Device)"),
                (18, "Microphone (Realtek High Definition Audio)"),
            ],
        )

    def test_without_registry_groups_by_exact_name(self) -> None:
        devices = [
            (1, "Microphone (HK 1080P K20Pro)", 2),
            (16, "Microphone (HK 1080P K20Pro)", 0),
            (17, "Microphone Array (AMD Audio Device)", 0),
        ]

        result = _group_candidates(devices, None)

        self.assertEqual(
            result,
            [(16, "Microphone (HK 1080P K20Pro)"),
             (17, "Microphone Array (AMD Audio Device)")],
        )

    def test_unrecognized_names_fall_back_instead_of_empty(self) -> None:
        # Реестр прочитался, но ни одно имя не совпало — список не пустеет
        devices = [(5, "Completely Different Name", 0)]

        result = _group_candidates(devices, {"{x}": "Fancy Brand X USB Mic"})

        self.assertEqual(result, [(5, "Completely Different Name")])
