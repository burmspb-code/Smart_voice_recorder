# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only

"""Тесты расшифровки: сборка текста и отмена — без модели Whisper."""

import tempfile
import unittest
from pathlib import Path

from src.transcriber import format_text, transcribe_file


class FormatTextTest(unittest.TestCase):
    def test_timestamp_and_text(self) -> None:
        segments = [(0.0, 1.5, "привет")]
        self.assertEqual(format_text(segments), "[00:00] привет")

    def test_long_pause_starts_new_paragraph(self) -> None:
        segments = [(0.0, 1.0, "первый"), (5.0, 6.0, "второй")]
        self.assertEqual(
            format_text(segments),
            "[00:00] первый\n\n[00:05] второй",
        )

    def test_short_pause_keeps_one_paragraph(self) -> None:
        segments = [(0.0, 1.0, "первый"), (2.0, 3.0, "второй")]
        self.assertEqual(
            format_text(segments),
            "[00:00] первый\n[00:02] второй",
        )

    def test_empty_segments_give_empty_text(self) -> None:
        self.assertEqual(format_text([]), "")


class Segment:
    """Один кусок речи, как его отдаёт faster-whisper."""

    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class WhisperStub:
    """Подделка модели: отдаёт заранее готовые куски речи."""

    def __init__(self, segments: list[Segment]) -> None:
        self._segments = segments

    def transcribe(self, path, **kwargs):
        return iter(self._segments), object()


class TranscribeFileTest(unittest.TestCase):
    @staticmethod
    def _wav_file() -> Path:
        return Path(tempfile.mkdtemp()) / "запись.wav"

    def test_writes_text_when_finished(self) -> None:
        wav = self._wav_file()
        model = WhisperStub([Segment(0.0, 1.0, "привет"), Segment(2.0, 3.0, "мир")])

        lines, text, finished = transcribe_file(model, wav)

        self.assertTrue(finished)
        self.assertEqual(lines, 2)
        self.assertEqual(text, "[00:00] привет\n[00:02] мир")
        self.assertTrue(wav.with_suffix(".txt").is_file())

    def test_cancel_mid_file_leaves_no_text(self) -> None:
        wav = self._wav_file()
        model = WhisperStub([Segment(0.0, 1.0, "привет")])

        lines, text, finished = transcribe_file(
            model, wav, should_cancel=lambda: True
        )

        self.assertEqual((lines, text, finished), (0, "", False))
        self.assertFalse(wav.with_suffix(".txt").exists())


class VadBrokenStub(WhisperStub):
    """Модель, у которой фильтр тишины не работает.

    Так выглядит запуск на чужой машине или в собранном .exe, где
    файл silero_vad_v6.onnx не оказался рядом с faster-whisper.
    """

    def __init__(self, segments: list[Segment]) -> None:
        super().__init__(segments)
        self.attempts: list[bool] = []

    def transcribe(self, path, **kwargs):
        vad = kwargs.get("vad_filter", False)
        self.attempts.append(vad)
        if vad:
            raise RuntimeError("NoSuchFile: silero_vad_v6.onnx")
        return super().transcribe(path, **kwargs)


class VadFallbackTest(unittest.TestCase):
    """Без модели фильтра тишины распознавание всё равно идёт."""

    @staticmethod
    def _wav_file() -> Path:
        return Path(tempfile.mkdtemp()) / "запись.wav"

    def test_retries_without_vad_filter(self) -> None:
        wav = self._wav_file()
        model = VadBrokenStub([Segment(0.0, 1.0, "привет")])

        lines, text, finished = transcribe_file(model, wav)

        self.assertTrue(finished)
        self.assertEqual(lines, 1)
        self.assertEqual(text, "[00:00] привет")
        self.assertEqual(model.attempts, [True, False])

    def test_other_error_is_not_hidden(self) -> None:
        class BrokenStub(WhisperStub):
            def transcribe(self, path, **kwargs):
                raise OSError("файл повреждён")

        with self.assertRaises(OSError):
            transcribe_file(BrokenStub([]), self._wav_file())
