"""Модуль кодирования файлов в видео."""

import cv2
import numpy as np
import os
import math
import subprocess
import zipfile
import io

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


def _zip_compress(input_file: str) -> tuple[bytes, str, int]:
    """
    Сжимает файл в ZIP-архив в памяти.
    Возвращает (zip_data, original_filename, original_size).
    """
    original_name = os.path.basename(input_file)
    original_size = os.path.getsize(input_file)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(input_file, original_name)
    zip_data = buf.getvalue()

    return zip_data, original_name, original_size


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
        # Предвычисление защитного кадра
        self._guard_frame = self._build_guard_frame()

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

    def _build_guard_frame(self) -> np.ndarray:
        """Создаёт защитный кадр (синие блоки)."""
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

    # ── быстрые утилиты ─────────────────────────────────────

    @staticmethod
    def _data_to_blocks(data: bytes) -> np.ndarray:
        """Конвертирует байты в массив 4-битных индексов (0..15). Векторизовано."""
        arr = np.frombuffer(data, dtype=np.uint8)
        high = (arr >> 4) & 0x0F
        low = arr & 0x0F
        nibbles = np.empty(len(arr) * 2, dtype=np.uint8)
        nibbles[0::2] = high
        nibbles[1::2] = low
        return nibbles

    def _render_frame(self, block_indices: np.ndarray) -> np.ndarray:
        """Рендерит кадр напрямую в numpy-массив без cv2.rectangle."""
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

            if row < by:
                y1 = ms + row * stride_y
                x1 = ms + col * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color

            rx = col + bx
            if rx < bx * 2 and row < by:
                y1 = ms + row * stride_y
                x1 = ms + rx * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color

            ry = row + by
            if col < bx and ry < by * 2:
                y1 = ms + ry * stride_y
                x1 = ms + col * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color

        return frame

    def _write_frame_ffmpeg(self, proc: subprocess.Popen, frame: np.ndarray):
        """Пишет один кадр в stdin FFmpeg."""
        proc.stdin.write(frame.tobytes())

    def _write_frame_opencv(self, writer: cv2.VideoWriter, frame: np.ndarray):
        """Пишет один кадр в OpenCV VideoWriter."""
        writer.write(frame)

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
        Сначала сжимает файл в ZIP, затем кодирует ZIP в видео.
        Кадры стримятся напрямую в FFmpeg/OpenCV — не хранятся в памяти.

        on_log(str)       — вызывается для каждого текстового сообщения
        on_progress(pct)  — вызывается с процентом прогресса (0..100)
        """
        def log(msg: str):
            if on_log:
                on_log(msg)

        # ── Сжатие в ZIP ────────────────────────────────────
        log(f"Сжатие в ZIP: {input_file}")
        zip_data, original_name, original_size = _zip_compress(input_file)

        zip_name = original_name + '.zip'
        zip_size = len(zip_data)
        ratio = (1 - zip_size / original_size) * 100 if original_size > 0 else 0
        log(f"Оригинал: {original_size} байт | ZIP: {zip_size} байт | Сжатие: {ratio:.1f}%")

        # Шифрование ZIP-данных
        if self.use_encryption:
            encrypted_data = xor_encrypt(zip_data, self.key)
            log("Данные зашифрованы")
        else:
            encrypted_data = zip_data

        del zip_data

        # Заголовок: хранит имя ZIP-файла и размер зашифрованных данных
        header = f"FILE:{zip_name}:SIZE:{len(encrypted_data)}:ORIG:{original_name}:ORIGSIZE:{original_size}|"
        header_bytes = header.encode('latin-1')
        log(f"Заголовок: {header}")

        # Блоки (numpy-массивы nibble-индексов)
        header_nibbles = self._data_to_blocks(header_bytes)
        data_nibbles = self._data_to_blocks(encrypted_data)
        eof_nibbles = self._data_to_blocks(self.eof_bytes)
        all_nibbles = np.concatenate([header_nibbles, data_nibbles, eof_nibbles])

        # Освобождаем память
        del encrypted_data, header_nibbles, data_nibbles, eof_nibbles

        log(f"Всего блоков: {len(all_nibbles)}")

        # Кадры
        frames_needed = math.ceil(len(all_nibbles) / self.blocks_per_region) + 5
        data_frames = frames_needed - 5
        log(f"Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")

        # Определяем способ записи
        ffmpeg_path = find_ffmpeg()
        use_ffmpeg = bool(ffmpeg_path)

        # Открываем выходной поток
        proc = None
        cv_writer = None

        if use_ffmpeg:
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
                log("FFmpeg pipe открыт")
            except Exception as e:
                log(f"Ошибка FFmpeg: {e}, использую OpenCV...")
                use_ffmpeg = False

        if not use_ffmpeg:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            cv_writer = cv2.VideoWriter(output_file, fourcc, self.fps, (self.width, self.height))
            log("OpenCV VideoWriter открыт")

        def write_frame(frame: np.ndarray):
            if use_ffmpeg and proc:
                self._write_frame_ffmpeg(proc, frame)
            elif cv_writer:
                self._write_frame_opencv(cv_writer, frame)

        # ── Стримим кадры данных ────────────────────────────
        for frame_num in range(data_frames):
            start_idx = frame_num * self.blocks_per_region
            end_idx = min(start_idx + self.blocks_per_region, len(all_nibbles))
            frame_nibbles = all_nibbles[start_idx:end_idx]
            frame = self._render_frame(frame_nibbles)
            write_frame(frame)
            del frame

            pct = int((frame_num + 1) / frames_needed * 70)
            if on_progress:
                on_progress(pct)
            if frame_num % 50 == 0:
                log(f"Кадр {frame_num + 1}/{data_frames}")

        # ── Защитные кадры ──────────────────────────────────
        for _ in range(5):
            write_frame(self._guard_frame)

        # Закрываем выходной поток
        if proc:
            try:
                proc.stdin.close()
                proc.wait(timeout=300)
                if proc.returncode == 0:
                    log("FFmpeg конвертация успешна")
                else:
                    log(f"FFmpeg вернул код {proc.returncode}")
            except Exception as e:
                log(f"Ошибка при закрытии FFmpeg: {e}")

        if cv_writer:
            cv_writer.release()
            log("OpenCV VideoWriter закрыт")

        if on_progress:
            on_progress(100)

        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            log(f"Видео сохранено: {output_file}")
            log(f"Размер: {size / 1024 / 1024:.2f} MB | Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")
            return True

        log("Ошибка: выходной файл не создан")
        return False
