"""Скрипт сборки YouTube File Storage в .exe"""

import PyInstaller.__main__
import customtkinter
import imageio_ffmpeg
import os
import sys
import shutil

# ── Пути к данным, которые нужно упаковать ────────────────
ctk_path = os.path.dirname(customtkinter.__file__)
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_dir = os.path.dirname(ffmpeg_exe)

# ── Параметры сборки ──────────────────────────────────────
APP_NAME = "YouTube-File-Storage"
ENTRY_POINT = "main.py"

args = [
    ENTRY_POINT,
    f"--name={APP_NAME}",
    "--onefile",
    "--windowed",                         # без консольного окна
    "--clean",
    "--noconfirm",

    # CustomTkinter: иконки, шрифты, темы
    f"--add-data={ctk_path}{os.pathsep}customtkinter",

    # imageio-ffmpeg: бинарник ffmpeg
    f"--add-data={ffmpeg_dir}{os.pathsep}imageio_ffmpeg/binaries",

    # Скрытые импорты
    "--hidden-import=customtkinter",
    "--hidden-import=imageio_ffmpeg",
    "--hidden-import=core",
    "--hidden-import=core.crypto",
    "--hidden-import=core.encoder",
    "--hidden-import=core.decoder",
    "--hidden-import=utils",
    "--hidden-import=utils.ffmpeg_finder",
    "--hidden-import=utils.key_storage",
    "--hidden-import=ui",
    "--hidden-import=ui.app",
    "--hidden-import=ui.theme",
    "--hidden-import=ui.encode_frame",
    "--hidden-import=ui.decode_frame",
    "--hidden-import=ui.components",
    "--hidden-import=ui.components.file_picker",
    "--hidden-import=ui.components.key_input",
    "--hidden-import=ui.components.log_viewer",
    "--hidden-import=ui.components.console_panel",

    # Иконка приложения (если есть)
    # "--icon=icon.ico",
]

print("=" * 60)
print("  Сборка YouTube File Storage (.exe)")
print("=" * 60)
print(f"  CustomTkinter: {ctk_path}")
print(f"  FFmpeg:         {ffmpeg_exe}")
print(f"  Режим:          onefile + windowed")
print("=" * 60)
print()

PyInstaller.__main__.run(args)

# ── Результат ─────────────────────────────────────────────
exe_path = os.path.join("dist", f"{APP_NAME}.exe")
if os.path.exists(exe_path):
    size_mb = os.path.getsize(exe_path) / 1024 / 1024
    print()
    print("=" * 60)
    print(f"  Сборка завершена!")
    print(f"  Файл: {os.path.abspath(exe_path)}")
    print(f"  Размер: {size_mb:.1f} MB")
    print("=" * 60)
else:
    print()
    print("Ошибка: .exe не найден в dist/")
    sys.exit(1)
