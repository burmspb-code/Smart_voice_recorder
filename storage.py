# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Хранение записей: где лежат файлы и как их находить.

Структура папок:
    <папка записей>/<название блока>/ГГГГ-ММ-ДД_ЧЧ-ММ-СС.wav
    <папка записей>/<название блока>/ГГГГ-ММ-ДД_ЧЧ-ММ-СС.txt

Название блока — это имя папки, а не часть имени файла.
Раньше блок был частью имени, и при большом числе записей
в одной папке было не разобрать, какая к чему относится.
"""

import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NO_BLOCK = "без названия"

# Символы, которые Windows не пускает в имя файла
INVALID_CHARS = '<>:"/\\|?*'

# Имена, зарезервированные Windows: папку с таким именем создать нельзя,
# даже с расширением. Проверяются без учёта регистра и без хвоста после точки.
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

# Длительность, прочитанная из заголовка файла: путь -> (mtime, размер, сек).
# Заголовок wav читается открытием файла, а scan() зовётся на каждое
# обновление таблицы и на каждую букву в названии блока. Без кэша это
# сотни открытий файлов там, где содержимое не менялось.
_DURATION_CACHE: dict[Path, tuple[float, int, float]] = {}


def format_duration(seconds: float, pad: bool = False) -> str:
    """Длительность в виде минут и секунд: 0:07, 12:34.

    pad=True — минуты с ведущим нулём (00:07): так время показывает
    таймер записи.
    """
    total = int(seconds)
    minutes = total // 60
    if pad:
        return f"{minutes:02d}:{total % 60:02d}"
    return f"{minutes}:{total % 60:02d}"


def _duration_from_file(wav: Path) -> float:
    """Читает длительность из заголовка wav. Ошибка — ноль секунд."""
    try:
        with wave.open(str(wav), "rb") as handle:
            rate = handle.getframerate()
            if not rate:
                return 0.0
            return handle.getnframes() / rate
    except (OSError, wave.Error, EOFError):
        return 0.0


def _cached_duration(wav: Path, modified: float, size: int) -> float:
    """Длительность с кэшем по времени изменения и размеру файла.

    Ключ — путь, а признак свежести — mtime и размер: перезаписанный
    файл с тем же именем перечитается, а неизменившийся не откроется.
    """
    known = _DURATION_CACHE.get(wav)
    if known is not None and known[0] == modified and known[1] == size:
        return known[2]

    duration = _duration_from_file(wav)
    _DURATION_CACHE[wav] = (modified, size, duration)
    return duration


def read_duration(wav: Path) -> float:
    """Длительность записи в секундах, из заголовка wav.

    Сам звук не читается: wave открывает файл и берёт число кадров
    из заголовка. Неразобранный или повреждённый файл даёт ноль,
    а не ошибку — список записей должен собираться всегда.
    """
    try:
        stat = wav.stat()
    except OSError:
        return 0.0
    return _cached_duration(wav, stat.st_mtime, stat.st_size)


def sanitize(name: str) -> str:
    """Готовит название блока к тому, чтобы стать именем папки."""
    cleaned = "".join("_" if ch in INVALID_CHARS else ch for ch in name.strip())
    cleaned = cleaned.strip(". ")[:60]

    # «CON», «NUL», «COM1» и подобные Windows не даёт использовать
    # как имя папки: подчёркивание в начале делает имя обычным
    if cleaned.upper() in RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


@dataclass
class Recording:
    """Одна запись на диске."""

    wav: Path
    block: str
    created: datetime | None
    has_text: bool
    size_mb: float
    duration: float = 0.0
    modified: float = 0.0

    @property
    def txt(self) -> Path:
        return self.wav.with_suffix(".txt")

    @property
    def name(self) -> str:
        return self.wav.name

    @property
    def length(self) -> str:
        """Длительность для таблицы. Прочерк, если неизвестна."""
        return format_duration(self.duration) if self.duration else "—"

    @property
    def status(self) -> str:
        """Имя текстового файла, если распознавание состоялось.

        Само имя полезнее слова «распознано»: по нему видно,
        какой файл соответствует какой записи, и его можно открыть.
        """
        return self.txt.name if self.has_text else "Нет текста"

    @property
    def block_folder(self) -> str:
        """Имя папки, в которой лежит запись.

        У записи без блока папки нет, но файлы всё равно лежат в
        папке с названием NO_BLOCK — для сравнения нужен именно
        этот вариант, иначе записи без блока не найдутся.
        """
        return self.block or NO_BLOCK


def block_dir(root: Path, block: str) -> Path:
    """Папка блока. Записи без названия складываются отдельно."""
    return root / (sanitize(block) or NO_BLOCK)


def parse_when(stem: str) -> datetime | None:
    """Достаёт дату и время из имени файла.

    Хвост с номером отбрасывается: 2026-10-09_18-30-27_2 → 2026-10-09_18-30-27
    """
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y%m%d_%H-%M-%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(stem, fmt)
        except ValueError:
            pass

    head, _, tail = stem.rpartition("_")
    if head and tail.isdigit():
        return parse_when(head)
    return None


def unique_path(root: Path, block: str) -> Path:
    """Путь для новой записи: папка создаётся, имя не повторяется.

    Хвост с номером нужен для случая, когда в одну секунду
    начинаются две записи подряд.
    """
    folder = block_dir(root, block)
    folder.mkdir(parents=True, exist_ok=True)

    # Время берётся одно на все попытки: иначе за время перебора
    # успевала смениться секунда и суффикс с номером не появлялся
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    for n in range(1, 100):
        name = f"{stamp}.wav" if n == 1 else f"{stamp}_{n}.wav"
        if not (folder / name).exists():
            return folder / name

    raise OSError("Слишком много файлов с таким именем")


def block_of(root: Path, wav: Path) -> str:
    """Название блока — имя папки, в которой лежит запись."""
    if wav.parent == root:
        return ""
    return wav.parent.name


def scan(root: Path, folder: str | None = None) -> list[Recording]:
    """Все записи, от свежих к старым.

    Порядок по времени изменения файла: он надёжнее имени,
    если файл переименовали или перенесли.

    folder — папка блока (для записей без названия это NO_BLOCK).
    Фильтр применяется до чтения заголовков: таблица показывает один
    блок, и открывать wav из всех остальных незачем.
    """
    if not root.is_dir():
        return []

    found = []
    for wav in root.rglob("*.wav"):
        try:
            stat = wav.stat()
        except OSError:
            continue

        block = block_of(root, wav)
        if folder is not None and (block or NO_BLOCK) != folder:
            continue

        found.append(
            Recording(
                wav=wav,
                block=block,
                created=parse_when(wav.stem),
                has_text=wav.with_suffix(".txt").exists(),
                size_mb=stat.st_size / 1024 / 1024,
                duration=_cached_duration(wav, stat.st_mtime, stat.st_size),
                modified=stat.st_mtime,
            )
        )

    # Время берём из уже сделанного stat: повторный вызов без защиты
    # мог бы упасть, если файл исчез между обходом и сортировкой
    return sorted(found, key=lambda r: r.modified, reverse=True)


def pending(root: Path, block: str | None = None) -> list[Recording]:
    """Записи без готовой расшифровки, от свежих к старым.

    block — папка блока. Пустое значение означает «все блоки»,
    нужно для случая, когда распознавание ограничено одним блоком,
    а таблица показывает только его.
    """
    return [r for r in scan(root, folder=block) if not r.has_text]


def blocks(root: Path) -> list[str]:
    """Названия всех блоков, которые уже встречались в записях.

    Нужны для подсказок в интерфейсе: пользователь вводит название
    блока руками, а тут можно предложить то, что уже использовал.
    """
    names = []
    for recording in scan(root):
        if recording.block and recording.block not in names:
            names.append(recording.block)
    return names
