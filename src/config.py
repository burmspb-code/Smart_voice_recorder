# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Настройки приложения.

Хранятся в JSON-файле в профиле пользователя Windows:
    %APPDATA%\\SmartVoiceRecorder\\settings.json

Путь к настройкам не зависит от того, откуда запущено приложение:
из ярлыка, из bat-файла, из PyCharm — файл тот же самый.
"""

import json
import os
from pathlib import Path

APP_NAME = "SmartVoiceRecorder"

# Модель по умолчанию. medium — 1,5 ГБ, скачивается один раз
# и дальше лежит в кэше Hugging Face
DEFAULT_MODEL = "medium"

# Модели, которые имеет смысл предлагать: тяжёлые, но на русском
# дают заметно лучший результат
AVAILABLE_MODELS = {
    "tiny": "tiny — 75 МБ, быстро, качество среднее",
    "base": "base — 145 МБ",
    "small": "small — 460 МБ",
    "medium": "medium — 1,5 ГБ, рекомендуется",
}


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


def default_recordings_dir() -> Path:
    """Корень для записей — папка recordings рядом с программой."""
    return project_root() / "recordings"


def looks_nested(candidate: Path) -> bool:
    """Похоже, что выбрали папку внутри корня записей.

    Если так сделать, папки блоков лягут ещё на уровень глубже:
    recordings/идея/идея. Такое легко получить, выбрав в диалоге
    папку, которая сама является папкой блока.
    """
    try:
        inside = candidate.resolve().is_relative_to(default_recordings_dir().resolve())
    except OSError, ValueError:
        return False

    return inside and candidate.resolve() != default_recordings_dir().resolve()


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
        self.load()

    def load(self) -> None:
        """Читает настройки из файла. Отсутствие или поломка файла не помеха."""
        path = settings_file()
        if not path.is_file():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            # Испорченный файл — начинаем с настроек по умолчанию
            return

        if not isinstance(data, dict):
            return

        self.recordings_dir = str(data.get("recordings_dir") or self.recordings_dir)
        self.model = str(data.get("model") or self.model)

        mic = data.get("microphone")
        self.microphone = mic if isinstance(mic, int) else None

        self.last_block = str(data.get("last_block") or "")

    def save(self) -> None:
        """Пишет настройки в файл. Папка создаётся при первом сохранении."""
        path = settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "recordings_dir": self.recordings_dir,
            "model": self.model,
            "microphone": self.microphone,
            "last_block": self.last_block,
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
