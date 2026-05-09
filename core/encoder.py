"""Модуль кодирования файлов в видео."""

import cv2
import numpy as np
import os
import math
import subprocess
import tempfile
import shutil
import struct

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

# Предвычисленная таблица: индекс 0..15 -> RGB
_COLOR_TABLE = np.array([COLORS[f'{i:04b}'] for i in range(16)], dtype=np.uint8)


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

        # Предвычисление маркерного кадра (фон + углы)
        self._marker_frame = self._build_marker_frame()

    # ── предвычисления ─────────────────────────────────────

    def _build_marker_frame(self) -> np.ndarray:
        """Создаёт кадр с маркерами и фоном (переиспользуется для каждого кадра)."""
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        ms = self.marker_size
        w, h = self.width, self.height
        for x1, y1, x2, y2 in [
            (0, 0, ms, ms),
            (w - ms, 0, w, ms),
            (0, h - ms, ms, h),
            (w - ms, h - ms, w, h),
        ]:
            frame[y1:y2, x1:x2] = (255, 255, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 2)
        return frame

    # ── быстрые утилиты ─────────────────────────────────────

    @staticmethod
    def _data_to_blocks(data: bytes) -> np.ndarray:
        """Конвертирует байты в массив 4-битных индексов (0..15). Векторизовано."""
        arr = np.frombuffer(data, dtype=np.uint8)
        # Каждый байт -> два 4-битных nibble: старший и младший
        high = (arr >> 4) & 0x0F
        low = arr & 0x0F
        # Чередуем: high[0], low[0], high[1], low[1], ...
        nibbles = np.empty(len(arr) * 2, dtype=np.uint8)
        nibbles[0::2] = high
        nibbles[1::2] = low
        return nibbles

    def _render_frame(self, block_indices: np.ndarray) -> np.ndarray:
        """
        Рендерит кадр напрямую в numpy-массив без cv2.rectangle.
        block_indices — массив nibble-индексов (0..15).
        """
        frame = self._marker_frame.copy()

        bw = self.block_width
        bh = self.block_height
        sp = self.spacing
        ms = self.marker_size
        stride_x = bw + sp
        stride_y = bh + sp
        bx = self.blocks_x
        by = self.blocks_y

        n = len(block_indices)

        for idx in range(n):
            nibble = block_indices[idx]
            color = _COLOR_TABLE[nibble]

            row = idx // bx
            col = idx % bx

            # ── Основной регион ──────────────────────────
            if row < by:
                y1 = ms + row * stride_y
                x1 = ms + col * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color

            # ── Резерв 1 (сдвиг по X) ───────────────────
            rx = col + bx
            if rx < bx * 2 and row < by:
                y1 = ms + row * stride_y
                x1 = ms + rx * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color

            # ── Резерв 2 (сдвиг по Y) ───────────────────
            ry = row + by
            if col < bx and ry < by * 2:
                y1 = ms + ry * stride_y
                x1 = ms + col * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color

        return frame

    def _render_guard_frame(self) -> np.ndarray:
        """Рендерит защитный кадр (синие блоки)."""
        frame = self._marker_frame.copy()
        bw = self.block_width
        bh = self.block_height
        sp = self.spacing
        ms = self.marker_size
        stride_x = bw + sp
        stride_y = bh + sp
        color = np.array([255, 0, 0], dtype=np.uint8)

        for row in range(self.blocks_y * 2):
            for col in range(self.blocks_x * 2):
                y1 = ms + row * stride_y
                x1 = ms + col * stride_x
                if y1 + bh <= self.height - ms and x1 + bw <= self.width - ms:
                    frame[y1:y1 + bh, x1:x1 + bw] = color

        return frame

    def _write_opencv_video(self, frames: list[np.ndarray], output_file: str):
        """Записывает видео через OpenCV из списка numpy-кадров."""
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_file, fourcc, self.fps, (self.width, self.height))
        for frame in frames:
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

        # Блоки (numpy-массивы nibble-индексов)
        header_nibbles = self._data_to_blocks(header_bytes)
        data_nibbles = self._data_to_blocks(encrypted_data)
        eof_nibbles = self._data_to_blocks(self.eof_bytes)
        all_nibbles = np.concatenate([header_nibbles, data_nibbles, eof_nibbles])

        log(f"Всего блоков: {len(all_nibbles)}")

        # Кадры
        frames_needed = math.ceil(len(all_nibbles) / self.blocks_per_region) + 5
        data_frames = frames_needed - 5
        log(f"Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")

        # Рендерим кадры
        ffmpeg_path = find_ffmpeg()
        all_frames: list[np.ndarray] = []

        for frame_num in range(data_frames):
            start_idx = frame_num * self.blocks_per_region
            end_idx = min(start_idx + self.blocks_per_region, len(all_nibbles))
            frame_nibbles = all_nibbles[start_idx:end_idx]
            frame = self._render_frame(frame_nibbles)
            all_frames.append(frame)

            pct = int((frame_num + 1) / frames_needed * 70)
            if on_progress:
                on_progress(pct)
            if frame_num % 50 == 0:
                log(f"Кадр {frame_num + 1}/{data_frames}")

        # Защитные кадры
        guard_frame = self._render_guard_frame()
        for _ in range(5):
            all_frames.append(guard_frame.copy())

        log("Конвертация в MP4...")

        # Конвертация
        if ffmpeg_path:
            # Pipe-режим: пишем кадры напрямую в FFmpeg через stdin
            cmd = [
                ffmpeg_path,
                '-framerate', str(self.fps),
                '-f', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{self.width}x{self.height}',
                '-i', '-',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-an',
                '-movflags', '+faststart',
                '-y',
                output_file,
            ]
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for frame in all_frames:
                    proc.stdin.write(frame.tobytes())
                proc.stdin.close()
                proc.wait(timeout=120)
                if proc.returncode == 0:
                    log("FFmpeg конвертация успешна")
                else:
                    log("FFmpeg вернул ошибку, использую OpenCV...")
                    self._write_opencv_video(all_frames, output_file)
            except Exception as e:
                log(f"Ошибка FFmpeg: {e}, использую OpenCV...")
                self._write_opencv_video(all_frames, output_file)
        else:
            log("FFmpeg не найден, использую OpenCV...")
            self._write_opencv_video(all_frames, output_file)

        if on_progress:
            on_progress(100)

        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            log(f"Видео сохранено: {output_file}")
            log(f"Размер: {size / 1024 / 1024:.2f} MB | Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")
            return True

        log("Ошибка: выходной файл не создан")
        return False
