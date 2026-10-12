# Copyright (C) 2026 Михаил Бурдуковский
# SPDX-License-Identifier: GPL-3.0-only
#
# Полный текст лицензии — в файле LICENSE в корне проекта.

"""
Свой виджет: показывает, идёт ли запись.

Раньше состояние было размазано по окну: цвет кнопки, подпись на
кнопке, полоска уровня, таймер. По одному экрану не было видно,
пишет программа сейчас или нет. Виджет собирает всё это в одном
месте и добавляет главное — цветную точку и слова.

Состояния:
    idle        готов, точка серая
    recording   идёт запись, точка красная и мигает
    paused      пауза, точка жёлтая и не мигает
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.styles import (
    BORDER,
    BORDER_STRONG,
    BUTTON,
    DISABLED_BG,
    DISABLED_BORDER,
    DISABLED_TEXT,
    FOCUS,
    TEXT,
    apply_property,
)
from storage import format_duration

IDLE = "idle"
RECORDING = "recording"
PAUSED = "paused"

STATE_TEXT = {
    IDLE: "Готово",
    RECORDING: "Идёт запись",
    PAUSED: "Пауза",
}

# Мигание точки: достаточно спокойно, чтобы не раздражать
PULSE_MS = 500


class PulseButton(QPushButton):
    """Кнопка, которая плавно переливается во время работы.

    Сначала здесь была полоса с процентами, но она обещала ход
    процесса, которого не видно: Whisper молчит до конца первых
    тридцати секунд звука, поэтому проценты прыгали с нуля сразу
    на половину. Мягкая смена фона говорит только то, что верно
    наверняка — работа идёт.

    Рисуем сами, а не через таблицу стилей: подставлять цвет в
    стиль на каждом кадре слишком дорого, а надпись на кнопке
    при этом остаётся на месте.
    """

    STEP_MS = 25
    CYCLE = 60  # кадров на одно переливание туда-обратно

    # Насколько сильно фон уходит в акцентный цвет
    MIX = 0.45

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._base = QColor(BUTTON)
        self._accent = QColor(FOCUS)
        self._frame = 0
        self._share = 0.0
        self._pulsing = False
        self._hovered = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    def enterEvent(self, event) -> None:
        """Мышь над кнопкой: чуть светлее, как у остальных кнопок."""
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    # ------------------------------------------------------------ состояние

    def start_pulse(self) -> None:
        """Начать переливание."""
        if self._pulsing:
            return

        self._pulsing = True
        self._frame = 0
        self._timer.start(self.STEP_MS)
        self.update()

    def stop_pulse(self) -> None:
        """Прекратить переливание и вернуть обычный вид."""
        if not self._pulsing:
            return

        self._pulsing = False
        self._timer.stop()
        self._frame = 0
        self._share = 0.0
        self.update()

    @property
    def is_pulsing(self) -> bool:
        return self._pulsing

    # -------------------------------------------------------------- отрисовка

    def _advance(self) -> None:
        """Один кадр переливания: треугольная волна туда-обратно."""
        self._frame = (self._frame + 1) % (self.CYCLE * 2)

        # Разбегаемся на две половины, чтобы фон не останавливался
        # в крайних точках
        half = self.CYCLE
        if self._frame < half:
            share = self._frame / half
        else:
            share = (self.CYCLE * 2 - self._frame) / half

        self._share = share * self.MIX
        self.update()

    def _blend(self) -> QColor:
        """Текущий цвет фона: смесь обычного и акцентного."""
        share = getattr(self, "_share", 0.0)
        return QColor(
            round(self._base.red() + (self._accent.red() - self._base.red()) * share),
            round(
                self._base.green() + (self._accent.green() - self._base.green()) * share
            ),
            round(
                self._base.blue() + (self._accent.blue() - self._base.blue()) * share
            ),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        box = self.rect().adjusted(0, 0, -1, -1)

        if self.isEnabled():
            fill = self._blend()
            if self._hovered:
                # Под курсором чуть светлее — так наведены и
                # остальные кнопки приложения
                fill = fill.lighter(115)
            border = QColor(BORDER_STRONG if self._hovered else BORDER)
            ink = QColor(TEXT)
        else:
            fill, border = QColor(DISABLED_BG), QColor(DISABLED_BORDER)
            ink = QColor(DISABLED_TEXT)

        painter.setPen(border)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 6, 6)

        painter.setPen(ink)
        painter.drawText(
            box.adjusted(0, 0, 0, -1),
            Qt.AlignmentFlag.AlignCenter,
            self.text(),
        )


class RecordIndicator(QWidget):
    """Точка, надпись, таймер и уровень звука."""

    def __init__(self) -> None:
        super().__init__()

        self._state = IDLE
        self._pulse_on = False

        self._build()
        self._start_pulse_timer()
        self.set_state(IDLE)

    def _build(self) -> None:
        self.setProperty("indicator", True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        self.dot = QLabel()
        self.dot.setProperty("dot", IDLE)
        self.dot.setFixedSize(14, 14)
        top.addWidget(self.dot)

        self.status_label = QLabel("Готово")
        self.status_label.setProperty("state", IDLE)
        top.addWidget(self.status_label)

        top.addStretch(1)

        self.timer_label = QLabel("00:00")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.timer_label.setMinimumWidth(90)
        self.timer_label.setProperty("timer", True)
        top.addWidget(self.timer_label)

        outer.addLayout(top)

        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setFixedHeight(10)
        outer.addWidget(self.meter)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------- состояние

    def set_state(self, state: str) -> None:
        """Переключает вид: готово, идёт запись или пауза."""
        self._state = state

        apply_property(self.dot, "dot", state)
        apply_property(self.status_label, "state", state)
        self.status_label.setText(STATE_TEXT.get(state, STATE_TEXT[IDLE]))

        # Мигает только запись: пауза и покой стоят спокойно
        if state != RECORDING:
            self._set_pulse(False)

    @property
    def state(self) -> str:
        return self._state

    def set_level(self, level: float) -> None:
        """Уровень звука, доля от полной шкалы: 0..1."""
        self.meter.setValue(max(0, min(100, int(level * 100))))

    def set_elapsed(self, seconds: float) -> None:
        self.timer_label.setText(format_duration(seconds, pad=True))

    def reset(self) -> None:
        """Возвращает вид в исходное состояние."""
        self.set_level(0.0)
        self.set_elapsed(0.0)
        self.set_state(IDLE)

    # --------------------------------------------------------------- мигание

    def _start_pulse_timer(self) -> None:
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._flip_pulse)
        self._pulse_timer.start(PULSE_MS)

    def _flip_pulse(self) -> None:
        if self._state != RECORDING:
            return
        self._set_pulse(not self._pulse_on)

    def _set_pulse(self, on: bool) -> None:
        if on == self._pulse_on:
            return
        self._pulse_on = on
        apply_property(self.dot, "pulse", on)
