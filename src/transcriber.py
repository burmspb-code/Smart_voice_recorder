# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Расшифровка записей локально, моделью Whisper.

Модуль не знает про интерфейс: работает в своём потоке и сообщает
о ходе через колбэки. Модель берётся из ModelStore, поэтому грузится
она один раз на всё приложение.

Что распознавать выбирает вызывающий код: конкретные файлы
или все нераспознанные.
"""

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from .model_store import ModelStore

LANGUAGE = "ru"

# Пауза в речи длиннее этого считается новым абзацем.
# Длинные абзацы тяжело править: не видно, где кончилась мысль.
PARAGRAPH_GAP = 2.0


def format_text(
    segments: list[tuple[float, float, str]],
    timecodes: bool = True,
) -> str:
    """Собирает текст с абзацами.

    Новый абзац начинается там, где человек замолчал дольше двух секунд.

    Метки времени можно не ставить: готовый текст, который уходит
    в буфер обмена или чужой программе, от них только мешает.
    """
    parts: list[str] = []
    previous_end: float | None = None

    for start, end, text in segments:
        if previous_end is not None and start - previous_end > PARAGRAPH_GAP:
            parts.append("")  # пустая строка = граница абзаца

        stamp = ""
        if timecodes:
            minutes = int(start // 60)
            seconds = int(start % 60)
            stamp = f"[{minutes:02d}:{seconds:02d}] "

        parts.append(f"{stamp}{text.strip()}")
        previous_end = end

    return "\n".join(parts)


def transcribe_file(
    model,
    wav: Path,
    should_cancel: Callable[[], bool] | None = None,
    timecodes: bool = True,
) -> tuple[int, str, bool]:
    """Расшифровывает один файл.

    Возвращает число строк, текст и признак «дописано до конца».

    Куски речи приходят лениво, по одному, поэтому остановка
    возможна прямо посреди разбора: выходим из цикла и уходим,
    ничего не записав. Рядом не остаётся обрезанного текста,
    запись просто остаётся без текста, и её можно распознать
    заново.
    """
    collected = _read_segments(model, wav, vad_filter=True, should_cancel=should_cancel)
    if collected is None:
        return 0, "", False

    text = format_text(collected, timecodes=timecodes)
    wav.with_suffix(".txt").write_text(text, encoding="utf-8")
    return len(collected), text, True


def _read_segments(
    model,
    wav: Path,
    vad_filter: bool,
    should_cancel: Callable[[], bool] | None,
) -> list[tuple[float, float, str]] | None:
    """Читает куски речи. None — пользователь нажал отмену.

    Сначала пробуем с фильтром тишины: он вырезает паузы, и разбор
    заметно быстрее и чище. Но модель фильтра — это отдельный файл
    рядом с faster-whisper, и в собранном .exe он может не оказаться
    на месте (и на чужой машине тоже). Тогда пробуем без него:
    медленнее и шумнее на тихих записях, но работает всегда.
    """
    try:
        raw_segments, _info = model.transcribe(
            str(wav),
            language=LANGUAGE,
            vad_filter=vad_filter,
            beam_size=5,
        )

        collected = []
        for segment in raw_segments:
            if should_cancel and should_cancel():
                return None
            collected.append((segment.start, segment.end, segment.text))
    except Exception:
        if vad_filter:
            logging.getLogger(__name__).warning(
                "Фильтр тишины не сработал, пробуем без него: %s", wav.name
            )
            return _read_segments(model, wav, vad_filter=False, should_cancel=should_cancel)
        raise

    return collected


class Transcriber:
    """Распознаёт файлы по очереди, в своём потоке.

    Один поток на всё приложение: модель не потокобезопасна,
    два одновременных распознавания могут сломать её состояние.
    """

    def __init__(self, store: ModelStore) -> None:
        self._store = store
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        files: Iterable[Path],
        model_name: str,
        on_file_start: Callable[[Path], None] | None = None,
        on_file_done: Callable[[Path, int, str], None] | None = None,
        on_all_done: Callable[[int, int, bool], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        timecodes: bool = True,
    ) -> bool:
        """Ставит файлы в очередь. False — уже идёт распознавание."""
        if self.is_busy:
            return False

        queue = [Path(f) for f in files]
        if not queue:
            return False

        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(
                queue,
                model_name,
                on_file_start,
                on_file_done,
                on_all_done,
                on_error,
                timecodes,
            ),
            daemon=True,
            name="transcriber",
        )
        self._thread.start()
        return True

    def cancel(self) -> None:
        """Просит остановиться: прерывание возможно прямо посреди файла.

        Такой файл остаётся без расшифровки — чтобы не появился
        обрезанный текст, — и его можно распознать заново.
        """
        self._cancel.set()

    def _run(
        self,
        queue: list[Path],
        model_name: str,
        on_file_start,
        on_file_done,
        on_all_done,
        on_error,
        timecodes: bool,
    ) -> None:
        done = 0
        total = len(queue)

        try:
            model = self._store.get(model_name)
        except Exception as error:  # noqa: BLE001 - причина уходит в строку статуса
            if on_error:
                on_error(error)
            return

        for wav in queue:
            if self._cancel.is_set():
                break

            if on_file_start:
                on_file_start(wav)

            try:
                segments, text, finished = transcribe_file(
                    model,
                    wav,
                    should_cancel=self._cancel.is_set,
                    timecodes=timecodes,
                )
            except Exception as error:  # noqa: BLE001 - причина уходит в строку статуса
                if on_error:
                    on_error(error)
                break

            # Прервали на полуслове: файла не будет, заново распознаём
            if not finished:
                break

            done += 1
            if on_file_done:
                on_file_done(wav, segments, text)

        if on_all_done:
            on_all_done(done, total, self._cancel.is_set())
