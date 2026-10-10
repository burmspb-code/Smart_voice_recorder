# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Модель Whisper живёт в памяти один раз.

Загрузка medium занимает полторы минуты и полтора гигабайта.
Если грузить её при каждом запуске, каждая новая запись будет
стартовать с этой паузой. Здесь модель создаётся один раз и
держится, пока работает приложение.

Модель не потокобезопасна: одновременно с ней работает только
один поток распознавания. Поэтому и загрузка, и использование
проходят через один запор.
"""

import threading
from collections.abc import Callable

MODEL_CACHE: dict[str, object] = {}


class ModelStore:
    """Загружает модель в фоновом потоке и отдаёт её по требованию."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loading = False
        self._model = None

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def load_async(
        self,
        name: str,
        on_done: Callable[[object], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Начинает загрузку модели, не блокируя вызывающий поток.

        Повторный вызов, когда модель уже грузится или загружена,
        ничего не делает — второй экземпляр не создаётся.
        """
        if self._model is not None or self._loading:
            if self._model is not None and on_done:
                on_done(self._model)
            return

        self._loading = True
        thread = threading.Thread(
            target=self._load_worker,
            args=(name, on_done, on_error),
            daemon=True,
            name="model-loader",
        )
        thread.start()

    def _load_worker(
        self,
        name: str,
        on_done: Callable[[object], None] | None,
        on_error: Callable[[Exception], None] | None,
    ) -> None:
        try:
            model = _create(name)
        except Exception as error:  # noqa: BLE001 - причина уходит в строку статуса
            self._loading = False
            if on_error:
                on_error(error)
            return

        # Под тем же замком, что и get(): иначе распознавание,
        # начатое в момент загрузки, могло бы создать вторую модель
        with self._lock:
            self._model = model
        self._loading = False
        if on_done:
            on_done(model)

    def get(self, name: str) -> object:
        """Отдаёт готовую модель, создавая её при необходимости.

        Вызывать только из того же потока, который её уже загрузил:
        это блокирующий вызов для распознавания.
        """
        with self._lock:
            if self._model is None:
                self._model = _create(name)
            return self._model

    def unload(self) -> None:
        """Отпускает модель. Нужна, если пользователь сменил размер."""
        with self._lock:
            self._model = None
        MODEL_CACHE.clear()


def _create(name: str) -> object:
    """Создаёт модель. CPU и int8 — единственная связка, проверенная на этой машине."""
    from faster_whisper import WhisperModel

    return WhisperModel(name, device="cpu", compute_type="int8")
