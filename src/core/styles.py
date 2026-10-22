# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Оформление приложения.

Всё оформление собрано здесь, в одном месте, а не размазано
по виджетам. Виджеты, которым нужен особый вид, помечаются
свойствами прямо в коде, а стили для них описаны в _stylesheet():

    label.setProperty("muted", True)     — приглушённый текст
    label.setProperty("state", "paused") — состояние индикатора
    frame.setProperty("panel", True)     — панель с текстом

Свойства меняются через apply_property(): без unpolish и polish
Qt не перечитывает таблицу стилей и виджет остаётся старым.

Крупный шрифт тоже задаётся здесь, а не через setFont в коде:
иначе оформление снова разъедется по файлам.

Три основных цвета заданы хозяином проекта, остальные посчитаны
от них: границы, приглушённый текст, состояния наведения.
"""

import tempfile
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QPointF, Qt, QTranslator
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

# --- Фон и поверхности -----------------------------------------------
BACKGROUND = "#201e29"  # окно
SURFACE = "#292634"  # поля ввода, таблица, текст
BUTTON = "#363344"  # кнопки и выпадающие списки
PANEL = "#232030"

# --- Границы ----------------------------------------------------------
BORDER = "#413d52"
BORDER_STRONG = "#56516b"

# --- Текст ------------------------------------------------------------
TEXT = "#e8e6f0"
ON_ACCENT = "#ffffff"
MUTED = "#9d98ab"

# Наведение и нажатие
HOVER = "#444055"
PRESSED = "#2e2b3a"

# Выключенные
DISABLED_BG = "#2a2833"
DISABLED_TEXT = "#615d6d"
DISABLED_BORDER = "#35323f"
PLACEHOLDER = "#6d6880"

# --- Акценты ----------------------------------------------------------
FOCUS = "#00a8ff"
LIVE = "#00ffb3"

# Нажатая кнопка. Тёмный синий, а не сам акцент: заливка
# ярко-голубым выбивалась из ряда и кричала громче кнопки записи
ACTIVE = "#25364a"
ACTIVE_TEXT = "#8fd4ff"

# Главная кнопка и запись
PRIMARY = "#ff3b30"
PRIMARY_HOVER = "#ff453a"
PRIMARY_PRESSED = "#d72f26"

# Предупреждение о несохранённых правках
WARNING = "#e0a13a"

def check_mark_path() -> str:
    """Рисует белую галочку и возвращает путь к файлу.

    В Qt-стилях картинка приклеивается через url(), а встроенные
    ресурсы Qt (:/qt-project.org/...) в PyQt6 живут не всегда.
    Поэтому рисуем галочку сами и кладём во временную папку.

    Вызывать только после создания QApplication: QPixmap без него
    завершает процесс, а не бросает исключение.
    """
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    pen = QPen(QColor("#ffffff"))
    pen.setWidth(5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    path = QPainterPath(QPointF(8, 17))
    path.lineTo(QPointF(13.5, 23))
    path.lineTo(QPointF(24, 10))
    painter.drawPath(path)
    painter.end()

    folder = Path(tempfile.gettempdir()) / "smart_voice_recorder"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "check.png"
    pixmap.save(str(target))
    return target.as_posix()

def _stylesheet(check_mark: str) -> str:
    """Собирает таблицу стилей.

    Отдельной функцией, а не константой модуля: путь к галочке
    появляется только после создания QApplication.
    """
    CHECK_MARK = check_mark
    return f"""
