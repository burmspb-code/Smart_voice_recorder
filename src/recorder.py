# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Запись голоса с микрофона.

Модуль ничего не знает про интерфейс: он пишет файл и сообщает
о событиях через колбэки. Поэтому его можно вызывать и из окна,
и из консольного скрипта, и из тестов.

Работает в своём потоке, потому что запись идёт непрерывно,
а интерфейсу нужно в это время оставаться живым.
"""

import threading
import time
import wave
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

try:
    import winreg
except ImportError:  # не Windows — реестра нет
    winreg = None

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 48000
CHANNELS = 1
DTYPE = "int16"

# Усиление записи. Микрофон отдаёт звук тише, чем хочется слышать
# в наушниках, поэтому всё записанное поднимается на десятую часть:
# 1.1 — это на десять процентов громче. Меняется одной цифрой.
RECORDING_GAIN = 1.1

# Запись может идти часами, поэтому звук в памяти не копим: как только
# буфер набрал SPILL_SECONDS звука, поток-сохраняльщик дописывает его
# в черновой файл рядом с будущей записью. FLUSH_WAKE — как часто он
# просыпается и смотрит на буфер.
FLUSH_WAKE = 2.0
SPILL_SECONDS = 30.0
SPILL_FRAMES = int(SPILL_SECONDS * SAMPLE_RATE)

# Ниже этого уровня (доля от полной шкалы) подозрительно тихо
SILENCE_WARNING = 0.01

# Порог тишины: доля от полной шкалы, ниже которой голоса нет.
#
# Считается по среднеквадратичному уровню, а не по пику: одиночный
# щелчок мышью пиком выглядит громко, а средним уровнем — нет.
# Иначе любой посторонний звук продлевал бы запись.
SILENCE_FLOOR = 0.005

# Уровень, начиная с которого звук считается голосом.
#
# Выше порога тишины, и между ними мёртвая зона, где ничего не
# меняется. Без неё шум на границе дрожал бы: то входит в тишину,
# то из неё выходит, и таймер не досчитывает до конца.
#
# Замерено на этой машине: фон в комнате держится в пределах
# 0.0024…0.0066, а речь — от 0.03. Порог стоит в этом зазоре.
VOICE_LEVEL = 0.012

# Устройства, которые похожи на микрофон, но им не являются.
# Такие записи Windows держит рядом с настоящими микрофонами.
NOT_A_MICROPHONE = (
    "стерео микшер",  # пишет системный звук, а не голос
    "stereo mix",
    "лин. вход",  # линейный вход: для музыкального инструмента, не для голоса
    "line in",
    "переназначение звуковых",  # служебная запись драйвера, не устройство
    "первичный драйвер",  # сам драйвер, а не микрофон
    "primary sound capture",
)

# Порядок предпочтения интерфейсов. У одного физического микрофона
# свой номер в каждом интерфейсе, и в списке они выглядят одинаково,
# поэтому оставляем только лучший.
# WASAPI — современный путь в Windows, MME и DirectSound старее.
API_PREFERENCE = (
    ("wasapi", 0),
    ("directsound", 1),
    ("mme", 2),
    ("wdm", 3),
)

# Реестр Windows, где Windows сама ведёт список физических микрофонов.
# PortAudio показывает один микрофон несколько раз — по разу в каждом
# интерфейсе, а тут каждому устройству соответствует ровно одна запись
# с состоянием: активно, отключено, не вставлено.
_MMDEVICES_CAPTURE = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
)
_DEVICE_STATE_MASK = 0xF
_DEVICE_STATE_ACTIVE = 1
_PKEY_FRIENDLY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_PKEY_DESCRIPTION = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"

# Слова, которые не говорят, что это за устройство: «Microphone»,
# «Audio», «Input» — есть у всех. Для опознания годятся только
# собственные имена: Realtek, AMD, HK 1080P, Redmi Buds и т.п.
_STOPWORDS = frozenset(
    {
        "audio",
        "device",
        "dev",
        "input",
        "microphone",
        "mic",
        "sound",
        "high",
        "definition",
        "hd",
        "wave",
        "array",
        "internal",
        "external",
        "capture",
        "driver",
        "card",
        "usb",
        "acp",
        "rear",
        "front",
        "line",
        "surround",
        "center",
        "side",
        "bass",
        "sub",
        "device0",
    }
)

# То же самое по-русски. Windows с русским языком называет устройства
# «Микрофон (Realtek(R) Audio)», и слово «Микрофон» есть и в имени из
# PortAudio, и в описании из реестра — по нему узнавался любой микрофон,
# поэтому все устройства сливались в одно. Здесь основы, а не целые
# слова: «звук», «звуковая», «звуковых» — одно и то же.
_STOPSTEMS = (
    "аудио",
    "звук",
    "микрофон",
    "устройств",
    "запис",
    "вход",
)

# Индекс PortAudio -> ключ физического устройства (endpoint из реестра
# либо имя). Заполняется в candidate_devices() и служит default_device():
# индекс по умолчанию у Windows может быть дублем того же микрофона
# в другом интерфейсе.
_DEVICE_GROUP: dict[int, str] = {}


def _api_priority(api_name: str) -> int:
    """Насколько интерфейс предпочтителен для записи голоса."""
    lowered = api_name.lower()
    for token, priority in API_PREFERENCE:
        if token in lowered:
            return priority
    return 99


def _is_junk(name: str) -> bool:
    """Похоже на микрофон, но записывать голос оттуда бессмысленно."""
    lowered = name.lower()
    return any(bad in lowered for bad in NOT_A_MICROPHONE)


def windows_capture_devices() -> dict[str, str] | None:
    """Активные физические микрофоны Windows: endpoint-id -> описание.

    Это то, что Windows считает включёнными микрофонами, — по одной
    записи на устройство, без дублей из разных интерфейсов. None, если
    реестр недоступен (не Windows или нет прав).
    """
    if winreg is None:
        return None

    devices: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _MMDEVICES_CAPTURE) as root:
            count, _, _ = winreg.QueryInfoKey(root)
            for slot in range(count):
                name = winreg.EnumKey(root, slot)
                try:
                    with winreg.OpenKey(root, name) as endpoint:
                        state = winreg.QueryValueEx(endpoint, "DeviceState")[0]
                    if state & _DEVICE_STATE_MASK != _DEVICE_STATE_ACTIVE:
                        continue

                    with winreg.OpenKey(root, name + r"\Properties") as props:
                        friendly = str(
                            winreg.QueryValueEx(props, _PKEY_FRIENDLY)[0]
                        )
                        description = str(
                            winreg.QueryValueEx(props, _PKEY_DESCRIPTION)[0]
                        )
                except OSError:
                    continue

                description = f"{friendly} {description}".strip()
                if _is_junk(description):
                    continue
                devices[name] = description
    except OSError:
        return None

    return devices


def _distinctive(description: str) -> set[str]:
    """Слова, по которым устройство узнаётся в имени PortAudio."""
    lowered = "".join(c if c.isalnum() else " " for c in description.lower())
    return {
        word
        for word in lowered.split()
        if len(word) >= 3
        and word not in _STOPWORDS
        and not word.startswith(_STOPSTEMS)
    }


def _match_endpoint(name: str, devices: dict[str, str]) -> str | None:
    """К какому физическому микрофону относится имя из PortAudio."""
    lowered = "".join(c if c.isalnum() else " " for c in name.lower())
    for endpoint, description in devices.items():
        words = _distinctive(description)
        if not words:
            continue
        if any(word in lowered for word in words):
            return endpoint
    return None


def _group_candidates(
    devices: list[tuple[int, str, int]], physical: dict[str, str] | None
) -> list[tuple[int, str]]:
    """По одному варианту на физический микрофон.

    devices — записи PortAudio: (индекс, имя, приоритет интерфейса).
    Без реестра (physical=None) группирует по точному имени, как
    раньше: на машинах без Windows другой информации нет.
    """
    best: dict[str, tuple[int, int, int, str]] = {}
    fallback_by_name = physical is None
    matched = 0

    for index, name, priority in devices:
        if fallback_by_name:
            group = name.lower()
        else:
            group = _match_endpoint(name, physical)
            if group is None:
                continue  # реестр такого микрофона не знает — его сейчас нет
            matched += 1

        entry = (priority, -len(name), index, name)
        if group not in best or entry < best[group]:
            best[group] = entry

    if not fallback_by_name and matched == 0:
        # Реестр прочитался, но ни одно имя не сопоставилось — не
        # выбрасываем всё, а возвращаемся к старому правилу
        best.clear()
        for index, name, priority in devices:
            group = name.lower()
            entry = (priority, -len(name), index, name)
            if group not in best or entry < best[group]:
                best[group] = entry

    result = [(index, name) for _p, _len, index, name in best.values()]
    result.sort(key=lambda item: item[0])
    return result


def candidate_devices() -> list[tuple[int, str]]:
    """Устройства-кандидаты: без дублей и без мусора.

    Один и тот же микрофон Windows показывает несколько раз — по разу
    в каждом интерфейсе (MME, DirectSound, WASAPI, WDM-KS), и у дублей
    имена слегка разные. Кандидаты группируются по физическим
    микрофонам из реестра Windows: от каждого остаётся лучший вариант.
    Звук при этом ещё не проверен, это делает probe_devices().
    """
    apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}

    devices = [
        (index, device["name"], _api_priority(apis.get(device["hostapi"], "")))
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0 and not _is_junk(device["name"])
    ]

    physical = windows_capture_devices() or None
    result = _group_candidates(devices, physical)

    _DEVICE_GROUP.clear()
    for index, name in result:
        if physical is not None:
            group = _match_endpoint(name, physical) or name.lower()
        else:
            group = name.lower()
        _DEVICE_GROUP[index] = group

    return result


def _delivers_audio(device: int, seconds: float = 0.3) -> bool:
    """Открывает устройство ненадолго и ждёт хоть один блок звука.

    Вызывать только из главного потока: WASAPI из фонового потока
    не открывается, PortAudio возвращает ошибку -9999.

    Нужно, чтобы выкинуть из списка устройства, которые открываются,
    но ничего не передают: устаревшие WDM-KS микрофоны и отключённые
    гарнитуры ведут себя именно так.
    """
    received = []

    def callback(indata, frames_count, time_info, status):
        received.append(indata)

    try:
        with sd.InputStream(
            device=device,
            channels=CHANNELS,
            samplerate=SAMPLE_RATE,
            dtype=DTYPE,
            callback=callback,
        ):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not received:
                time.sleep(0.02)
    except Exception:  # noqa: BLE001 - сбой железа равносилен «не проверилось»
        return False

    return bool(received)


def test_device(index: int) -> bool:
    """Проверяет одно устройство. Вызывать из главного потока."""
    return _delivers_audio(index)


def default_device(devices: list[tuple[int, str]]) -> int | None:
    """Микрофон по умолчанию.

    Сначала уважается выбор Windows: если устройство по умолчанию
    есть в списке рабочих, берётся оно. Индекс по умолчанию может быть
    дублем того же микрофона в другом интерфейсе (например, MME) —
    тогда берём лучший вариант этого же микрофона из списка.
    Иначе — первое рабочее.
    """
    if not devices:
        return None

    try:
        system_default = sd.default.device[0]
    except Exception:  # noqa: BLE001 - нетPortAudio, значит нет и умолчания
        system_default = None

    if system_default is not None:
        for index, _name in devices:
            if index == system_default:
                return index

        target = _DEVICE_GROUP.get(system_default)
        if target is not None:
            for index, _name in devices:
                if _DEVICE_GROUP.get(index) == target:
                    return index

    return devices[0][0]


def _amplify(chunk: np.ndarray) -> np.ndarray:
    """Тот же звук, но громче — на RECORDING_GAIN.

    Считается во float: int16 при умножении переполнился бы молча,
    и громкий звук превратился бы в треск. После усиления отсчёты
    подрезаются по краям шкалы, чтобы перегрузка оставалась
    перегрузкой, а не меняла знак.
    """
    louder = chunk.astype(np.float32) * RECORDING_GAIN
    return np.clip(louder, -32768, 32767).astype(np.int16)


class Recorder:
    """Запись одного файла за раз.

    Порядок работы: start() → пауза/продолжение → stop().
    Остановить можно и не начав — тогда файл не создаётся.
    """

    def __init__(
        self,
        on_level: Callable[[float], None] | None = None,
        on_tick: Callable[[float], None] | None = None,
        on_finished: Callable[[Path, float, float], None] | None = None,
        on_empty: Callable[[], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.on_level = on_level
        self.on_tick = on_tick
        self.on_finished = on_finished
        self.on_empty = on_empty
        self.on_cancelled = on_cancelled

        self._thread: threading.Thread | None = None
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        # Черновой файл и то, сколько секунд в него уже сброшено
        self._spill_path: Path | None = None
        self._spilled = 0.0
        self._peak = 0.0
        self._level = 0.0
        self._path: Path | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._cancelled = threading.Event()
        self._started_at = 0.0

        # Слежение за тишиной. _silent_since — с какого момента звук
        # молчит (None — тишины нет, считать нечего).
        # _voice_seen — был ли голос хоть раз: запись без голоса
        # бесполезна, такой файл не сохраняется.
        self._rms = 0.0
        self._silent_since: float | None = None
        self._voice_seen = False

        # Счётчик записанного времени: _written хранит закрытые отрезки,
        # _segment_start — начало текущего. На паузе он равен None,
        # и таймер стоит.
        self._written = 0.0
        self._segment_start: float | None = 0.0

    @property
    def is_recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def elapsed(self) -> float:
        """Сколько секунд реально записано.

        Время на паузе не в счёт: звук не пишется, значит и таймер
        должен стоять. Иначе длинная пауза попадёт и в длительность
        записи, и в отчёт о размере файла.
        """
        if not self.is_recording:
            return self._written

        if self._segment_start is None:
            return self._written  # сейчас пауза

        return self._written + (time.monotonic() - self._segment_start)

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    @property
    def level(self) -> float:
        """Уровень последнего куска звука, доля от полной шкалы: 0..1.

        Читается из окна, поэтому значение простое число,
        а не результат вычисления прямо в этом месте.
        """
        return self._level

    @property
    def silence_seconds(self) -> float:
        """Сколько секунд подряд микрофон молчит.

        Ноль, если звук идёт. Пока запись на паузе, счётчик стоит:
        пауза — это не тишина, а решение самого человека.
        """
        since = self._silent_since
        if since is None:
            return 0.0

        return time.monotonic() - since

    def pause(self) -> None:
        """Останавливает таймер и запись, не закрывая файл."""
        # Пауза обрывает тишину: человек не молчал, он отошёл
        # от микрофона. Пока стоит пауза, тишина не считается.
        self._silent_since = None

        if not self.is_recording or self._paused.is_set():
            return

        # Закрываем текущий отрезок, чтобы пауза в него не попала
        self._written += time.monotonic() - self._segment_start
        self._segment_start = None
        self._paused.set()

    def resume(self) -> None:
        """Продолжает запись и снова пускает таймер."""
        # После паузы тишина считается заново
        self._reset_silence()

        if not self._paused.is_set():
            return

        self._segment_start = time.monotonic()
        self._paused.clear()

    def toggle_pause(self) -> None:
        if self._paused.is_set():
            self.resume()
        else:
            self.pause()

    def start(self, path: Path, device: int | None = None) -> None:
        """Начинает запись в файл path.

        Поток звука открывается здесь, в том потоке, откуда пришёл
        вызов. Это не перестраховка, а требование PortAudio: WASAPI
        не открывается из фонового потока и возвращает ошибку -9999.
        Сами звуковые данные идут отдельным потоком PortAudio, поэтому
        интерфейс от этого не блокируется.

        Ошибка открытия поднимается наружу, чтобы окно показало её сразу.
        """
        if self.is_recording:
            return

        self._path = path
        self._frames = []
        self._spill_path = path.with_suffix(".raw")
        self._spilled = 0.0
        self._peak = 0.0
        self._level = 0.0
        self._rms = 0.0
        self._voice_seen = False
        self._reset_silence()
        self._stop.clear()
        self._paused.clear()
        self._cancelled.clear()
        self._started_at = time.monotonic()
        self._written = 0.0
        self._segment_start = self._started_at

        self._stream = sd.InputStream(
            device=device,
            channels=CHANNELS,
            samplerate=SAMPLE_RATE,
            dtype=DTYPE,
            callback=self._audio_callback,
        )
        self._stream.start()

        self._thread = threading.Thread(
            target=self._wait_worker,
            daemon=True,
            name="recorder",
        )
        self._thread.start()

    def _audio_callback(self, indata, frames_count, time_info, status) -> None:
        """Колбэк PortAudio: приходит пачками из отдельного потока."""
        if self._stop.is_set() or self._paused.is_set():
            return

        mono = indata[:, 0] if indata.ndim > 1 else indata
        # Дальше идёт уже усиленный звук: индикатор уровня, автостоп
        # и сохранённый файл должны показывать одно и то же
        chunk = _amplify(mono)
        self._frames.append(chunk)

        # int16 максимум 32767 — приводим к шкале 0..1
        level = float(np.abs(chunk).max()) / 32768
        self._peak = max(self._peak, level)
        self._level = level
        if self.on_level:
            self.on_level(level)

        self._track_sound(chunk)

    def _track_sound(self, chunk: np.ndarray) -> None:
        """Следит за тишиной и запоминает, был ли голос.

        Молчание считается по среднеквадратичному уровню куска: он
        отвечает на вопрос «есть ли голос», а пик отвечает на вопрос
        «был ли хоть один громкий отсчёт». Для тишины нужен первый.

        Между порогами мёртвая зона: звук на границе не должен то
        входить в тишину, то из неё выходить.
        """
        rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float32))))) / 32768
        self._rms = rms

        if rms >= VOICE_LEVEL:
            # Голос есть: тишина закончилась
            self._silent_since = None
            self._voice_seen = True
        elif rms < SILENCE_FLOOR and self._silent_since is None:
            # Тишина началась — запоминаем момент, с которого её считать
            self._silent_since = time.monotonic()

    def _reset_silence(self) -> None:
        """Начинает отсчёт тишины заново.

        Вызывается при начале записи и при продолжении после паузы:
        после паузы человек начинает говорить заново, и тишину надо
        считать с этого момента.
        """
        self._silent_since = time.monotonic()

    def _close_stream(self) -> None:
        """Останавливает и закрывает поток звука.

        Вызывается из того же потока, что и start(): закрывать
        из фонового потока WASAPI тоже не даёт.
        """
        stream, self._stream = self._stream, None
        if stream is None:
            return

        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001, S110 - уборка, ошибка тут ничего не даст
            pass

    def _close_segment(self) -> None:
        """Закрывает текущий отрезок записи, перенося его в счётчик.

        Без этого после остановки отброшенный кусок времени теряется:
        таймер откатывается назад.
        """
        if self._segment_start is None:
            return

        self._written += time.monotonic() - self._segment_start
        self._segment_start = None

    def cancel(self) -> None:
        """Останавливает запись и не сохраняет файл."""
        if not self.is_recording:
            return

        # Порядок важен: сначала гасим колбэк (событие стопа), потом
        # закрываем звук и ждём, пока поток-сохраняльщик всё выбросит.
        self._cancelled.set()
        self._stop.set()
        self._paused.clear()
        self._close_stream()
        self._close_segment()
        self._join_workers()

    def stop(self) -> None:
        """Останавливает запись и сохраняет файл."""
        if not self.is_recording:
            return

        # Сначала гасим колбэк — иначе он может успеть добавить пачку
        # уже после того, как мы решили остановиться (см. _close_stream)
        self._stop.set()
        self._paused.clear()
        self._close_stream()
        self._close_segment()
        self._join_workers()

    def _join_workers(self) -> None:
        """Ждёт, пока фоновый поток закончит сохранять или выбрасывать."""
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _wait_worker(self) -> None:
        """Фоновый поток: ждёт стоп и по пути сбрасывает звук на диск.

        Звук он не трогает — поток уже закрыт в stop() или cancel().
        Пока стоп не наступил, просыпается каждые FLUSH_WAKE секунд и
        переносит накопленное в черновой файл: память держится в рамках
        SPILL_SECONDS звука при любой длине записи. Потолка по времени
        нет: как бы долго ни шла запись, стоп будет замечен сразу.
        """
        while not self._stop.wait(FLUSH_WAKE):
            self._flush_pending()

        # Остаток буфера — тоже в черновик, чтобы сохранять из одного места
        self._flush_pending(force=True)

        if self.on_tick:
            self.on_tick(time.monotonic() - self._started_at)

        # Отмена: всё, что накоплено, выбрасывается
        if self._cancelled.is_set():
            self._discard()
            if self.on_cancelled:
                self.on_cancelled()
            return

        # Голоса не было — файла не будет. Запись из одной тишины
        # бесполезна, а пустые файлы только мешают в списке.
        if not self._voice_seen:
            self._discard()
            if self.on_empty:
                self.on_empty()
            return

        path = self._path
        if path is None:
            return

        try:
            self._write(path)
        except OSError:
            if self.on_empty:
                self.on_empty()
            return

        if self.on_finished:
            self.on_finished(path, self._duration(), self._peak)

    def _flush_pending(self, *, force: bool = False) -> None:
        """Переносит накопленный звук в черновой файл, если это нужно.

        force забирает всё, что есть в буфере: так в конце вся запись
        оказывается в одном месте — в черновике, и сохранять можно
        оттуда напрямую.
        """
        if not force:
            buffered = sum(len(chunk) for chunk in self._frames)
            if buffered < SPILL_FRAMES:
                return

        pending, self._frames = self._frames, []
        if not pending:
            return

        spill = self._spill_path
        if spill is None:
            return

        spill.parent.mkdir(parents=True, exist_ok=True)
        with spill.open("ab") as raw:
            raw.write(np.concatenate(pending).tobytes())

        self._spilled += sum(len(chunk) for chunk in pending) / SAMPLE_RATE

    def _discard(self) -> None:
        """Выбрасывает накопленное: память и черновой файл."""
        self._frames = []
        self._spilled = 0.0

        spill = self._spill_path
        if spill is not None:
            with suppress(OSError):
                spill.unlink(missing_ok=True)

    def _write(self, path: Path) -> None:
        """Собирает wav из черновика.

        Звук сбрасывался на диск по частям ещё во время записи
        (_flush_pending), поэтому итоговый файл переписывается из
        черновика байт в байт — копить всё в памяти не нужно.

        Файл сохраняется целиком, вместе с тишиной по краям: обрезка
        отрезала часть записи, а лишние секунды тишины и так убирает
        фильтр речи при распознавании.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        spill = self._spill_path

        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            if spill is not None and spill.is_file():
                with spill.open("rb") as raw:
                    while True:
                        block = raw.read(1024 * 1024)
                        if not block:
                            break
                        wav.writeframes(block)

        # Черновик сослужил службу — он больше не нужен
        if spill is not None:
            with suppress(OSError):
                spill.unlink(missing_ok=True)

    def _duration(self) -> float:
        """Сколько секунд записано."""
        buffered = sum(len(chunk) for chunk in self._frames) / SAMPLE_RATE
        return self._spilled + buffered

    def is_too_quiet(self) -> bool:
        """Заметно тихо — похоже, микрофон не тот или выключен."""
        return self._peak < SILENCE_WARNING
