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
    RECORDING_GAIN,
    SAMPLE_RATE,
    Recorder,
    _amplify,
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
        # Голос в записи был — значит, файл сохранится целиком
        recorder._voice_seen = True
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

    def test_duration_counts_silence_too(self) -> None:
        # Файл сохраняется целиком, вместе с тишиной: длительность
        # записи не укорачивается
        recorder = Recorder()
        recorder._frames = [np.zeros(SAMPLE_RATE * 3, dtype=np.int16)]
        recorder._spilled = 60.0
        recorder._voice_seen = True

        self.assertAlmostEqual(recorder._duration(), 63.0)


class GainTest(unittest.TestCase):
    """Усиление записи: громче на десятую часть, без переполнения."""

    def test_sound_gets_louder(self) -> None:
        quieter = np.array([0, 1000, -1000], dtype=np.int16)

        louder = _amplify(quieter)

        self.assertEqual(louder.dtype, np.int16)
        self.assertEqual(louder[0], 0)
        self.assertEqual(louder[1], round(1000 * RECORDING_GAIN))
        self.assertEqual(louder[2], round(-1000 * RECORDING_GAIN))

    def test_full_scale_is_clipped_not_wrapped(self) -> None:
        # Громкий звук после усиления не должен менять знак: иначе
        # на месте громкого хлопка в файле окажется треск
        loud = np.array([32000, -32000, 32767], dtype=np.int16)

        louder = _amplify(loud)

        self.assertEqual(louder[0], 32767)
        self.assertEqual(louder[1], -32768)
        self.assertEqual(louder[2], 32767)

    def test_source_chunk_is_left_alone(self) -> None:
        source = np.array([1000, -1000], dtype=np.int16)

        _amplify(source)

        self.assertEqual(source.tolist(), [1000, -1000])

    def test_saved_file_is_louder(self) -> None:
        # Весь путь целиком: колбэк -> буфер -> черновик -> wav
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        recorder = Recorder()
        recorder._path = root / "g.wav"
        recorder._spill_path = root / "g.raw"

        tone = np.full(SAMPLE_RATE, 1000, dtype=np.int16)
        recorder._audio_callback(tone.reshape(-1, 1), len(tone), None, None)
        recorder._stop.set()
        recorder._wait_worker()

        with wave.open(str(recorder._path), "rb") as wav:
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)

        self.assertEqual(len(data), SAMPLE_RATE)
        self.assertEqual(int(np.abs(data).max()), round(1000 * RECORDING_GAIN))


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

    def test_russian_names_do_not_merge_microphones(self) -> None:
        """Русское «Микрофон» есть в любом имени, опознавать по нему нельзя.

        Windows с русским языком называет все устройства «Микрофон
        (…)», поэтому без русских слов-пустышек оба микрофона
        находились как один и тот же.
        """
        physical = {
            "{k20}": "Микрофон HK 1080P K20Pro",
            "{rtk}": "Микрофон Realtek(R) Audio",
        }

        self.assertEqual(_distinctive("Микрофон Realtek(R) Audio"), {"realtek"})
        self.assertEqual(
            _distinctive("Микрофон HK 1080P K20Pro"), {"1080p", "k20pro"}
        )
        self.assertEqual(
            _match_endpoint("Микрофон (HK 1080P K20Pro)", physical), "{k20}"
        )
        self.assertEqual(
            _match_endpoint("Микрофон (Realtek(R) Audio)", physical), "{rtk}"
        )

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