QWidget {{
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}

QMainWindow, QDialog {{
    background: {BACKGROUND};
}}

/* --- Подписи --------------------------------------------------------- */

QLabel {{
    color: {TEXT};
    font-size: 13px;
}}

QLabel[muted="true"] {{
    color: {MUTED};
}}

QLabel[warning="true"] {{
    color: {WARNING};
}}

QLabel[heading="true"] {{
    font-weight: 600;
}}

/* Сообщения внизу окна: цвет по смыслу, иначе ошибка и
   «модель готова» выглядели бы одинаково */
QLabel[kind="info"] {{
    color: {MUTED};
}}

QLabel[kind="success"] {{
    color: {LIVE};
}}

QLabel[kind="warning"] {{
    color: {WARNING};
}}

QLabel[kind="error"] {{
    color: {PRIMARY_HOVER};
}}

/* --- Индикатор записи -------------------------------------------------- */

QWidget[indicator="true"] {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* Точка состояния */
QLabel[dot="idle"] {{
    background-color: {BORDER_STRONG};
    border-radius: 7px;
}}

QLabel[dot="recording"] {{
    background-color: {PRIMARY};
    border-radius: 7px;
}}

/* Мигание во время записи */
QLabel[dot="recording"][pulse="true"] {{
    background-color: {PRIMARY_HOVER};
}}

QLabel[dot="paused"] {{
    background-color: {WARNING};
    border-radius: 7px;
}}

QLabel[dot="pulse"] {{
    background-color: {PRIMARY_HOVER};
}}

/* Подпись состояния */
QLabel[state="idle"] {{
    color: {MUTED};
    font-weight: 600;
}}

QLabel[state="recording"] {{
    color: {PRIMARY_HOVER};
    font-weight: 600;
}}

QLabel[state="paused"] {{
    color: {WARNING};
    font-weight: 600;
}}

QLabel[timer="true"] {{
    font-size: 28px;
    font-weight: bold;
    color: {LIVE};
    font-family: "Consolas", monospace;
}}

/* --- Поля ввода и списки --------------------------------------------- */

QLineEdit, QListWidget, QPlainTextEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {ON_ACCENT};
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: {FOCUS};
    selection-color: {ON_ACCENT};
}}

QComboBox {{
    combobox-popup: 0;
    background-color: {BUTTON};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {ON_ACCENT};
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: {FOCUS};
    selection-color: {ON_ACCENT};
}}

QComboBox:disabled {{
    background: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
    color: {DISABLED_TEXT};
}}

QLineEdit:focus, QComboBox:focus, QListWidget:focus, QPlainTextEdit:focus {{
    border: 1px solid {FOCUS};
}}

QLineEdit::placeholder {{
    color: {PLACEHOLDER};
}}

QLineEdit:disabled, QListWidget:disabled, QPlainTextEdit:disabled {{
    background: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
    color: {DISABLED_TEXT};
}}

QComboBox {{
    combobox-popup: 0;
}}

QComboBox::drop-down {{
    border: none;
    padding-right: 10px;
}}

/* --- Всплывающие списки -----------------------------------------------

   Раскрывающийся список у QComboBox — отдельное окно, и вложенное
   правило вида QComboBox QAbstractItemView до него не доходит.
   Поэтому правило глобальное: оно достаёт и до списка микрофонов,
   и до подсказок в поле блока.

   combobox-popup: 0 заставляет Qt рисовать список средствами
   самого QComboBox — иначе по краям остаётся белая рамка. */

QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    outline: none;
    padding: 4px;
    selection-background-color: {FOCUS};
    selection-color: {ON_ACCENT};
}}

QAbstractItemView::item {{
    padding: 6px 8px;
    border-radius: 4px;
    min-height: 22px;
}}

QAbstractItemView::item:hover {{
    background-color: {HOVER};
    color: {TEXT};
}}

QAbstractItemView::item:selected {{
    background-color: {FOCUS};
    color: {ON_ACCENT};
}}

/* Меню сроков автостопа. Обычное меню Qt рисуется системными цветами:
   на тёмном фоне светлая полоса выглядит чужой, поэтому фон, рамка
   и подсветка строки заданы здесь. */

QMenu {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    color: {TEXT};
}}

QMenu::item {{
    padding: 6px 26px 6px 30px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {ACTIVE};
    color: {ACTIVE_TEXT};
}}

/* Отметка выбранного срока: слева от строки, как у галочек */
QMenu::indicator {{
    width: 16px;
    height: 16px;
    margin-left: 8px;
}}

QMenu::indicator:checked {{
    image: url("{CHECK_MARK}");
}}

/* --- Список записей --------------------------------------------------- */

QListWidget::item {{
    padding: 5px 8px;
    border-radius: 4px;
}}

QListWidget::item:selected {{
    background: {FOCUS};
    color: {ON_ACCENT};
}}

QListWidget::item:hover:!selected {{
    background: {HOVER};
}}

/* --- Кнопки ----------------------------------------------------------- */

QPushButton {{
    background-color: {BUTTON};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    font-weight: bold;
    font-size: 13px;
    padding: 6px 12px;
}}

QPushButton:hover {{
    background-color: {HOVER};
    border-color: {BORDER_STRONG};
}}

QPushButton:pressed {{
    background-color: {PRESSED};
}}

QPushButton:disabled {{
    background-color: {DISABLED_BG};
    color: {DISABLED_TEXT};
    border-color: {DISABLED_BORDER};
}}

/* Поле автостопа: слово «АвтоСтоп» написано в нём самом, значка
   рядом нет. Список сроков раскрывается по нажатию, поэтому это
   кнопка с меню, но выглядеть она должна полем, а не клавишей:
   отсюда обычное начертание и место справа под стрелку списка. */

