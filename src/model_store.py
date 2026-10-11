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

Модуль также знает, откуда берётся модель и как она хранится на
диске: где лежит, скачана ли, сколько занимает места. Эти сведения
нужны окну, чтобы первый запуск был понятным.
"""

import logging
import os
import shutil
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import models_dir, settings_dir

# --- Тихий режим библиотек Hugging Face ------------------------------------
# Переменные выставляются до первого импорта huggingface_hub и
# faster_whisper: библиотеки читают их при импорте. Приложение говорит
# с пользователем своими сообщениями, а «сырые» строки про отсутствие
# токена и симлинки в консоли только пугают. Чтобы убрать и их, и
# подсказки о ходе загрузки, хватает этих четырёх переменных.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

# Модель хранится не в общем кэше Hugging Face, а в папке приложения.
# Кэш выглядит одноразовым и его могут удалить; модель должна пережить
# очистку и переклоне проекта. Переменную нужно задать до импорта
# huggingface_hub — он читает её при загрузке и дальше не пересматривает.
os.environ.setdefault("HF_HUB_CACHE", str(models_dir()))


def _quiet_library_logs() -> None:
    """Уводит служебные сообщения библиотек на уровень ERROR и выше."""
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("hf_xet").setLevel(logging.ERROR)


def repo_id(name: str) -> str:
    """Имя модели → идентификатор репозитория на Hugging Face.

    «medium» превращается в «Systran/faster-whisper-medium». Если дали
    уже готовый идентификатор или путь, возвращается как есть.
    """
    from faster_whisper.utils import _MODELS

    return _MODELS.get(name, name)


def storage_dir() -> Path:
    """Папка, где лежит модель. Задаётся через HF_HUB_CACHE на импорте."""
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def repo_cache_dir(name: str) -> Path:
    """Папка одной модели внутри хранилища."""
    return storage_dir() / ("models--" + repo_id(name).replace("/", "--"))


def _legacy_hub_dir() -> Path:
    """Стандартный кэш Hugging Face, где модель лежала раньше.

    Он же остаётся местом по умолчанию, если HF_HUB_CACHE не задавать.
    """
    return Path.home() / ".cache" / "huggingface" / "hub"


def migrate_from_legacy(name: str) -> Path | None:
    """Переносит модель из старого кэша HF в папку приложения.

    Раньше модель скачивалась в общий кэш (~/.cache/huggingface).
    Теперь она живёт в профиле пользователя, но качать полтора
    гигабайта заново не нужно: переносим уже готовую.

    Возвращает путь, откуда перенесли, либо None, если переносить
    нечего — модель уже на месте или её нет в старом кэше.
    """
    target = repo_cache_dir(name)
    if target.exists():
        return None

    legacy = _legacy_hub_dir() / target.name
    # Переезд на самого себя и неполный старый кэш пропускаем
    if legacy == target or not (legacy / "snapshots").is_dir():
        return None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
    except OSError:
        # Не смогли перенести — скачаем заново, это не повод падать
        return None

    return legacy


def symlinks_supported() -> bool:
    """Поддерживает ли система симлинки в кэше.

    Без симлинков библиотека копирует файлы, и модель занимает вдвое
    больше места. На Windows симлинки доступны только в режиме
    разработчика или от администратора.
    """
    try:
        from huggingface_hub import file_download

        return bool(file_download.are_symlinks_supported())
    except Exception:  # noqa: BLE001 - не смогли проверить, считаем обычный режим
        return True


def is_cached(name: str) -> bool:
    """Скачана ли модель в папку приложения и лежит ли там целиком."""
    try:
        from huggingface_hub import try_to_load_from_cache

        found = try_to_load_from_cache(repo_id(name), "model.bin")
    except Exception:  # noqa: BLE001 - кэш недоступен, значит ещё не скачано
        return False

    return isinstance(found, str) and Path(found).is_file()


def model_size_on_disk(name: str) -> int:
    """Сколько байт модель занимает на диске.

    Симлинки не считаются: они указывают на уже посчитанные файлы.
    А копии в режиме без симлинков считаются — это и есть настоящий
    расход места, который увидит пользователь.
    """
    total = 0
    for root, _dirs, files in os.walk(repo_cache_dir(name)):
        for file_name in files:
            path = Path(root) / file_name
            try:
                if path.is_symlink():
                    continue
                total += path.stat().st_size
            except OSError:
                continue
    return total


def format_size(size: int) -> str:
    """Байты в понятный вид: 1,5 ГБ, 460 МБ, 75 МБ."""
    if size <= 0:
        return "неизвестно"
    if size >= 1024**3:
        return f"{size / 1024**3:.1f}".replace(".", ",") + " ГБ"
    return f"{size / 1024**2:.0f} МБ"


@dataclass
class ModelInfo:
    """Что известно о модели до и после загрузки."""

    name: str
    repo_id: str
    models_dir: Path
    cached: bool
    symlinks: bool
    footprint: int
    moved_from: Path | None = None

    @property
    def duplicates(self) -> bool:
        """Хранилище держит копии, а не симлинки: места нужно вдвое больше."""
        return not self.symlinks


def describe(name: str) -> ModelInfo:
    """Собирает сведения о модели для сообщения пользователю.

    Заодно переносит модель из старого кэша Hugging Face, если она
    там осталась: пользователь не должен качать её заново.
    """
    _quiet_library_logs()
    moved_from = migrate_from_legacy(name)
    cached = is_cached(name)
    return ModelInfo(
        name=name,
        repo_id=repo_id(name),
        models_dir=storage_dir(),
        cached=cached,
        symlinks=symlinks_supported(),
        footprint=model_size_on_disk(name) if cached else 0,
        moved_from=moved_from,
    )


@contextmanager
def _muted_console():
    """Прячет служебный вывод библиотек на время загрузки модели.

    Часть сообщений Hugging Face печатает мимо sys.stderr — прямо в
    файловый дескриптор, поэтому фильтра warnings недостаточно.
    Перенаправляем сам дескриптор, но пишем в файл рядом с настройками,
    а не в никуда: если загрузка сорвётся, лог останется для разбора.
    """
    log_path = settings_dir() / "model-load.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        saved = os.dup(2)
    except OSError:
        # Нет второго дескриптора (запуск без консоли) — глушить нечего
        yield
        return

    try:
        handle = open(log_path, "wb", buffering=0)  # noqa: PTH123, SIM115 - нужен дескриптор
    except OSError:
        os.close(saved)
        yield
        return

    try:
        os.dup2(handle.fileno(), 2)
        yield
    finally:
        try:
            os.dup2(saved, 2)
        finally:
            os.close(saved)
            handle.close()


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


def _create(name: str) -> object:
    """Создаёт модель. CPU и int8 — единственная связка, проверенная на этой машине.

    Если модель уже скачана, работаем только с диском: ни сети, ни
    лишних запросов при обычном запуске.
    """
    _quiet_library_logs()
    from faster_whisper import WhisperModel

    local_only = is_cached(name)
    with _muted_console():
        return WhisperModel(
            name,
            device="cpu",
            compute_type="int8",
            local_files_only=local_only,
        )
