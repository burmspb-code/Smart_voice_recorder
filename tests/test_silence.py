# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты остановки записи по тишине.

Железо не трогаем: куски звука подаются прямо в колбэк рекордера,
ровно так же, как их подаёт PortAudio. Проверяем три вещи — как
считается тишина, был ли голос и что попадает в файл.
"""

import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from src.recorder import (
    SILENCE_FLOOR,
    VOICE_LEVEL,
    Recorder,
)

# Уровни для проверок. Молчание — ниже порога, голос — заметно выше,
# пограничный — между порогами, там, где решает мёртвая зона.
QUIET = SILENCE_FLOOR / 2
SPEECH = VOICE_LEVEL * 4
BORDERLINE = (SILENCE_FLOOR + VOICE_LEVEL) / 2

CHUNK = 1024


def chunk(level: float, frames: int = CHUNK) -> np.ndarray:
    """Кусок звука постоянного уровня."""
    return np.full(frames, int(level * 32768), dtype=np.int16)


class SilenceTest(unittest.TestCase):
    """Счётчик тишины: растёт, сбрасывается, не дрожит на границе."""

    def setUp(self) -> None:
        self.recorder = Recorder()

    def feed(self, level: float, frames: int = CHUNK) -> None:
        """Подаёт кусок звука в колбэк, как это делает PortAudio."""
        self.recorder._audio_callback(chunk(level, frames), frames, None, None)

    def test_quiet_starts_the_clock(self) -> None:
        self.feed(QUIET)

        self.assertGreater(self.recorder.silence_seconds, 0.0)

    def test_speech_clears_the_clock(self) -> None:
        self.feed(QUIET)
        self.feed(SPEECH)

        self.assertEqual(self.recorder.silence_seconds, 0.0)

    def test_clock_keeps_running_while_quiet(self) -> None:
        self.feed(QUIET)
        first = self.recorder.silence_seconds
        time.sleep(0.05)
        self.feed(QUIET)
        second = self.recorder.silence_seconds

        # Тишина непрерывная, значит и счётчик не сбрасывается
        self.assertGreater(second, first)

    def test_borderline_level_keeps_silence_running(self) -> None:
        self.feed(QUIET)
        self.feed(BORDERLINE)

        self.assertGreater(self.recorder.silence_seconds, 0.0)

    def test_borderline_level_does_not_start_silence(self) -> None:
        self.feed(SPEECH)
        self.feed(BORDERLINE)

        self.assertEqual(self.recorder.silence_seconds, 0.0)

    def test_loud_sound_ends_silence(self) -> None:
        self.feed(QUIET)
        self.feed(SPEECH)

        self.assertEqual(self.recorder.silence_seconds, 0.0)

    def test_voice_end_silence(self) -> None:
        # Пока голос идёт, тишины нет — счётчик стоит на нуле
        self.feed(SPEECH)

        self.assertEqual(self.recorder.silence_seconds, 0.0)


class VoiceSeenTest(unittest.TestCase):
    """Был ли голос в записи: без него файла не будет."""

    def setUp(self) -> None:
        self.recorder = Recorder()

    def feed(self, level: float, frames: int = CHUNK) -> None:
        self.recorder._audio_callback(chunk(level, frames), frames, None, None)

    def test_quiet_is_not_voice(self) -> None:
        self.feed(QUIET)

        self.assertFalse(self.recorder._voice_seen)

    def test_speech_is_voice(self) -> None:
        self.feed(SPEECH)

        self.assertTrue(self.recorder._voice_seen)

    def test_voice_stays_remembered(self) -> None:
        self.feed(SPEECH)
        self.feed(QUIET)

        # Голос был — значит, запись не пустая
        self.assertTrue(self.recorder._voice_seen)

    def test_voice_seen_property_is_public(self) -> None:
        # Окну нужен этот признак: пока голоса не было, автостоп
        # не останавливает запись
        self.feed(QUIET)
        self.assertFalse(self.recorder.voice_seen)

        self.feed(SPEECH)
        self.assertTrue(self.recorder.voice_seen)


class PauseTest(unittest.TestCase):
    """Пауза — это не молчание микрофона."""

    def setUp(self) -> None:
        self.recorder = Recorder()

    def test_pause_freezes_the_clock(self) -> None:
        self.recorder._audio_callback(chunk(QUIET), CHUNK, None, None)

        self.recorder.pause()

        self.assertEqual(self.recorder.silence_seconds, 0.0)

    def test_resume_starts_the_clock_again(self) -> None:
        self.recorder._audio_callback(chunk(QUIET), CHUNK, None, None)
        self.recorder.pause()

        self.recorder.resume()

        self.assertGreater(self.recorder.silence_seconds, 0.0)


class SavedFileTest(unittest.TestCase):
    """Что в итоге оказывается на диске."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _recorder(self, **kwargs) -> Recorder:
        recorder = Recorder(**kwargs)
        recorder._spill_path = self.root / "r.raw"
        recorder._path = self.root / "r.wav"
        return recorder

    def _feed(self, recorder: Recorder, level: float, frames: int = CHUNK) -> None:
        recorder._audio_callback(chunk(level, frames), frames, None, None)

    def _frames_in_file(self, path: Path) -> int:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes()

    def test_silent_tail_is_kept(self) -> None:
        recorder = self._recorder()
        self._feed(recorder, SPEECH)
        self._feed(recorder, SPEECH)
        self._feed(recorder, QUIET)
        self._feed(recorder, QUIET)
        recorder._stop.set()

        recorder._wait_worker()

        # Файл сохраняется целиком: обрезка отрезала часть записи
        self.assertTrue(recorder._path.is_file())
        self.assertEqual(self._frames_in_file(recorder._path), 4 * CHUNK)

    def test_silent_head_is_kept(self) -> None:
        recorder = self._recorder()
        self._feed(recorder, QUIET)
        self._feed(recorder, SPEECH)
        recorder._stop.set()

        recorder._wait_worker()

        self.assertEqual(self._frames_in_file(recorder._path), 2 * CHUNK)

    def test_silent_recording_leaves_no_file(self) -> None:
        emptied = []
        recorder = self._recorder(on_empty=lambda: emptied.append(1))
        self._feed(recorder, QUIET)
        self._feed(recorder, QUIET)
        recorder._stop.set()

        recorder._wait_worker()

        self.assertEqual(emptied, [1])
        self.assertFalse(recorder._path.exists())
        # Черновик тоже убран, иначе он остался бы мусором на диске
        self.assertFalse(recorder._spill_path.exists())

    def test_room_noise_is_not_voice(self) -> None:
        # Фон в комнате держится около 0.004 — это тишина, а не речь.
        # Запись из одного фона файла создавать не должна.
        emptied = []
        recorder = self._recorder(on_empty=lambda: emptied.append(1))
        self._feed(recorder, 0.004)
        self._feed(recorder, 0.006)
        self._feed(recorder, 0.003)
        recorder._stop.set()

        recorder._wait_worker()

        self.assertEqual(emptied, [1])
        self.assertFalse(recorder._path.exists())

    def test_duration_matches_the_saved_file(self) -> None:
        recorder = self._recorder()
        self._feed(recorder, QUIET)
        self._feed(recorder, SPEECH)
        self._feed(recorder, SPEECH)
        self._feed(recorder, QUIET)
        recorder._stop.set()

        recorder._wait_worker()

        self.assertAlmostEqual(
            recorder._duration(),
            self._frames_in_file(recorder._path) / 48000,
            places=3,
        )
