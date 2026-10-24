# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Главное окно приложения.

Окно не ждёт завершения записи и не ждёт конца распознавания:
и то и другое идёт в отдельных потоках, а сюда приходят только
сообщения о том, что изменилось. Поэтому интерфейс не замирает,
даже пока идёт расшифровка на несколько минут.

Раскладка горизонтальная: настройки сверху, кнопки записи в
центре, записи и выезжающая панель с текстом снизу.
"""

import contextlib
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import (
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QTextBlock,
    QTextBlockFormat,
    QTextCursor,
    QTextFormat,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import storage
from src import config, model_store
from src.config import (
    AUTO_STOP_CHOICES,
    AUTO_STOP_LABELS,
    AVAILABLE_MODELS,
    Settings,
)
from src.core.icons import app_icon, icon
from src.core.styles import (
    LIVE,
    MUTED,
    apply_locale,
    apply_property,
    apply_theme,
)
from src.model_store import ModelStore
from src.recorder import Recorder, candidate_devices, default_device, test_device
from src.transcriber import Transcriber
from src.widgets import (
    IDLE,
    PAUSED,
    RECORDING,
    PulseButton,
    RecordIndicator,
    format_duration,
)

# Обновление индикатора и таймера. Быстрее 30 раз в секунду
# незаметно глазом, но окно перестаёт отвечать.
UI_TICK_MS = 33

# За сколько секунд до конца срока предупредить, что запись сейчас
# остановится. Без предупреждения автостоп выглядит как поломка.
AUTO_STOP_WARN = 2.0

# Значок на клавишах записи. Кнопки по 44 пикселя, и 30 — предел:
# значок с полями 6 px занимает их целиком, а на 32 кнопки уже
# подрастают до 46
BUTTON_ICON = 30

# Значок перед полем верхней строки. Поля в оформлении по 34 пикселя,
# и значок занимает эту высоту целиком; на 36 строка уже подрастает
ROW_ICON = 34


class Signals(QObject):
    """Мост из рабочих потоков в интерфейс.

    Qt требует трогать элементы интерфейса только из главного потока,
    поэтому всё, что приходит из записи и распознавания, проходит
    через эти сигналы.
    """

    recorded = pyqtSignal(str, float, float)  # путь, длительность, пик
    nothing_recorded = pyqtSignal()
    record_failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    model_ready = pyqtSignal()
    model_failed = pyqtSignal(str)

    file_started = pyqtSignal(str)
    file_done = pyqtSignal(str, int, str)  # путь, строк, текст
    all_done = pyqtSignal(int, int, bool)  # сделано, всего, отменено
    transcribe_failed = pyqtSignal(str)


# Столбцы таблицы записей
# Виды сообщений внизу окна. Цвет выбирается по смыслу:
# успех, предупреждение и ошибка не должны выглядеть одинаково.
# У BUSY свой цвет: пока идёт запись или распознавание, строка должна
# притягивать взгляд — иначе кажется, что приложение просто зависло
INFO = "info"
BUSY = "busy"
SUCCESS = "success"
WARNING = "warning"
ERROR = "error"

COLUMN_BLOCK = 0
COLUMN_FILE = 1
COLUMN_STATUS = 2
COLUMN_LENGTH = 3
COLUMN_SIZE = 4

# Разделитель между кусками в окне: пустая строка, помеченная в
# оформлении. Ни одного символа в тексте — в буфер обмена и в файл
# такая граница не попадает, там остаётся только речь.
SEPARATOR_MARK = "block-separator"
SEPARATOR_BG = "#3a3648"

COLUMN_NAMES = [
    "Папка",
    "Файл",
    "Текст",
    "Длительность, мин:сек",
    # Единицы в заголовке не дублируем: «МБ» уже стоит в самом значении,
    # а лишние буквы отбирают место у имени файла
    "Размер",
]


@dataclass
class TextBlock:
    """Кусок текста в окне и файл, из которого он взят.

    Границы куска держит метка в оформлении пустой строки, а не
    текст разделителя: текстом искать нельзя — пользователь его
    правит, и куски склеиваются. Метка в оформлении переживает
    правки и при этом не попадает в текст: в буфер обмена и в
    файл `.txt` уходит чистая речь, без полосок и дат.
    """

    wav: Path


class MainWindow(QMainWindow):
    """Окно приложения."""

    def __init__(self) -> None:
        """Создаёт главное окно: настройки, запись, модель, интерфейс.

        Окно не ждёт конца записи и распознавания — они идут в
        отдельных потоках, а сюда приходят только сообщения о
        изменении. Интерфейс не замирает даже при долгом распознавании.
        """
        super().__init__()

        # Иконка ставится здесь, а не в run(): окно может быть создано
        # и без запуска приложения, и иконка нужна в обоих случаях
        icon = app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        # Окно открывается ровно под содержимое: вторая зона свёрнута
        self.resize(920, 380)

        self.settings = Settings()
        self.recordings = self.settings.recordings_path()
        self.model_store = ModelStore()
        self.transcriber = Transcriber(self.model_store)
        self.signals = Signals()

        # Наполненное окно: куски текста и файлы, из которых они взяты
        self.blocks: list[TextBlock] = []
        # Правки пользователя, ещё не записанные на диск
        self.dirty = False
        self._appending = False

        # Сколько символов ушло в буфер по ходу текущего распознавания:
        # показываем это в итоговой строке состояния
        self._copied_chars = 0

        # Сведения о модели: нужны, чтобы первый запуск был понятным
        self._model_info: model_store.ModelInfo | None = None
        self._model_needs_download = False
        self._download_notice_shown = False
        self._model_moved_from: Path | None = None
        self._moved_notice_shown = False
        # Пока модель грузится, строка статуса не перебивается
        self._model_loading = False
        # Состояние таймера прогресса скачивания: метки времени, байты,
        # скорость, а также момент, когда скачивание сменилось загрузкой
        self._model_progress_timer: QTimer | None = None
        self._model_progress_at = 0.0
        self._model_progress_done = 0
        self._model_progress_speed = 0.0
        self._model_loading_since: float | None = None

        # Остановка записи по тишине: предупреждение показано,
        # а сама остановка уже случилась — оба флага живут между
        # тиками, поэтому их надо помнить
        self._auto_stop_warned = False
        self._auto_stopped = False

        self._build_ui()
        self._connect()

        self._fill_devices()
        self._fill_models()
        self._refresh_list()
        self._start_model()
        self._start_device_probe()
        self._tick_timer()

    # ---------------------------------------------------------------- сборка

    def _build_ui(self) -> None:
        """Собирает всё окно сверху вниз: строка настроек, кнопки,
        индикатор, список записей и текст, строка состояния внизу.

        Пружина держит содержимое сверху, чтобы во время анимации зоны
        ничего не съезжало.
        """
        self.setWindowTitle("Голосовые заметки")
        self.resize(920, 680)  # Чуть расширим для удобства

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addLayout(self._build_top_row())
        layout.addWidget(self._build_auto_row())
        layout.addLayout(self._build_control_row())
        layout.addWidget(self._build_level())
        layout.addWidget(self._build_details_zone())

        # Пружина держит содержимое сверху, а строку состояния внизу.
        # Без неё лишняя высота достаётся строкам выше зоны, и пока
        # окно догоняет анимацию, всё содержимое съезжает вниз
        layout.addStretch(1)

        self.status = QLabel("Готово")
        # Свойство kind задаёт цвет по смыслу и перебивает приглушённый
        # текст, поэтому muted здесь не нужен
        self.status.setProperty("kind", INFO)
        # Без этого подпись впитывает всё свободное место окна
        # и выглядит вторым пустым окном внизу экрана
        self.status.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.status)

    def _say(self, text: str, kind: str = INFO) -> None:
        """Сообщение внизу окна с цветом по смыслу.

        Пока модель грузится, сообщение о загрузке не перебивается
        обычными сообщениями: иначе оно мелькнёт и пропадёт, а
        пользователь так и не поймёт, чего ждёт приложение. Ошибкам
        и предупреждениям дорога открыта — они важнее.
        """
        if self._model_loading and kind not in (WARNING, ERROR):
            return

        self._set_status(text, kind)

    def _set_status(self, text: str, kind: str = INFO) -> None:
        """Ставит текст статуса напрямую, минуя защиту от перебивания.

        Без unpolish и polish Qt не перечитывает правило для нового
        свойства, и подпись осталась бы прежнего цвета.
        """
        self.status.setText(text)
        apply_property(self.status, "kind", kind)

    def _build_details_zone(self) -> QWidget:
        """Вторая зона: список записей и текст распознавания.

        Зона свёрнута при запуске и поднимается по кнопке в правом
        верхнем углу. Автоматически она не открывается: распознавание
        может идти в фоне, и окно не должно дёргаться само собой.
        Постоянная часть окна — только то, что нужно для записи:
        блок, микрофон, кнопки и индикатор.
        """
        zone = QWidget()
        zone.setProperty("zone", True)

        box = QVBoxLayout(zone)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        box.addLayout(self._build_list_row())

        # Пружина прижимает текст к низу окна, когда панель высокая
        box.addStretch(1)

        box.addWidget(self._build_text_panel())

        self.details_zone = zone
        zone.setMaximumHeight(0)

        # Высота окна без зоны. Считается до старта анимации, а на
        # каждом её кадре к ней прибавляется текущая высота зоны
        self._zone_base = 0

        # Зона поднимается и опускается анимацией высоты:
        # анимировать геометрию внутри слоя неудобно, а
        # maximumHeight работает устойчиво при любой длине текста
        self._zone_animation = QPropertyAnimation(zone, b"maximumHeight")
        self._zone_animation.setDuration(180)
        # Окно ужимается вместе с зоной, кадр в кадр. Если ужимать его
        # только после анимации, освободившееся место на миг раздвигает
        # строки выше зоны, и всё содержимое проваливается вниз
        self._zone_animation.valueChanged.connect(self._follow_zone)
        # Последний штрих: прокрутка таблицы и точная высота
        self._zone_animation.finished.connect(self._fit_window)

        return zone

    def _build_top_row(self) -> QHBoxLayout:
        """Название блока, микрофон, модель, папка записей."""
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(self._icon_label("block", "Папка для записи"))

        self.block_input = QLineEdit()
        self.block_input.setPlaceholderText(
            "название записи — например: идеи, планёрка"
        )
        self.block_input.setText(self.settings.last_block)
        self.block_input.setClearButtonEnabled(True)

        # Таблица перечитывается не на каждую букву, а когда название
        # перестало меняться: иначе пришлось бы открывать все wav
        self._block_timer = QTimer(self)
        self._block_timer.setSingleShot(True)
        self._block_timer.setInterval(400)
        self._block_timer.timeout.connect(lambda: self._refresh_list(announce=False))

        # Подсказки из прошлых названий: то, что уже использовалось
        known = storage.blocks(self.recordings)
        if known:
            completer = QCompleter(known)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.block_input.setCompleter(completer)
        row.addWidget(self.block_input, 3)
        self.block_input.returnPressed.connect(self._on_block_entered)
        self.block_input.textChanged.connect(
            lambda _: (self._update_block_hint(), self._on_block_typed())
        )
        self._update_block_hint()

        row.addWidget(self._icon_label("microphone", "Микрофон"))

        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(230)
        self.device_box.setToolTip("Микрофон для записи")
        row.addWidget(self.device_box, 2)

        row.addWidget(self._icon_label("models", "Модель распознавания"))

        self.model_box = QComboBox()
        for name, label in AVAILABLE_MODELS.items():
            self.model_box.addItem(label, name)
        self.model_box.setToolTip(
            "Модель распознавания.\n"
            "Смена подхватывается при следующем запуске."
        )
        row.addWidget(self.model_box, 2)

        return row

    def _build_auto_row(self) -> QWidget:
        """Переключатели автоматической расшифровки.

        С ними обычный сценарий короткий: записал, нажал ■, текст
        сам расшифровался и лёг в буфер обмена — вставлять можно
        сразу, не открывая нижнюю зону.
        """
        holder = QWidget()
        box = QHBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        # Галочки ставятся до подключения слотов: иначе начальная
        # расстановка сохранилась бы в файл настроек и затёрла бы
        # значения, которые там уже лежат
        self.auto_transcribe_box = QCheckBox("Распознавать после остановки")
        self.auto_transcribe_box.setChecked(self.settings.auto_transcribe)
        self.auto_transcribe_box.setToolTip(
            "Автораспознавание после нажатия клавиши Stop"
        )
        box.addWidget(self.auto_transcribe_box)

        self.auto_copy_box = QCheckBox("Копировать в буфер")
        self.auto_copy_box.setChecked(self.settings.auto_copy)
        self.auto_copy_box.setToolTip("Скопировать текст последней записи")
        box.addWidget(self.auto_copy_box)

        self.timecodes_box = QCheckBox("Таймкоды")
        self.timecodes_box.setChecked(self.settings.timecodes)
        self.timecodes_box.setToolTip("Добавлять в текст распознавания")
        box.addWidget(self.timecodes_box)

        self.play_sound_box = QCheckBox("Звук после распознавания")
        self.play_sound_box.setChecked(getattr(self.settings, "play_sound", True))
        self.play_sound_box.setToolTip("Короткий сигнал по окончании расшифровки")
        box.addWidget(self.play_sound_box)

        # АвтоСтоп: микрофон замолчал — значит, человек закончил
        # и забыл нажать ■. Слово написано в самом поле, а сроки
        # раскрываются по нажатию: поэтому это кнопка с меню, а не
        # поле выбора со значком рядом.
        self.auto_stop_button = QPushButton("АвтоСтоп")
        self.auto_stop_button.setObjectName("autoStop")
        self.auto_stop_button.setToolTip(
            "Остановить запись, если микрофон замолчал.\n"
            "«нет» — запись идёт, пока не нажмёте ■."
        )

        self.auto_stop_menu = QMenu(self)
        for seconds in (None, *AUTO_STOP_CHOICES):
            title = "нет" if seconds is None else AUTO_STOP_LABELS[seconds]
            action = self.auto_stop_menu.addAction(title)
            action.setCheckable(True)
            action.setData(seconds)
        self.auto_stop_button.setMenu(self.auto_stop_menu)
        self._mark_auto_stop()
        box.addWidget(self.auto_stop_button)

        # Слоты подключаем последними: до этого момента расстановка
        # галочек ещё не должна была ничего записывать в настройки
        self.auto_transcribe_box.toggled.connect(self._on_auto_transcribe)
        self.auto_copy_box.toggled.connect(self._on_auto_copy)
        self.timecodes_box.toggled.connect(self._on_timecodes)
        self.play_sound_box.toggled.connect(self._on_play_sound)
        self.auto_stop_menu.aboutToShow.connect(self._highlight_auto_stop)
        self.auto_stop_menu.triggered.connect(self._on_auto_stop)

        box.addStretch(1)
        return holder

    @staticmethod
    def _icon_label(name: str, tip: str) -> QLabel:
        """Значок вместо текстовой надписи перед полем.

        Подпись занимала бы место и дублировала то, что уже
        написано в самом поле. Смысл остаётся в подсказке.
        """
        label = QLabel()
        label.setPixmap(icon(name, ROW_ICON).pixmap(ROW_ICON, ROW_ICON))
        label.setToolTip(tip)
        label.setFixedWidth(ROW_ICON)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _current_block(self) -> str:
        """Папка текущего блока — та, куда едут записи."""
        return storage.sanitize(self.block_input.text().strip()) or storage.NO_BLOCK

    def _block_target(self) -> Path:
        """Папка, куда попадёт следующая запись."""
        return storage.block_dir(self.recordings, self.block_input.text())

    def _update_block_hint(self) -> None:
        """Подсказка с полным путём: папку блока легко принять
        за корень, а найти потом записи невозможно."""
        self.block_input.setToolTip(f"Записи попадут сюда:\n{self._block_target()}")

    def _on_block_typed(self) -> None:
        """Перечитывает таблицу, когда название блока меняется.

        С задержкой: scan() открывает каждый wav ради длительности,
        а набор названия вызывает это на каждую букву.
        """
        self._block_timer.start()

    def _on_block_entered(self) -> None:
        """Enter в поле папки: показываем, куда пойдёт запись.

        Фокус с поля уводим: после Enter название принято, и
        мигающий курсор в поле только мешает — он читается как
        «здесь ещё что-то печатать».
        """
        self._block_timer.stop()
        self.block_input.clearFocus()

        target = self._block_target()
        self._update_block_hint()
        self._refresh_list()

        if self.block_input.text().strip():
            self._say(f"Записи будут в папку {target}")
        else:
            self._say(f"Без названия папки — записи в {target}", WARNING)

    def _build_control_row(self) -> QHBoxLayout:
        """Кнопки записи.

        На первой клавише три состояния: начать, пауза и продолжить.
        Отдельная клавиша «стоп» стоит там, где раньше была пауза.
        """
        row = QHBoxLayout()
        row.setSpacing(8)

        self.record_button = QPushButton()
        self.record_button.setMinimumHeight(44)
        self.record_button.setMinimumWidth(110)
        self.record_button.setIcon(icon("play", BUTTON_ICON))
        self.record_button.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
        self.record_button.setToolTip("Начать запись")
        row.addWidget(self.record_button)

        self.stop_button = QPushButton()
        self.stop_button.setMinimumHeight(44)
        self.stop_button.setMinimumWidth(110)
        self.stop_button.setIcon(icon("stop", BUTTON_ICON))
        self.stop_button.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
        self.stop_button.setToolTip("Остановить запись и сохранить файл")
        self.stop_button.setEnabled(False)
        row.addWidget(self.stop_button)

        self.cancel_button = QPushButton()
        self.cancel_button.setMinimumHeight(44)
        self.cancel_button.setMinimumWidth(110)
        self.cancel_button.setIcon(icon("cancel", BUTTON_ICON))
        self.cancel_button.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Отменить последнюю запись")
        row.addWidget(self.cancel_button)

        row.addStretch(1)

        self.zone_button = QPushButton()
        self.zone_button.setMinimumHeight(44)
        self.zone_button.setMinimumWidth(110)
        self.zone_button.setIcon(icon("open", BUTTON_ICON))
        self.zone_button.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
        self.zone_button.setCheckable(True)
        self.zone_button.setToolTip(
            "Показать или скрыть список записей и распознанный текст"
        )
        row.addWidget(self.zone_button)

        return row

    def _build_level(self) -> QWidget:
        """Индикатор записи: точка, состояние, таймер и уровень."""
        self.indicator = RecordIndicator()
        return self.indicator

    def _build_list_row(self) -> QHBoxLayout:
        """Таблица записей и кнопки работы с ними.

        Кнопки стоят столбиком: в ряд они растягивались на всю ширину
        зоны и отжимали таблицу, хотя нужны редко. Распознавание
        живёт здесь же — оно работает с записями из таблицы, а не
        с самим микрофоном.

        Подписи «Записи» нет: таблица и так видна, а надпись
        занимала бы место и выглядела лишней.
        """
        row = QHBoxLayout()
        row.setSpacing(8)

        self.record_table = QTableWidget(0, len(COLUMN_NAMES))
        self.record_table.setHorizontalHeaderLabels(COLUMN_NAMES)
        self.record_table.verticalHeader().setVisible(False)
        self.record_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.record_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.record_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.record_table.verticalHeader().setDefaultSectionSize(26)
        self.record_table.setAlternatingRowColors(False)

        # Шапка живёт отдельно от строк и при прокрутке не уезжает —
        # так и задумано в QTableWidget. Высоту не задаём: с отступами
        # из стилей ей нужна больше, чем считает Qt по умолчанию,
        # и при фиксированной высоте заголовки обрезаются
        header = self.record_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        # Высоту не задаём жёстко: с отступами из стилей Qt считает
        # её сам иначе заголовки обрезаются. Нижняя граница нужна
        # только чтобы шапка не схлопнулась совсем.
        header.setMinimumHeight(40)
        # По умолчанию Qt прижимает текст к верху, а у нас он
        # должен стоять посередине полосы
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )

        # Тянется только «Файл»: остальные колонки берут ровно столько,
        # сколько нужно под своё содержимое. Иначе имя файла
        # делится с «Папкой» и обрезается на самом важном месте
        for fixed in (COLUMN_BLOCK, COLUMN_STATUS, COLUMN_LENGTH, COLUMN_SIZE):
            header.setSectionResizeMode(fixed, QHeaderView.ResizeMode.ResizeToContents)

        self.record_table.cellDoubleClicked.connect(self._open_row_file)

        self.record_table.setMaximumHeight(170)
        row.addWidget(self.record_table, 1)

        folder = QVBoxLayout()
        folder.setSpacing(6)

        self.transcribe_button = PulseButton()
        self.transcribe_button.setText("Распознать")
        self.transcribe_button.setMinimumHeight(40)
        self.transcribe_button.setMinimumWidth(150)
        self.transcribe_button.setToolTip(
            "Расшифровать последнюю запись без готового текста"
        )
        folder.addWidget(self.transcribe_button)

        self.choose_folder_button = QPushButton("Сменить папку…")
        self.choose_folder_button.setMinimumHeight(40)
        self.choose_folder_button.clicked.connect(self._choose_folder)
        folder.addWidget(self.choose_folder_button)

        folder.addStretch(1)
        row.addLayout(folder)

        return row

    def _build_text_panel(self) -> QWidget:
        """Панель с распознанным текстом.

        Сама себя не прячет: её показывает и прячет вторая зона,
        иначе получилось бы два переключателя на одном экране.
        """
        self.text_panel = QFrame()
        self.text_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.text_panel.setProperty("panel", True)

        layout = QVBoxLayout(self.text_panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.text_header = QLabel("Текст распознавания")
        self.text_header.setProperty("heading", True)
        header.addWidget(self.text_header)

        self.dirty_label = QLabel("есть несохранённые правки")
        self.dirty_label.setProperty("warning", True)
        self.dirty_label.setVisible(False)
        header.addWidget(self.dirty_label)

        header.addStretch(1)

        self.save_button = QPushButton("Сохранить")
        self.save_button.setToolTip("Записать правки обратно в файлы .txt")
        header.addWidget(self.save_button)

        self.save_as_button = QPushButton("Сохранить как")
        self.save_as_button.setToolTip("Сохранить текст из окна отдельным файлом")
        header.addWidget(self.save_as_button)

        self.copy_button = QPushButton("Скопировать в буфер")
        self.copy_button.setToolTip(
            "Скопировать выделенное, а если ничего не выделено — весь текст"
        )
        header.addWidget(self.copy_button)

        self.clear_button = QPushButton("Очистить")
        header.addWidget(self.clear_button)

        layout.addLayout(header)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "Здесь появляется распознанный текст.\nТекст можно править перед отправкой."
        )
        self.text_edit.setMinimumHeight(170)
        self.text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.text_edit)

        # Значок копирования включается, только когда есть что копировать
        self.text_edit.textChanged.connect(self._update_copy_button)
        self._update_copy_button()

        return self.text_panel

    def _update_copy_button(self) -> None:
        """Кнопка копирования гаснет на пустом тексте."""
        self.copy_button.setEnabled(bool(self.text_edit.toPlainText().strip()))

    def _connect(self) -> None:
        """Кнопки и сигналы."""
        self.record_button.clicked.connect(self._toggle_record)
        self.stop_button.clicked.connect(self._stop_record)
        self.cancel_button.clicked.connect(self._cancel_record)
        self.transcribe_button.clicked.connect(self._transcribe)

        self.save_button.clicked.connect(self._save_blocks)
        self.save_as_button.clicked.connect(self._save_as_file)
        self.copy_button.clicked.connect(self._copy_to_clipboard)
        self.clear_button.clicked.connect(self._clear_text)
        self.zone_button.clicked.connect(self._toggle_zone)

        self.text_edit.textChanged.connect(self._on_text_changed)

        self.signals.recorded.connect(self._on_recorded)
        self.signals.nothing_recorded.connect(self._on_nothing_recorded)
        self.signals.record_failed.connect(self._on_error)
        self.signals.cancelled.connect(self._on_cancelled)

        self.signals.model_ready.connect(self._on_model_ready)
        self.signals.model_failed.connect(self._on_model_failed)

        self.signals.file_started.connect(self._on_file_started)
        self.signals.file_done.connect(self._on_file_done)
        self.signals.all_done.connect(self._on_all_done)
        self.signals.transcribe_failed.connect(self._on_error)

    # -------------------------------------------------------------- наполнение

    def _fill_devices(self) -> None:
        """Пустое место на время проверки микрофонов."""
        self.device_box.clear()
        self.device_box.addItem("Проверяю микрофоны…", None)
        self.device_box.setEnabled(False)
        self.record_button.setEnabled(False)

    def _start_device_probe(self) -> None:
        """Проверка микрофонов по одному устройству за такт.

        Проверять можно только из главного потока: WASAPI из фонового
        не открывается. Чтобы окно не замирало, устройства проверяются
        по очереди, между нимиQt успевает обработать события.

        Нужна один раз при запуске: устройства могут быть отключены,
        переименованы или отвалиться при смене USB-порта.
        """
        try:
            self._candidates = candidate_devices()
        except Exception:  # noqa: BLE001 - PortAudio поднимает всё подряд
            self._candidates = []

        self._working: list[tuple[int, str]] = []
        self._probe_index = 0

        if not self._candidates:
            self._on_devices_ready([])
            return

        self._probe_timer = QTimer(self)
        self._probe_timer.timeout.connect(self._probe_tick)
        self._probe_timer.start(1)

    def _probe_tick(self) -> None:
        """Одна проверка за такт."""
        if self._probe_index >= len(self._candidates):
            self._probe_timer.stop()
            self._on_devices_ready(self._working)
            return

        index, name = self._candidates[self._probe_index]
        self._probe_index += 1

        if test_device(index):
            self._working.append((index, name))

    def _on_devices_ready(self, devices) -> None:
        """Заполняет список проверенными микрофонами."""
        self.device_box.clear()
        self.device_box.setEnabled(True)

        if not devices:
            self.device_box.addItem("Микрофон не найден", None)
            self.record_button.setEnabled(False)
            self._say("Работающих микрофонов не найдено", WARNING)
            return

        preferred = self.settings.microphone
        chosen = preferred if preferred is not None else default_device(devices)

        for index, name in devices:
            self.device_box.addItem(name, index)
            self.device_box.setItemData(
                self.device_box.count() - 1, name, Qt.ItemDataRole.ToolTipRole
            )

        # Выпадающий список делаем достаточно широким, чтобы названия
        # микрофонов не обрезались многоточием
        self.device_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        view = self.device_box.view()
        try:
            width_hint = view.sizeHintForColumn(0)
            scrollbar_w = view.verticalScrollBar().sizeHint().width()
            view.setMinimumWidth(max(width_hint + scrollbar_w + 16, 360))
        except Exception:  # noqa: BLE001, S110
            pass  # не критично: список чуть уже, но рабочий

        if chosen is not None:
            position = self.device_box.findData(chosen)
            if position >= 0:
                self.device_box.setCurrentIndex(position)

        self.record_button.setEnabled(True)
        skipped = len(self._candidates) - len(devices)
        suffix = f", отброшено {skipped}" if skipped > 0 else ""
        self._say(f"Микрофонов готово: {len(devices)}{suffix}")

    def _fill_models(self) -> None:
        """Выбирает в списке модель, которая сейчас в настройках."""
        position = self.model_box.findData(self.settings.model)
        if position >= 0:
            self.model_box.setCurrentIndex(position)

    def _refresh_list(self, announce: bool = True) -> None:
        """Перечитывает записи текущего блока с диска.

        Порядок хронологический: старые сверху, новые снизу. Список
        читается как журнал, а свежая запись всегда внизу — и
        программа сама прокручивает к ней.

        Показывается только тот блок, название которого сейчас в
        поле: в таблице лежат файлы одного блока, а не всех сразу.
        Иначе блоки перемешиваются и непонятно, где чьи записи.

        announce выключается при наборе названия: сообщение о
        количестве записей мигало бы на каждой букве.
        """
        block = self._current_block()
        recordings = [
            item for item in storage.scan(self.recordings) if item.block_folder == block
        ]

        self.record_table.setRowCount(len(recordings))

        # scan() отдаёт от свежих к старым, таблице нужен обратный порядок
        for row_number, item in enumerate(reversed(recordings)):
            self._fill_row(row_number, item)

        # Показываем последнюю запись: она внизу после разворота
        self.record_table.scrollToBottom()

        pending = sum(1 for item in recordings if not item.has_text)

        if announce:
            self._say(
                f"Блок «{block}»: записей {len(recordings)}, без текста {pending}"
            )

        self._update_transcribe_button()

    def _fill_row(self, row_number: int, item: storage.Recording) -> None:
        """Заполняет одну строку таблицы."""
        cells = [
            item.block or storage.NO_BLOCK,
            item.name,
            item.status,
            item.length,
            f"{item.size_mb:.1f} МБ",
        ]
        hints = [
            None,
            "Двойной щелчок — открыть запись в проигрывателе",
            "Двойной щелчок — открыть текст в блокноте"
            if item.has_text
            else "Запись ещё не распознана",
            "Длительность записи в минутах и секундах",
            "Размер файла записи в мегабайтах",
        ]

        for column, value in enumerate(cells):
            cell = QTableWidgetItem(value)
            if hints[column]:
                cell.setToolTip(hints[column])

            if column == COLUMN_BLOCK:
                # Путь хранится в данных ячейки: он нужен при выборе
                # строки и при двойном щелчке, а всплывающая
                # подсказка исчезает
                cell.setData(Qt.ItemDataRole.UserRole, str(item.wav))
            elif column == COLUMN_STATUS:
                # Цветом сразу видно, что ещё не распознано
                color = QColor(LIVE if item.has_text else MUTED)
                cell.setForeground(QBrush(color))

            self.record_table.setItem(row_number, column, cell)

    def _open_row_file(self, row: int, column: int) -> None:
        """Двойной щелчок открывает файл как обычно в Windows.

        По имени записи открывается сам звук — его возьмёт
        проигрыватель, назначенный системой. По имени текстового
        файла — блокнот. В остальных колонках щелчок ничего не делает.
        """
        if column not in (COLUMN_FILE, COLUMN_STATUS):
            return

        first = self.record_table.item(row, COLUMN_BLOCK)
        if first is None:
            return

        wav = Path(first.data(Qt.ItemDataRole.UserRole))
        target = wav if column == COLUMN_FILE else wav.with_suffix(".txt")

        if not target.is_file():
            self._say(f"Файла нет: {target.name}", ERROR)
            return

        try:
            # startfile открывает файл программой по умолчанию для
            # этого расширения: звук — проигрывателем, txt — блокнотом
            os.startfile(target)  # noqa: S606 - путь из таблицы, shell тут не нужен
        except OSError as error:
            self._say(f"Не удалось открыть: {error}", ERROR)
            return

        self._say(f"Открыт {target.name}", SUCCESS)

    def _set_transcribe_caption(self, text: str) -> None:
        """Надпись на кнопке распознавания."""
        self.transcribe_button.setText(text)

    def _update_transcribe_button(self) -> None:
        """Кнопка «Распознать».

        Пока идёт распознавание, кнопка остаётся нажимаемой: ею
        прерывают работу. Выключать её нельзя — тогда исчез бы
        и перелив, и способ остановить распознавание.

        Заодно обновляет доступность записи: одновременно писать
        и распознавать нельзя, иначе пришлось бы молча ронять
        распознавание по второму нажатию.
        """
        if self.transcriber.is_busy:
            self.transcribe_button.setEnabled(True)
            self._set_transcribe_caption("Остановить")
            self._lock_record_during_transcribe(True)
            return

        self._lock_record_during_transcribe(False)

        recorder = self.recorder
        recording = recorder.is_recording if recorder is not None else False
        has_pending = bool(
            storage.pending(self.recordings, block=self._current_block())
        )

        self.transcribe_button.setEnabled(has_pending and not recording)
        self._set_transcribe_caption("Распознать")

    def _lock_record_during_transcribe(self, busy: bool) -> None:
        """Гасит кнопку записи, пока идёт распознавание.

        Модель не потокобезопасна и работает в своём потоке: вторая
        запись заняла бы микрофон и CPU, а распознавание — тем более.
        Раньше нажатие просто отменяло распознавание молча, и
        половина файлов оставалась без текста.
        """
        if busy:
            self.record_button.setEnabled(False)
            self.record_button.setToolTip(
                "Дождитесь окончания распознавания — потом можно будет записать снова"
            )
            return

        recorder = self.recorder
        if recorder is not None and recorder.is_recording:
            return  # идёт запись — состояние кнопки ведёт _update_record_ui

        self.record_button.setEnabled(True)

    # ----------------------------------------------------------------- запись

    def _ensure_recorder(self) -> Recorder:
        """Запись создаётся один раз и живёт вместе с окном."""
        if getattr(self, "_recorder", None) is None:
            self._recorder = Recorder(
                on_finished=lambda path, dur, peak: self.signals.recorded.emit(
                    str(path), dur, peak
                ),
                on_empty=self.signals.nothing_recorded.emit,
                on_cancelled=self.signals.cancelled.emit,
            )
        return self._recorder

    @property
    def recorder(self) -> Recorder:
        """Рекордер текущего окна, если он уже создан."""
        return getattr(self, "_recorder", None)

    def _toggle_record(self) -> None:
        """Одна клавиша на три состояния: начать, пауза, продолжить."""
        # Кнопку мы уже гасим, но сюда попадают и горячие клавиши,
        # и автозапуск: микрофон должен быть один
        if self.transcriber.is_busy:
            self._say(
                "Идёт распознавание — запись начнётся сразу после него",
                WARNING,
            )
            return

        recorder = self._ensure_recorder()

        if recorder.is_recording:
            recorder.toggle_pause()
            self._update_record_ui()
            return

        block = self.block_input.text().strip()
        target = storage.unique_path(self.recordings, block)

        device = self.device_box.currentData()
        try:
            recorder.start(target, device)
        except Exception as error:  # noqa: BLE001 - причина показывается внизу
            # Поток звука открывается сразу, поэтому ошибка видна тут же
            self._say(f"Не удалось начать запись: {error}", ERROR)
            return

        self._auto_stop_warned = False
        self._auto_stopped = False
        self._set_recording_ui(True)
        self._say(f"Идёт запись → {target.name}", BUSY)

    def _stop_record(self) -> None:
        """Останавливает запись и сразу сохраняет файл.

        Подтверждение не нужно: потерять запись из-за лишнего
        вопроса дороже, чем получить лишний файл на диске.
        """
        recorder = self._ensure_recorder()
        if not recorder.is_recording:
            return

        recorder.stop()
        self._set_recording_ui(False)
        self._say("Сохраняю запись…", BUSY)

    def _cancel_record(self) -> None:
        """Отменяет текущую запись, файл не сохраняется."""
        self._ensure_recorder().cancel()
        self._set_recording_ui(False)

    def _set_recording_ui(self, recording: bool) -> None:
        """Кнопки и индикатор во время записи и после неё."""
        self.stop_button.setEnabled(recording)
        self.cancel_button.setEnabled(recording)

        if not recording:
            self.indicator.reset()

        self._update_record_ui()
        self._update_transcribe_button()

    def _update_record_ui(self) -> None:
        """Значок на клавише старта и состояние индикатора."""
        recorder = self.recorder
        recording = recorder is not None and recorder.is_recording
        paused = recording and recorder.is_paused

        if not recording:
            self.record_button.setIcon(icon("play", BUTTON_ICON))
            self.record_button.setToolTip("Начать запись")
            self.indicator.set_state(IDLE)
        elif paused:
            self.record_button.setIcon(icon("play", BUTTON_ICON))
            self.record_button.setToolTip("Продолжить запись")
            self.indicator.set_state(PAUSED)
            self._say("Пауза — нажмите ту же клавишу, чтобы продолжить", BUSY)
        else:
            self.record_button.setIcon(icon("pause", BUTTON_ICON))
            self.record_button.setToolTip("Пауза")
            self.indicator.set_state(RECORDING)

    def _on_recorded(self, path: str, duration: float, peak: float) -> None:
        """Запись закончилась: обновляет список и сообщает о результате.

        Если шла запись по автостопу или звук почти не записан —
        говорит об этом в строке состояния. При включённом
        автораспознавании тут же начинает расшифровку.
        """
        self._set_recording_ui(False)
        self._refresh_list()

        message = f"Готово: {Path(path).name}  ({format_duration(duration)})"
        if self._auto_stopped:
            # Пользователь ■ не нажимал — надо сказать, что произошло
            message += "  — остановлено по тишине"
        elif peak < 0.01:
            message += "  — звук почти не записан"
        self._say(message, SUCCESS)

        if self.auto_transcribe_box.isChecked():
            self._transcribe()

    # ------------------------------------------------- автоматическая расшифровка

    def _on_auto_transcribe(self, checked: bool) -> None:
        """Запоминает настройку «Распознавать после остановки»."""
        self.settings.auto_transcribe = checked
        self.settings.save()

    def _on_auto_copy(self, checked: bool) -> None:
        """Запоминает настройку «Копировать в буфер»."""
        self.settings.auto_copy = checked
        self.settings.save()

    def _on_timecodes(self, checked: bool) -> None:
        """Запоминает настройку «Таймкоды»."""
        self.settings.timecodes = checked
        self.settings.save()

    def _on_play_sound(self, checked: bool) -> None:
        """Запоминает настройку «Звук после распознавания»."""
        self.settings.play_sound = bool(checked)
        self.settings.save()

    def _on_auto_stop(self, action: QAction) -> None:
        """Выбран новый срок автостопа (или он выключен вовсе)."""
        self.settings.auto_stop_seconds = action.data()
        self.settings.save()
        self._mark_auto_stop()

    def _mark_auto_stop(self) -> None:
        """Отмечает в списке тот срок, который сейчас в настройках."""
        current = self.settings.auto_stop_seconds
        for action in self.auto_stop_menu.actions():
            action.setChecked(action.data() == current)

    def _highlight_auto_stop(self) -> None:
        """Подсвечивает текущий срок при открытии списка.

        Списки микрофона и модели открываются с подсвеченной выбранной
        строкой — список автостопа должен выглядеть так же.
        """
        self._mark_auto_stop()
        for action in self.auto_stop_menu.actions():
            if action.isChecked():
                self.auto_stop_menu.setActiveAction(action)
                return

    def _on_nothing_recorded(self) -> None:
        """Микрофон молчал — запись отменена, файл не создан."""
        self._set_recording_ui(False)
        self._say("Отсутствуют входные данные, запись отменена", WARNING)

    def _on_error(self, message: str) -> None:
        """Ошибка из рабочего потока: показываем текстом в строке состояния."""
        self._say(f"Ошибка: {message}", ERROR)
        self._set_recording_ui(False)
        self._update_transcribe_button()

    def _on_cancelled(self) -> None:
        """Запись отменена пользователем, файл не сохраняется."""
        self._set_recording_ui(False)
        self._say("Запись отменена, файл не сохранён", WARNING)

    # ------------------------------------------------------------ распознавание

    def _start_model(self) -> None:
        """Модель грузится один раз, сразу при запуске.

        До загрузки выясняем, лежит ли модель на диске: от этого зависит
        и сообщение в строке состояния, и уведомление при первом запуске.
        """
        name = self.settings.model
        self._model_info = model_store.describe(name)
        self._model_needs_download = not self._model_info.cached
        self._model_moved_from = self._model_info.moved_from
        size = model_store.format_size(self._model_info.footprint)

        if self._model_needs_download:
            message = (
                f"Скачиваю модель «{name}» из интернета — это делается один раз"
            )
            self._start_download_progress(name)
        elif self._model_moved_from is not None:
            message = (
                f"Перенёс модель «{name}» из старого кэша и загружаю в память ({size})"
            )
        else:
            message = (
                f"Загружаю модель «{name}» с диска в память ({size}) — интернет не нужен"
            )

        # Пока модель не готова, это сообщение главное: обычные строки
        # статуса его не перебивают (см. _say).
        self._model_loading = True
        self._set_status(message, BUSY)

        self.model_store.load_async(
            name,
            on_done=lambda _model: self.signals.model_ready.emit(),
            on_error=lambda err: self.signals.model_failed.emit(str(err)),
        )

    def _start_download_progress(self, name: str) -> None:
        """Таймер прогресса скачивания: процент, скорость, сколько осталось.

        Общий объём спрашиваем у Hugging Face один раз в фоновом
        потоке — сетевой запрос не должен задерживать окно.
        """
        threading.Thread(
            target=lambda: model_store.expected_size(name),
            daemon=True,
            name="model-size",
        ).start()

        now = time.monotonic()
        self._model_progress_at = now
        self._model_progress_done = 0
        self._model_progress_speed = 0.0
        self._model_loading_since = None

        if self._model_progress_timer is None:
            self._model_progress_timer = QTimer(self)
            self._model_progress_timer.timeout.connect(self._update_model_progress)
            self._model_progress_timer.setInterval(500)
        self._model_progress_timer.start()

    def _update_model_progress(self) -> None:
        """Один такт таймера: обновляет текст «Скачиваю модель…»."""
        if not self._model_loading:
            self._model_progress_timer.stop()
            return

        name = self.settings.model

        # Скачалось — теперь модель грузится с диска в память,
        # и это тоже не мгновенно: показываем, сколько уже ждём
        if model_store.is_cached(name):
            if self._model_loading_since is None:
                self._model_loading_since = time.monotonic()
            seconds = int(time.monotonic() - self._model_loading_since)
            self._set_status(
                f"Модель «{name}» скачана, загружаю в память… ({seconds} с)",
                BUSY,
            )
            return

        now = time.monotonic()
        interval = now - self._model_progress_at
        if interval >= 1.0:
            done = model_store.downloaded_bytes(name)
            speed = (done - self._model_progress_done) / interval
            # Сглаженная скорость: резкие рывки не дёргают текст
            self._model_progress_speed = (
                0.7 * self._model_progress_speed + 0.3 * speed
            )
            self._model_progress_at = now
            self._model_progress_done = done
        else:
            done = self._model_progress_done

        total = model_store.expected_size(name)
        if total > 0 and done < total:
            percent = min(done * 100 // total, 99)
            remaining = total - done
            if self._model_progress_speed > 0:
                minutes = int(remaining / self._model_progress_speed / 60)
                eta = (
                    f", осталось ~{minutes} мин"
                    if minutes >= 1
                    else ", меньше минуты"
                )
            else:
                eta = ""
            message = (
                f"Скачиваю модель «{name}»: {percent}% "
                f"({self._progress_size(done)} из "
                f"{model_store.format_size(total)})"
                f"{self._progress_speed_text()}{eta}"
            )
        else:
            # Объём пока не известен — считаем только уже скачанное
            message = f"Скачиваю модель «{name}»: уже {self._progress_size(done)}"

        self._set_status(message, BUSY)

    def _progress_size(self, size: int) -> str:
        """Скачанные байты в компактный вид. Ноль не становится «неизвестно»."""
        if size >= 1024**3:
            return f"{size / 1024**3:.2f}".replace(".", ",") + " ГБ"
        return f"{size / 1024**2:.0f} МБ"

    def _progress_speed_text(self) -> str:
        """Скорость скачивания, если она уже измерилась."""
        if self._model_progress_speed <= 0:
            return ""
        megabytes = self._model_progress_speed / 1024 / 1024
        return f", {megabytes:.1f}".replace(".", ",") + " МБ/с"

    def _format_expected_size(self) -> str:
        """Примерный размер модели на диске с учётом режима кэша.

        В режиме копирования файлы ложатся дважды, поэтому места
        нужно вдвое больше — это и показываем заранее.
        """
        info = self._model_info
        megabytes = config.MODEL_SIZES_MB.get(info.name, 0)
        if megabytes and info.duplicates:
            megabytes *= 2
        if not megabytes:
            return "неизвестно"
        return model_store.format_size(megabytes * 1024 * 1024)

    def _model_mode_text(self) -> str:
        """Каким способом хранилище держит файлы."""
        if self._model_info.symlinks:
            return "симлинки — файлы не дублируются"
        return "копирование — файлы дублируются (режим разработчика Windows выключен)"

    def _model_mode_hint(self) -> str:
        """Что даёт режим копирования и как его поправить."""
        if self._model_info.symlinks:
            return ""
        return (
            "Из-за копирования модель займёт примерно вдвое больше места. "
            "Вернуть место можно, включив в Windows режим разработчика "
            "(Параметры → Система → Для разработчиков) и удалив папку модели: "
            "при следующем запуске модель скачается заново, уже без дублирования."
        )

    def _announce_download(self) -> None:
        """Уведомление перед первой загрузкой модели.

        Показывается один раз, когда модели ещё нет на диске. Объясняет,
        что скачивается, куда, каким способом хранятся файлы и что будет
        после загрузки.
        """
        info = self._model_info
        parts = [
            f"Модель «{info.name}» скачивается один раз, из интернета.",
            "",
            f"Куда: {info.models_dir}",
            f"Режим: {self._model_mode_text()}",
            f"Ожидаемый размер: около {self._format_expected_size()}",
        ]

        hint = self._model_mode_hint()
        if hint:
            parts += ["", hint]

        parts += [
            "",
            "Пока идёт загрузка, приложение можно использовать как обычно. "
            "После неё интернет больше не понадобится: распознавание идёт "
            "локально, на этом компьютере.",
        ]

        QMessageBox.information(
            self, "Первый запуск: скачивается модель", "\n".join(parts)
        )

    def _announce_move(self) -> None:
        """Уведомление о переносе модели из старого кэша HF.

        Показывается один раз: раньше модель лежала в общем кэше,
        теперь живёт в папке приложения, и качать её заново не нужно.
        """
        info = self._model_info
        size = model_store.format_size(info.footprint)
        QMessageBox.information(
            self,
            "Модель переехала",
            f"Модель «{info.name}» больше не лежит в кэше Hugging Face — "
            f"я перенёс её в папку приложения:\n\n{info.models_dir}\n\n"
            f"Занято на диске: {size}. Скачивать заново не нужно: интернет "
            "уже не понадобится, распознавание идёт локально.",
        )

    def _on_model_ready(self) -> None:
        """Модель загружена: останавливает таймер прогресса и сообщает,
        что можно работать офлайн. После первой загрузки показывает
        отдельное окно с размером модели.
        """
        if self._model_progress_timer is not None:
            self._model_progress_timer.stop()
        name = self.settings.model
        size_text = model_store.format_size(model_store.model_size_on_disk(name))
        self._model_loading = False
        self._say(f"Модель «{name}» готова ({size_text} на диске). Работаем офлайн.", SUCCESS)

        # После первой загрузки отдельно сообщаем размер и офлайн-режим
        if self._model_needs_download:
            self._model_needs_download = False
            QMessageBox.information(
                self,
                "Модель готова",
                f"Модель «{name}» скачана и сохранена.\n\n"
                f"Занято на диске: {size_text}.\n\n"
                "Интернет больше не нужен: распознавание идёт локально, "
                "на этом компьютере.",
            )

        self._update_transcribe_button()

    def _on_model_failed(self, error: str) -> None:
        """Модель не загрузилась: ошибка в строке состояния."""
        if self._model_progress_timer is not None:
            self._model_progress_timer.stop()
        self._model_loading = False
        self._say(f"Не удалось загрузить модель: {error}", ERROR)
        self._update_transcribe_button()

    def _transcribe(self) -> None:
        """Расшифровывает последнюю запись без готового текста.

        Повторный щелчок по кнопке прерывает распознавание: пока
        идёт работа, шкала лежит поверх кнопки, но мышь проходит
        насквозь и попадает в кнопку.
        """
        if self.transcriber.is_busy:
            self.transcriber.cancel()
            self._say("Останавливаю распознавание…", BUSY)
            return

        waiting = storage.pending(self.recordings, block=self._current_block())
        if not waiting:
            self._say("Нечего распознавать: все записи уже с текстом", WARNING)
            return

        target = waiting[0]
        self._say(f"Распознаю {target.name}", BUSY)
        self._pulse_start("Распознаю…")

        started = self.transcriber.start(
            [target.wav],
            self.model_box.currentData(),
            on_file_start=lambda wav: self.signals.file_started.emit(str(wav)),
            on_file_done=lambda wav, lines, text: self.signals.file_done.emit(
                str(wav), lines, text
            ),
            on_all_done=lambda done, total, stopped: self.signals.all_done.emit(
                done, total, stopped
            ),
            on_error=lambda err: self.signals.transcribe_failed.emit(str(err)),
            timecodes=self.timecodes_box.isChecked(),
        )

        if started:
            self._update_transcribe_button()

    def _pulse_start(self, caption: str) -> None:
        """Кнопка распознавания переливается, пока идёт работа.

        Подпись показывает, какой файл разбирается: при записи
        подлиннее первые куски приходят не сразу, и без имени
        пауза выглядит зависанием.
        """
        self.transcribe_button.start_pulse()
        self.transcribe_button.setToolTip(caption)

    def _pulse_stop(self) -> None:
        """Останавливает переливание кнопки «Распознать»."""
        self.transcribe_button.stop_pulse()
        self.transcribe_button.setToolTip(
            "Расшифровать последнюю запись без готового текста"
        )

    def _on_file_started(self, wav: str) -> None:
        """Один файл начал распознаваться: включает пульсацию кнопки."""
        self._pulse_start(f"Распознаю {Path(wav).name}…")
        self._say(f"Распознаю {Path(wav).name}, это займёт время", BUSY)

    def _on_file_done(self, wav: str, lines: int, text: str) -> None:
        """Один файл расшифрован: добавляет текст в окно и обновляет список.
        Если речи не нашлось — предупреждение, иначе успех.
        """
        self._append_text(Path(wav), text)
        self._refresh_list()

        if lines == 0:
            self._say(f"{Path(wav).name}: речь не распознана", WARNING)
        else:
            self._say(f"{Path(wav).name}: готово, строк {lines}", SUCCESS)

    def _on_all_done(self, done: int, total: int, stopped: bool) -> None:
        """Всё распознавание закончилось: убирает пульсацию, пишет итог в
        строку состояния и, если нужно, издаёт звук завершения.
        """
        self._pulse_stop()
        self._update_transcribe_button()
        if stopped:
            # Файла не осталось: прервали на середине и ничего не записали
            self._say(
                "Остановил. Текст не сохранён — запись можно распознать заново",
                WARNING,
            )
        else:
            remaining = len(storage.pending(self.recordings))
            suffix = f", осталось без текста: {remaining}" if remaining else ""
            if self._copied_chars:
                suffix += f", скопировано в буфер: {self._copied_chars} символов"
            self._say(f"Распознано файлов: {done}{suffix}", SUCCESS)
            self._copied_chars = 0
            if done > 0 and getattr(self.settings, "play_sound", True):
                _play_done_sound()

    # ------------------------------------------------------------------ текст

    def _scroll_to_end(self) -> None:
        """Прокручивает текст к последнему куску.

        Новый кусок встаёт ниже видимой части, и без прокрутки
        распознанное остаётся за нижним краем: пользователь видел бы
        его, только потянув ползунок сам. Курсор при этом не трогаем —
        иначе правка посреди текста при следующей записи уехала бы
        в самый конец.
        """
        bar = self.text_edit.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _insert_separator(self, title: str, text: str) -> None:
        """Вставляет кусок текста вместе с разделителем перед ним.

        Разделитель — серая полоска, и она видна всегда: с заголовком
        «── дата · блок ──», когда включены таймкоды, и без единого
        символа, когда они выключены. Метка при этом живёт в
        оформлении блока, а не в тексте: в буфер обмена и в файл такая
        граница не попадает, там остаётся только речь.
        """
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.text_edit.toPlainText():
            # Пустая строка перед полосой: без неё куски липнут друг
            # к другу. QPlainTextEdit не умеет отступы абзацев, поэтому
            # это отдельная пустая строка — в тексте она остаётся, и по
            # ней видно, где кончилась одна запись и началась другая
            cursor.insertBlock()
            cursor.insertBlock()

        mark = QTextBlockFormat()
        mark.setProperty(QTextFormat.Property.UserProperty, SEPARATOR_MARK)
        # Полоска видна всегда: граница между кусками не должна зависеть
        # от того, включены ли метки времени
        mark.setBackground(QColor(SEPARATOR_BG))
        if title:
            mark.setForeground(QBrush(QColor(MUTED)))
            cursor.insertText(title)
        cursor.setBlockFormat(mark)

        # Текст куска — в отдельном блоке. Иначе метка попала бы
        # в сам кусок (сохранять было бы нечего) и унаследовалась
        # бы всеми следующими строками при вводе.
        cursor.insertBlock()
        cursor.setBlockFormat(QTextBlockFormat())
        cursor.insertText(text)

    def _separator_blocks(self) -> list[QTextBlock]:
        """Блоки-разделители в порядке появления.

        Идём по блокам документа, а не ищем текст: метка живёт
        в оформлении и в тексте не читается.
        """
        marks = []
        block = self.text_edit.document().begin()
        while block.isValid():
            if (
                block.blockFormat().property(QTextFormat.Property.UserProperty)
                == SEPARATOR_MARK
            ):
                marks.append(block)
            block = block.next()
        return marks

    def _append_text(self, wav: Path, text: str) -> None:
        """Добавляет распознанный текст в окно и запоминает, откуда он."""
        title = storage.block_of(self.recordings, wav) or storage.NO_BLOCK
        when = storage.parse_when(wav.stem)
        # Секунды в разделителе: две записи в одну минуту должны
        # остаться разными кусками
        stamp = when.strftime("%d.%m.%Y %H:%M:%S") if when else "—"

        self._appending = True

        # Разделитель всегда с меткой: с заголовком, если включены
        # таймкоды, и пустой полосой без единого символа, если нет
        title = f"── {stamp} · {title} ──" if self.timecodes_box.isChecked() else ""
        self._insert_separator(title, text)

        # Свежий кусок должен быть на виду: он встаёт ниже видимой
        # части, и без прокрутки его пришлось бы искать ползунком
        self._scroll_to_end()

        self._appending = False

        self.blocks.append(TextBlock(wav=wav))
        self.dirty = False
        self._update_dirty()

        # В буфер идёт только что распознанный кусок, а не всё окно:
        # после ■ в буфер должен попасть ровно то, что он сейчас
        # записал, иначе вставить «куда угодно» неудобно — в буфер
        # уехала бы вся история разговора вместе с разделителями.
        # Пустой текст тоже не кладём: стёртое содержимое буфера
        # было бы не вернуть.
        if self.auto_copy_box.isChecked() and text.strip():
            QApplication.clipboard().setText(text.strip())
            self._copied_chars = len(text.strip())

    def _on_text_changed(self) -> None:
        """Правка пользователя делает окно несохранённым."""
        if self._appending or self.dirty:
            return
        self.dirty = True
        self._update_dirty()

    def _update_dirty(self) -> None:
        """Показывает или скрывает метку «есть несохранённые правки» и
        доступность кнопки «Сохранить».
        """
        self.dirty_label.setVisible(self.dirty)
        self.save_button.setEnabled(self.dirty)

    def _save_blocks(self) -> None:
        """Пишет правки из окна обратно в файлы .txt.

        Кусок идёт от своего разделителя до следующего: метки лежат
        в оформлении, поэтому правки в любом месте не сбивают границы
        и в текст просачивается только речь.
        """
        if not self.blocks:
            return

        content = self.text_edit.toPlainText()

        marks = self._separator_blocks()
        if len(marks) < len(self.blocks):
            self._say("Разделители потеряны, куски не разделены", WARNING)
            return

        written = 0
        for index, block in enumerate(self.blocks):
            # Тело куска начинается после разделителя, а кончается
            # началом следующего разделителя (или концом окна)
            start = marks[index].position() + len(marks[index].text()) + 1
            end = (
                marks[index + 1].position()
                if index + 1 < len(marks)
                else len(content)
            )

            chunk = content[start:end].strip()
            block.wav.with_suffix(".txt").write_text(chunk, encoding="utf-8")
            written += 1

        self.dirty = False
        self._update_dirty()
        self._refresh_list()
        self._say(f"Правки записаны в файлы: {written}", SUCCESS)

    def _save_as_file(self) -> None:
        """Сохраняет содержимое окна отдельным файлом."""
        text = self.text_edit.toPlainText().strip()
        if not text:
            self._say("В окне нет текста", WARNING)
            return

        target, _filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить текст",
            str(self.recordings / "текст.txt"),
            "Текстовые файлы (*.txt);;Все файлы (*)",
        )
        if not target:
            return

        try:
            Path(target).write_text(text, encoding="utf-8")
        except OSError as error:
            self._say(f"Не удалось сохранить: {error}", ERROR)
            return

        self._say(f"Сохранено: {target}", SUCCESS)

    def _copy_text(self) -> None:
        """Копирует в буфер обмена.

        Если выделен кусок — только он, иначе весь текст из окна.
        Так же работает и кнопка, и значок в углу поля.
        """
        cursor = self.text_edit.textCursor()

        if cursor.hasSelection():
            # QTextCursor отдаёт абзацы символом перевода строки
            text = cursor.selectedText().replace(" ", "\n")
        else:
            text = self.text_edit.toPlainText().strip()

        if not text:
            self._say("В окне нет текста", WARNING)
            return

        QApplication.clipboard().setText(text)
        self._say(f"Скопировано в буфер обмена: {len(text)} символов", SUCCESS)

    def _copy_to_clipboard(self) -> None:
        """Кнопка «Копировать»: то же, что делает значок в углу."""
        self._copy_text()

    def _clear_text(self) -> None:
        """Очищает окно. Файлы на диске остаются."""
        if self.dirty and not self._ask_save("Очистить окно?"):
            return

        self._appending = True
        self.text_edit.clear()
        self._appending = False

        self.blocks.clear()
        self.dirty = False
        self._update_dirty()
        self._say("Окно очищено, файлы на диске сохранились", SUCCESS)

    def _measure(self, zone_limit: int) -> int:
        """Высота окна при заданной высоте зоны.

        Пока зона свёрнута, её размер не посчитать, поэтому на миг
        снимаем ограничение, меряем и возвращаем как было.

        Меряется только высота: adjustSize() подогнал бы ещё и ширину
        центрального виджета под подсказку размера, и окно на миг
        теряло бы ширину.
        """
        saved = self.details_zone.maximumHeight()
        self.details_zone.setMaximumHeight(zone_limit)
        needed = self.centralWidget().sizeHint().height()
        self.details_zone.setMaximumHeight(saved)
        return needed

    def _stationary_height(self) -> int:
        """Высота окна без второй зоны: только запись."""
        return self._measure(0)

    def _zone_height(self) -> int:
        """Высота зоны, когда она раскрыта.

        Ограничена экраном, а не половиной окна: окно само
        подрастает под зону, поэтому резать её не нужно.
        """
        screen = QApplication.primaryScreen()
        limit = screen.availableGeometry().height() - 140 if screen else 700

        saved = self.details_zone.maximumHeight()
        self.details_zone.setMaximumHeight(16777215)
        needed = self.details_zone.sizeHint().height()
        self.details_zone.setMaximumHeight(saved)

        return min(needed, max(200, limit))

    def _fit_window(self) -> None:
        """Подгоняет высоту окна под то, что сейчас показано.

        Вызывается, когда анимация зоны дошла до конца: окно должно
        ужиматься вместе с ней, иначе под индикатором остаётся пустота.
        """
        if self.details_zone.maximumHeight() > 0:
            target = self._measure(16777215)
            # Зона только что раскрылась: полоса прокрутки таблицы
            # появилась вместе с ней, до этого прокручивать некуда
            self.record_table.scrollToBottom()
            # Текст тоже открывается на последнем куске, а не на начале
            self._scroll_to_end()
        else:
            target = self._stationary_height()

        screen = QApplication.primaryScreen()
        limit = screen.availableGeometry().height() - 90 if screen else 800

        self.resize(self.width(), max(300, min(target, limit)))

    def _toggle_zone(self) -> None:
        """Кнопка «Записи и текст»: открыть или закрыть зону.

        У checkable-кнопки состояние меняется до вызова слота,
        поэтому здесь оно уже новое — смотрим на него.
        """
        if self.zone_button.isChecked():
            self._show_zone()
        else:
            self._hide_zone()

    def _show_zone(self) -> None:
        """Раскрывает вторую зону: список записей и текст распознавания."""
        self.zone_button.setChecked(True)
        self.zone_button.setToolTip("Скрыть записи и текст распознавания")
        self._move_zone(self._zone_height())

    def _hide_zone(self) -> None:
        """Сворачивает вторую зону, остаётся только панель записи."""
        self.zone_button.setChecked(False)
        self.zone_button.setToolTip("Записи и текст распознавания")
        self._move_zone(0)

    def _move_zone(self, target: int) -> None:
        """Ведёт зону от текущей высоты к target.

        Высота окна без зоны меряется здесь, до старта: во время
        анимации зона меняется, и мерить её на каждом кадре незачем.
        """
        self._zone_base = self._stationary_height()

        self._zone_animation.stop()
        self._zone_animation.setStartValue(self.details_zone.maximumHeight())
        self._zone_animation.setEndValue(target)
        self._zone_animation.start()

    def _follow_zone(self, height: int) -> None:
        """Кадр анимации: окно тянется за зоной.

        Высота окна — постоянная часть плюс то, что сейчас занимает
        зона. Так содержимое выше зоны стоит на месте, а не уезжает
        вниз, пока окно ждёт конца анимации.
        """
        screen = QApplication.primaryScreen()
        limit = screen.availableGeometry().height() - 90 if screen else 800

        wanted = self._zone_base + int(height)
        self.resize(self.width(), max(300, min(wanted, limit)))

    # ------------------------------------------------------------------ папка

    def _choose_folder(self) -> None:
        """Переход в другой блок.

        Папка рядом с текущими блоками — это сам блок: подставляем
        её название в поле, и туда едут таблица и новые записи.
        Папка снаружи — это новый корень для записей целиком.

        Раньше любую выбранную папку приложение делало корнем, и
        папку блока можно было выбрать по ошибке — тогда блоки
        начинали вкладываться друг в друга.
        """
        chosen = QFileDialog.getExistingDirectory(
            self, "Папка для записей", str(self.recordings)
        )
        if not chosen:
            return

        path = Path(chosen)

        # Прямой потомок корня записей — это папка блока
        if path.parent == self.recordings:
            self.block_input.setText(path.name)
            self._update_block_hint()
            self._refresh_list()
            self._say(f"Открыт блок {path.name}", SUCCESS)
            return

        if config.looks_nested(path, self.recordings):
            answer = QMessageBox.question(
                self,
                "Похоже, выбрана папка блока",
                f"{chosen}\n\n"
                "Эта папка лежит внутри папки записей, но не является "
                "её блоком. Если сделать её корнем, записи лягут ещё "
                "на уровень глубже.\n\n"
                "Всё равно выбрать её корнем?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.settings.recordings_dir = chosen
        self.settings.save()
        self.recordings = self.settings.recordings_path()
        self._update_block_hint()
        self._refresh_list()
        self._say(f"Записи теперь в: {chosen}", SUCCESS)

    # -------------------------------------------------------------- таймер

    def _tick_timer(self) -> None:
        """Уровень и таймер обновляются одним тикером.

        Отдельные сигналы на каждый фрагмент звука забили бы очередь
        событий: PortAudio даёт их тысячи раз в секунду.
        """
        timer = QTimer(self)
        timer.timeout.connect(self._on_tick)
        timer.start(UI_TICK_MS)
        self._ui_timer = timer

    def _on_tick(self) -> None:
        """Забирает свежие данные записи и обновляет индикатор."""
        recorder = getattr(self, "_recorder", None)
        if recorder is None or not recorder.is_recording:
            return

        self.indicator.set_elapsed(recorder.elapsed)
        self.indicator.set_level(recorder.level)

        self._check_auto_stop(recorder)

    def _check_auto_stop(self, recorder: Recorder) -> None:
        """Останавливает запись, если микрофон молчит дольше срока.

        Тишину считает сам рекордер, а решение принимается здесь: поток
        звука нельзя закрывать из фонового потока, только из того, где
        его открывали. Тикер окна как раз работает в главном потоке.
        """
        limit = self.settings.auto_stop_seconds
        if limit is None:
            # Автостоп выключен — запись идёт, пока не нажмут ■
            return

        # Пауза — это решение человека, а не молчание микрофона
        if recorder.is_paused:
            return

        silence = recorder.silence_seconds

        if silence < limit - min(AUTO_STOP_WARN, limit / 2):
            self._auto_stop_warned = False
            return

        if not self._auto_stop_warned:
            self._auto_stop_warned = True
            self._say("Тишина — остановлю запись", WARNING)

        if silence >= limit:
            self._auto_stopped = True
            recorder.stop()
            self._set_recording_ui(False)

    def showEvent(self, event) -> None:
        """При первом показе окно ужимается под содержимое.

        Зона свёрнута, и без этого окно открывается в полный размер
        с пустым местом внизу. Дальше размером управляет анимация.
        """
        super().showEvent(event)
        if not getattr(self, "_fitted_once", False):
            self._fitted_once = True
            self._fit_window()

        # Уведомление о первой загрузке показываем уже на экране, а не
        # до показа окна: иначе диалог появился бы над пустым рабочим
        # столом, и было бы непонятно, к чему он относится.
        if self._model_needs_download and not self._download_notice_shown:
            self._download_notice_shown = True
            QTimer.singleShot(0, self._announce_download)
        elif self._model_moved_from is not None and not self._moved_notice_shown:
            self._moved_notice_shown = True
            QTimer.singleShot(0, self._announce_move)

    # ---------------------------------------------------------------- выход

    def _ask_save(self, title: str) -> bool:
        """Спрашивает про несохранённые правки. True — можно закрывать."""
        answer = QMessageBox.question(
            self,
            title,
            "В окне есть несохранённые правки.\nСохранить их в файлы?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )

        if answer == QMessageBox.StandardButton.Cancel:
            return False

        if answer == QMessageBox.StandardButton.Save:
            self._save_blocks()

        return True

    def closeEvent(self, event) -> None:
        """Перед выходом: закрыть запись и спросить про правки."""
        recorder = getattr(self, "_recorder", None)
        if recorder is not None and recorder.is_recording:
            recorder.stop()
            recorder.cancel()

        if self.transcriber.is_busy:
            self.transcriber.cancel()

        if self.dirty and not self._ask_save("Выход"):
            event.ignore()
            return

        self.settings.last_block = self.block_input.text().strip()
        self.settings.microphone = self.device_box.currentData()
        self.settings.model = self.model_box.currentData()
        self.settings.save()

def _play_done_sound() -> None:
    """Короткий сигнал после завершения распознавания.

    Файл speech_on.wav берётся из src/assets рядом с программой.
    Автономный, не зависит от настроек звуков Windows.
    Учитывает настройку «Звук после распознавания».
    """
    try:
        from src.config import Settings

        settings = Settings()
        if not getattr(settings, "play_sound", True):
            return
    except Exception:
        pass

    from src.core.icons import asset_path

    path = asset_path("speech_on.wav")
    if not path.is_file():
        return

    def _run() -> None:
        with contextlib.suppress(Exception):
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)

    threading.Thread(target=_run, daemon=True).start()


def _set_app_id() -> None:
    """Сообщает Windows, что окно принадлежит нашему приложению.

    Без этого панель задач группирует окно по процессу: в запуске из
    исходников там значок Python, а закреплённая кнопка ведёт не туда.
    Со своим идентификатором Windows берёт значок из окна и запоминает
    приложение отдельно от python.exe. У собранного .exe с вшитым
    значком всё работает и так, но одинаковое поведение лучше.

    Вызывать до создания QApplication: идентификатор читается в момент
    появления первого окна, позже он уже не подействует.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "burmspb.SmartVoiceRecorder"
        )
    except (AttributeError, OSError):
        # Старая Windows или урезанная среда — не повод не запускаться
        pass


def run() -> int:
    """Запуск приложения."""
    _set_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("SmartVoiceRecorder")
    apply_locale(app)
    apply_theme(app)

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()

    return app.exec()
