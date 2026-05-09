"""Модуль кодирования файлов в видео."""

import cv2
import numpy as np
import os
import math
import subprocess
import tempfile
import shutil

from core.crypto import xor_encrypt
from utils.ffmpeg_finder import find_ffmpeg

# 16 цветов: 4-битный код -> RGB
COLORS = {
    '0000': (255, 0, 0),
    '0001': (0, 255, 0),
    '0010': (0, 0, 255),
    '0011': (255, 255, 0),
    '0100': (255, 0, 255),
    '0101': (0, 255, 255),
    '0110': (255, 128, 0),
    '0111': (128, 0, 255),
    '1000': (0, 128, 128),
    '1001': (128, 128, 0),
    '1010': (128, 0, 128),
    '1011': (0, 128, 0),
    '1100': (128, 0, 0),
    '1101': (0, 0, 128),
    '1110': (192, 192, 192),
    '1111': (255, 255, 255),
}

EOF_MARKER = "\u2588" * 64  # "█" * 64


class YouTubeEncoder:
    def __init__(self, key: str | None = None):
        self.width = 1920
        self.height = 1080
        self.fps = 6

        self.block_height = 16
        self.block_width = 24
        self.spacing = 4
        self.marker_size = 80

        self.key = key
        self.use_encryption = key is not None

        self.colors = COLORS
        self.eof_bytes = EOF_MARKER.encode('utf-8')

        # Расчёт сетки
        self.blocks_x = (self.width - 2 * self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2 * self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y

    # ── внутренние утилиты ──────────────────────────────────

    @staticmethod
    def _data_to_blocks(data: bytes) -> list[str]:
        """Конвертирует байты в список 4-битных строк."""
        all_bits: list[str] = []
        for byte in data:
            for i in range(7, -1, -1):
                all_bits.append(str((byte >> i) & 1))
        while len(all_bits) % 4 != 0:
            all_bits.append('0')
        return [''.join(all_bits[i:i + 4]) for i in range(0, len(all_bits), 4)]

    def _bits_to_color(self, bits: str) -> tuple:
        """4-битная строка -> RGB-кортеж."""
        while len(bits) < 4:
            bits = '0' + bits
        return self.colors.get(bits, (255, 0, 0))

    def _draw_markers(self, frame: np.ndarray) -> np.ndarray:
        """Рисует маркеры по углам кадра."""
        ms = self.marker_size
        w, h = self.width, self.height
        for x1, y1, x2, y2 in [
            (0, 0, ms, ms),
            (w - ms, 0, w, ms),
            (0, h - ms, ms, h),
            (w - ms, h - ms, w, h),
        ]:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 255), -1)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 2)
        return frame

    def _draw_block(self, frame: np.ndarray, x: int, y: int, color: tuple) -> bool:
        """Рисует один блок в сетке."""
        ms = self.marker_size
        x1 = ms + x * (self.block_width + self.spacing)
        y1 = ms + y * (self.block_height + self.spacing)
        x2 = x1 + self.block_width
        y2 = y1 + self.block_height
        if x2 > self.width - ms or y2 > self.height - ms:
            return False
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 1)
        return True

    def _write_opencv_video(self, temp_dir: str, frames_needed: int, output_file: str):
        """Записывает видео через OpenCV (запасной вариант)."""
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_file, fourcc, self.fps, (self.width, self.height))
        for frame_num in range(frames_needed):
            frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
            frame = cv2.imread(frame_file)
            if frame is not None:
                out.write(frame)
        out.release()

    # ── основная логика ─────────────────────────────────────

    def encode(
        self,
        input_file: str,
        output_file: str,
        on_log: callable = None,
        on_progress: callable = None,
    ) -> bool:
        """
        Кодирует файл в видео.

        on_log(str)       — вызывается для каждого текстового сообщения
        on_progress(pct)  — вызывается с процентом прогресса (0..100)
        """
        def log(msg: str):
            if on_log:
                on_log(msg)

        # Читаем файл
        with open(input_file, 'rb') as f:
            data = f.read()

        log(f"Файл: {input_file}")
        log(f"Размер: {len(data)} байт")

        # Шифрование
        if self.use_encryption:
            encrypted_data = xor_encrypt(data, self.key)
            log("Данные зашифрованы")
        else:
            encrypted_data = data

        # Заголовок
        header = f"FILE:{os.path.basename(input_file)}:SIZE:{len(data)}|"
        header_bytes = header.encode('latin-1')
        log(f"Заголовок: {header}")

        # Блоки
        header_blocks = self._data_to_blocks(header_bytes)
        data_blocks = self._data_to_blocks(encrypted_data)
        eof_blocks = self._data_to_blocks(self.eof_bytes)
        all_blocks = header_blocks + data_blocks + eof_blocks

        log(f"Всего блоков: {len(all_blocks)}")

        # Кадры
        frames_needed = math.ceil(len(all_blocks) / self.blocks_per_region) + 5
        log(f"Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")

        temp_dir = tempfile.mkdtemp()

        try:
            # Генерация кадров данных
            data_frames = frames_needed - 5
            for frame_num in range(data_frames):
                frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                frame = self._draw_markers(frame)

                start_idx = frame_num * self.blocks_per_region
                end_idx = min(start_idx + self.blocks_per_region, len(all_blocks))
                frame_blocks = all_blocks[start_idx:end_idx]

                for idx, bits in enumerate(frame_blocks):
                    y = idx // self.blocks_x
                    x = idx % self.blocks_x
                    if y < self.blocks_y:
                        self._draw_block(frame, x, y, self._bits_to_color(bits))
                    # Резерв 1
                    rx = x + self.blocks_x
                    if rx < self.blocks_x * 2 and y < self.blocks_y:
                        self._draw_block(frame, rx, y, self._bits_to_color(bits))
                    # Резерв 2
                    ry = y + self.blocks_y
                    if x < self.blocks_x and ry < self.blocks_y * 2:
                        self._draw_block(frame, x, ry, self._bits_to_color(bits))

                cv2.imwrite(os.path.join(temp_dir, f"frame_{frame_num:05d}.png"), frame)

                pct = int((frame_num + 1) / frames_needed * 70)
                if on_progress:
                    on_progress(pct)
                if frame_num % 50 == 0:
                    log(f"Кадр {frame_num + 1}/{frames_needed}")

            # Защитные кадры
            for i in range(5):
                frame_num = data_frames + i
                frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                frame = self._draw_markers(frame)
                for y in range(self.blocks_y * 2):
                    for x in range(self.blocks_x * 2):
                        self._draw_block(frame, x, y, (255, 0, 0))
                cv2.imwrite(os.path.join(temp_dir, f"frame_{frame_num:05d}.png"), frame)

            log("Конвертация в MP4...")

            # FFmpeg
            ffmpeg_path = find_ffmpeg()
            if ffmpeg_path:
                cmd = [
                    ffmpeg_path,
                    '-framerate', str(self.fps),
                    '-i', os.path.join(temp_dir, 'frame_%05d.png'),
                    '-c:v', 'libx264',
                    '-preset', 'slow',
                    '-crf', '23',
                    '-pix_fmt', 'yuv420p',
                    '-an',
                    '-movflags', '+faststart',
                    '-y',
                    output_file,
                ]
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    log("FFmpeg конвертация успешна")
                except Exception as e:
                    log(f"Ошибка FFmpeg: {e}, использую OpenCV...")
                    self._write_opencv_video(temp_dir, frames_needed, output_file)
            else:
                log("FFmpeg не найден, использую OpenCV...")
                self._write_opencv_video(temp_dir, frames_needed, output_file)

            if on_progress:
                on_progress(100)

            if os.path.exists(output_file):
                size = os.path.getsize(output_file)
                log(f"Видео сохранено: {output_file}")
                log(f"Размер: {size / 1024 / 1024:.2f} MB | Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")
                return True

            log("Ошибка: выходной файл не создан")
            return False

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
