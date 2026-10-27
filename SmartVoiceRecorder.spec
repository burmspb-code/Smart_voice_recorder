# -*- mode: python ; coding: utf-8 -*-
#
# Сборка одного файла .exe: python -m PyInstaller SmartVoiceRecorder.spec
#
# Почему файлы перечислены вручную, а не «сами нашлись»:
#
# * faster_whisper/assets/silero_vad_v6.onnx — не модуль, а модель для
#   onnxruntime. Без неё распознавание падает на vad_filter=True
#   с NO_SUCHFILE. PyInstaller такие файлы не подхватывает сам.
# * src/assets — иконки интерфейса, читаются из папки пакета.
# * src/assets/icon.ico — значок самого .exe. Без него PyInstaller
#   вшивает свой, поэтому в Проводнике и на панели задач был чужой
#   значок. Пересобрать из icon.png: python make_icon.py
#
# Модель распознавания (1,5 ГБ) сюда НЕ входит: её приложение
# скачивает само при первом запуске.

import os

import faster_whisper

assets = os.path.join(os.path.dirname(faster_whisper.__file__), 'assets')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/assets', 'src/assets'),
        (assets, 'faster_whisper/assets'),
    ],
    hiddenimports=['sounddevice', 'numpy', 'faster_whisper', 'av', 'onnxruntime'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['soundfile'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SmartVoiceRecorder',
    icon='src/assets/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # сжатие ломает onnxruntime и ctranslate2
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # приложение оконное, консоль пользователю не нужна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