QPushButton#autoStop {{
    color: {ON_ACCENT};
    font-weight: normal;
    padding: 6px 26px 6px 10px;
    text-align: left;
}}

/* Кнопка-переключатель зоны: нажата — значит зона раскрыта */
QPushButton:checked {{
    background-color: {ACTIVE};
    border-color: {FOCUS};
    color: {ACTIVE_TEXT};
}}

QPushButton:checked:hover {{
    background-color: {ACTIVE};
    border-color: {LIVE};
}}

/* --- Индикатор уровня -------------------------------------------------- */

QProgressBar {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
    color: {ON_ACCENT};
}}

QProgressBar::chunk {{
    background-color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 {FOCUS}, stop: 1 {LIVE});
    border-radius: 3px;
}}

/* --- Панель с текстом --------------------------------------------------- */

QFrame[panel="true"] {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* --- Таблица записей --------------------------------------------------- */

QTableWidget {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BACKGROUND};
    color: {TEXT};
}}

/* Шапка живёт отдельно от строк: в QTableWidget она и так не
   прокручивается, но ей нужны свои цвета, чтобы не сливалась
   с таблицей.

   Нижней черты нет намеренно: Qt рисует разделитель под шапкой
   сам, и вместе с border он читался как подчёркивание. Отличаем
   шапку от строк цветом и вертикальной линией между столбцами. */
QHeaderView::section {{
    background-color: {PANEL};
    color: {MUTED};
    border: none;
    border-right: 1px solid {SURFACE};
    padding: 8px 10px;
    font-weight: 600;
    text-align: left;
}}

QTableWidget::item {{
    padding: 4px 8px;
    border: none;
}}

QTableWidget::item:selected {{
    background: {FOCUS};
    color: {ON_ACCENT};
}}

QTableCornerButton::section {{
    background-color: {PANEL};
    border: none;
    border-bottom: 1px solid {BORDER};
}}

/* --- Полосы прокрутки --------------------------------------------------- */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {BORDER_STRONG};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 5px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {BORDER_STRONG};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- Диалоги и подсказки -------------------------------------------------- */

QMessageBox {{
    background: {BACKGROUND};
}}

QMessageBox QLabel {{
    color: {TEXT};
    background: transparent;
}}

/* Подсказки закругляем тем же радиусом, что и остальные края,
   иначе на тёмном фоне они выглядят чужеродными квадратами */
QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* --- Переключатели ----------------------------------------------------- */

QCheckBox {{
    color: {TEXT};
    font-size: 13px;
    spacing: 8px;
    padding: 2px 0;
}}

QCheckBox:disabled {{
    color: {DISABLED_TEXT};
}}

/* Галочка белая: на тёмном фоне голубой значок теряется,
   а белый читается сразу и выглядит как отметка «включено» */
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background-color: {SURFACE};
}}

QCheckBox::indicator:hover {{
    border-color: {FOCUS};
}}

QCheckBox::indicator:checked {{
    background-color: {FOCUS};
    border-color: {FOCUS};
    image: url("{CHECK_MARK}");
}}

QCheckBox::indicator:disabled {{
    background-color: {DISABLED_BG};
    border-color: {DISABLED_BORDER};
}}"""


def apply_property(widget: QWidget, name: str, value: bool) -> None:
    """Меняет свойство виджета и заставляет Qt перерисовать его.

    Без unpolish/polish Qt не перечитает таблицу стилей,
    и виджет останется старого вида.
    """
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def apply_locale(app: QApplication) -> None:
    """Переводит стандартные диалоги Qt на русский.

    Свои надписи живут в коде, а кнопки в сообщениях, поле выбора
    файла и заголовки окон Qt берёт из перевода операционной
    системы. Если язык системы английский, надписи получаются
    английские — это и случилось с окном выхода.

    Перевод нужно удержать: сборщик Qt освобождает QTranslator,
    на который никто не ссылается, и подписи снова станут
    английскими без единого предупреждения.
    """
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translator = QTranslator(app)

    if translator.load("qtbase_ru", folder):
        app.installTranslator(translator)
        app.locale_translator = translator


def apply_theme(app: QApplication) -> None:
    """Включает оформление на всё приложение.

    Галочка рисуется здесь, а не на уровне модуля: QPixmap требует
    уже созданного QApplication.
    """
    app.setStyle("Fusion")
    app.setStyleSheet(_stylesheet(check_mark_path()))
