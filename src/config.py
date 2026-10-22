# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Настройки приложения.

Хранятся в JSON-файле в профиле пользователя Windows:
    %APPDATA%\\SmartVoiceRecorder\\settings.json

Скачанная модель лежит отдельно, в локальной папке приложения:
    %LOCALAPPDATA%\\SmartVoiceRecorder\\models

Путь не зависит от того, откуда запущено приложение: из ярлыка,
из bat-файла, из PyCharm — файлы те же самые.
"""

import json
import os
from pathlib import Path

APP_NAME = "SmartVoiceRecorder"

# Модель по умолчанию. medium — 1,5 ГБ, скачивается один раз
# и дальше лежит в локальной папке приложения
DEFAULT_MODEL = "medium"

# Модели, которые имеет смысл предлагать: тяжёлые, но на русском
# дают заметно лучший результат
AVAILABLE_MODELS = {
    "tiny": "tiny — 75 МБ, быстро, качество среднее",
    "base": "base — 145 МБ",
    "small": "small — 460 МБ",
    "medium": "medium — 1,5 ГБ, рекомендуется",
}

# Примерный размер модели на диске, МБ. Точный размер заранее неизвестен,
# а порядок величины нужен для сообщения при первой загрузке.
MODEL_SIZES_MB = {
    "tiny": 75,
    "base": 145,
    "small": 460,
    "medium": 1500,
}

# Сколько секунд тишины считать концом записи. Варианты для выбора.
AUTO_STOP_CHOICES = (3, 5, 10)

# Как сроки называются в списке. Коротко: длинные слова растягивали
# список шире поля, из которого он выпадает.
AUTO_STOP_LABELS = {
    3: "3 с",
    5: "5 с",
    10: "10 с",
}

# Сколько секунд тишины считать концом записи по умолчанию.
DEFAULT_AUTO_STOP_SECONDS = 5


def settings_dir() -> Path:
    """Папка настроек в профиле пользователя.

    Берётся из переменной окружения APPDATA, которую Windows всегда
    задаёт для зарегистрированного пользователя.
    """
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME

    # APPDATA нет — почти не бывает на Windows, но путь всё равно нужен
    return Path.home() / "AppData" / "Roaming" / APP_NAME


def settings_file() -> Path:
    return settings_dir() / "settings.json"


def local_app_dir() -> Path:
    """Локальная папка приложения в профиле пользователя.

    Отдельно от настроек: модель — это большие файлы, которым нечего
    делать в roaming-профиле (APPDATA), иначе они поедут на сервер
    вместе с настройками.
    """
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME

    # LOCALAPPDATA нет — почти не бывает на Windows, но путь всё равно нужен
    return Path.home() / "AppData" / "Local" / APP_NAME


def models_dir() -> Path:
    """Папка со скачанной моделью Whisper.

    Не кэш Hugging Face: кэш выглядит одноразовым, его могут удалить
    как мусор. Модель должна переживать очистку кэша и переклоне
    проекта, поэтому лежит в профиле пользователя под своим именем.
    """
    return local_app_dir() / "models"


def default_recordings_dir() -> Path:
    """Корень для записей — папка recordings рядом с программой."""
    return project_root() / "recordings"


def looks_nested(candidate: Path, root: Path) -> bool:
    """Похоже, что выбрали папку внутри корня записей.

    root — текущий корень записей, а не папка по умолчанию: если
    пользователь уже сменил корень, проверка идёт от него.

    Если так сделать, папки блоков лягут ещё на уровень глубже:
    recordings/идея/идея. Такое легко получить, выбрав в диалоге
    папку, которая сама является папкой блока.
    """
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return False

    return resolved.is_relative_to(root_resolved) and resolved != root_resolved


def project_root() -> Path:
    """Корень проекта — папка, где лежит pyproject.toml.

    Считается от расположения этого файла, а не от текущей рабочей
    папки: иначе запуск из другой директории ломает пути.
    """
    return Path(__file__).resolve().parent.parent


class Settings:
    """Настройки приложения. Читаются при старте, пишутся по кнопке «Сохранить»."""

    def __init__(self) -> None:
        self.recordings_dir: str = str(project_root() / "recordings")
        self.model: str = DEFAULT_MODEL
        self.microphone: int | None = None  # None — выбрать автоматически
        self.last_block: str = ""
        # Все три переключателя по умолчанию включены: обычный сценарий
        # «записал → нажал ■ → текст в буфере» не должен требовать
        # предварительной настройки, а метки времени пригодились тем,
        # кто правит записи в окне.
        self.auto_transcribe: bool = True
        self.auto_copy: bool = True
        self.timecodes: bool = True
        # Авто-стоп: срок тишины, после которого запись останавливается
        # сама. None — автостоп выключен. Молчание микрофона и пауза
        # самого человека — разные вещи, поэтому выключить автостоп
        # можно в любой момент.
        self.auto_stop_seconds: int | None = DEFAULT_AUTO_STOP_SECONDS
        self.load()

    def load(self) -> None:
        """Читает настройки из файла. Отсутствие или поломка файла не помеха."""
        path = settings_file()
        if not path.is_file():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Испорченный файл — начинаем с настроек по умолчанию
            return

        if not isinstance(data, dict):
            return

        self.recordings_dir = str(data.get("recordings_dir") or self.recordings_dir)
        self.model = str(data.get("model") or self.model)

        mic = data.get("microphone")
        self.microphone = mic if isinstance(mic, int) else None

        self.last_block = str(data.get("last_block") or "")

        self.auto_transcribe = bool(data.get("auto_transcribe", True))
        self.auto_copy = bool(data.get("auto_copy", True))
        self.timecodes = bool(data.get("timecodes", True))

        self.auto_stop_seconds = self._read_auto_stop(data)

    def _read_auto_stop(self, data: dict) -> int | None:
        """Срок автостопа из настроек. None — автостоп выключен.

        Раньше хранились две величины: галочка «включено» и срок.
        Старый файл читаем как есть, иначе выключенный автостоп
        включился бы сам при первом же запуске.
        """
        # Прежний формат: галочка и срок лежали отдельно
        if "auto_stop" in data and not data["auto_stop"]:
            return None

        seconds = data.get("auto_stop_seconds", DEFAULT_AUTO_STOP_SECONDS)
        if seconds is None:
            return None

        # Список в окне короткий и постоянный. Значение вне списка
        # превратилось бы в лишний пункт, которого там быть не должно,
        # поэтому такой срок заменяется на обычный
        if seconds in AUTO_STOP_CHOICES:
            return seconds
        return DEFAULT_AUTO_STOP_SECONDS

    def save(self) -> None:
        """Пишет настройки в файл. Папка создаётся при первом сохранении."""
        path = settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "recordings_dir": self.recordings_dir,
            "model": self.model,
            "microphone": self.microphone,
            "last_block": self.last_block,
            "auto_transcribe": self.auto_transcribe,
            "auto_copy": self.auto_copy,
            "timecodes": self.timecodes,
            "auto_stop_seconds": self.auto_stop_seconds,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def recordings_path(self) -> Path:
        """Папка с записями, созданная при необходимости."""
        path = Path(self.recordings_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def model_label(self) -> str:
        return AVAILABLE_MODELS.get(self.model, self.model)
