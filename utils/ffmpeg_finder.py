"""Поиск FFmpeg: системный или из пакета imageio-ffmpeg."""

import os
import subprocess


def find_ffmpeg() -> str | None:
    """
    Возвращает путь к FFmpeg или None.

    Порядок поиска:
    1. Системный ffmpeg в PATH
    2. imageio-ffmpeg (pip-пакет)
    """
    # Системный FFmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return 'ffmpeg'
    except Exception:
        pass

    # imageio-ffmpeg
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.isfile(ffmpeg_exe):
            return ffmpeg_exe
    except Exception:
        pass

    return None
