"""Модуль кодирования файлов в видео."""

import cv2
import numpy as np
import os
import math
import subprocess

from core.crypto import xor_encrypt
from utils.ffmpeg_finder import find_ffmpeg

# 16 цветов: 4-битный код -> RGB
# Яркая палитра, устойчивая к ±1 искажениям при H.264 сжатии (shift=5 квантование)
# Нижние 8: значения (48, 240) — насыщенные цвета (центры бакетов 1 и 7)
# Верхние 8: значения (80, 208) — более тёмные варианты (центры бакетов 2 и 6)
COLORS = {
    '0000': (48, 48, 48),       # тёмно-серый
    '0001': (240, 48, 48),      # красный
    '0010': (48, 240, 48),      # зелёный
    '0011': (240, 240, 48),     # жёлтый
    '0100': (48, 48, 240),      # синий
    '0101': (240, 48, 240),     # пурпурный
    '0110': (48, 240, 240),     # голубой
    '0111': (240, 240, 240),    # белый
    '1000': (80, 80, 80),       # серый
    '1001': (208, 80, 80),      # тёмно-красный
    '1010': (80, 208, 80),      # тёмно-зелёный
    '1011': (208, 208, 80),     # тёмно-жёлтый
    '1100': (80, 80, 208),      # тёмно-синий
    '1101': (208, 80, 208),     # тёмно-пурпурный
    '1110': (80, 208, 208),     # тёмно-голубой
    '1111': (208, 208, 208),    # светло-серый
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
        self.spacing = 1
        self.marker_size = 80

        self.key = key
        self.use_encryption = key is not None

        self.colors = COLORS
        self.eof_bytes = EOF_MARKER.encode('utf-8')

        # Расчёт сетки — каждый блок уникален, без дублирования
        self.blocks_x = (self.width - 2 * self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2 * self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_frame = self.blocks_x * self.blocks_y

        # Предвычисление маркерного кадра (фон + углы)
        self._marker_frame = self._build_marker_frame()
        # Предвычисление защитного кадра
        self._guard_frame = self._build_guard_frame()
        # Предвычисление координат блоков
        self._precompute_block_coords()

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
        color = np.array([48, 240, 48], dtype=np.uint8)  # цвет 0010 (зелёный)

        for row in range(self.blocks_y):
            for col in range(self.blocks_x):
                y1 = ms + row * stride_y
                x1 = ms + col * stride_x
                frame[y1:y1 + bh, x1:x1 + bw] = color
        return frame

    def _precompute_block_coords(self):
        """Предвычисляет координаты всех блоков для быстрого рендеринга.

        Каждый блок имеет ровно одну позицию в сетке.
        Координаты хранятся как Python-списки для максимальной скорости
        в горячем цикле рендеринга.
        """
        bw = self.block_width
        bh = self.block_height
        ms = self.marker_size
        stride_x = bw + self.spacing
        stride_y = bh + self.spacing

        y1_list = []
        x1_list = []
        for row in range(self.blocks_y):
            for col in range(self.blocks_x):
                y1 = ms + row * stride_y
                x1 = ms + col * stride_x
                y1_list.append(y1)
                x1_list.append(x1)

        self._block_y1 = y1_list
        self._block_x1 = x1_list
        self._actual_blocks = len(y1_list)

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
        """Рендерит кадр — каждый блок в своей уникальной позиции."""
        frame = self._marker_frame.copy()

        bh = self.block_height
        bw = self.block_width
        n = min(len(block_indices), self._actual_blocks)

        # Получаем цвета для всех nibble-индексов
        colors = _COLOR_TABLE[block_indices[:n]]

        # Python-цикл — самый быстрый вариант для slice-присваивания блоков
        for i in range(n):
            frame[self._block_y1[i]:self._block_y1[i] + bh,
                  self._block_x1[i]:self._block_x1[i] + bw] = colors[i]

        return frame

    def _write_frame_ffmpeg(self, proc: subprocess.Popen, frame: np.ndarray):
        """Пишет один кадр в stdin FFmpeg."""
        proc.stdin.write(frame.data.tobytes())

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
        Кадры стримятся напрямую в FFmpeg/OpenCV — не хранятся в памяти.

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

        # Освобождаем память исходных данных
        del data, encrypted_data, header_nibbles, data_nibbles, eof_nibbles

        log(f"Всего блоков: {len(all_nibbles)}")

        # Кадры — используем полную сетку без дублирования
        bpf = self._actual_blocks
        frames_needed = math.ceil(len(all_nibbles) / bpf) + 5
        data_frames = frames_needed - 5
        log(f"Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")
        log(f"Блоков на кадр: {bpf} ({bpf // 2} байт/кадр)")

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
                '-preset', 'ultrafast',
                '-crf', '18',
                '-pix_fmt', 'yuv444p',
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
            start_idx = frame_num * bpf
            end_idx = min(start_idx + bpf, len(all_nibbles))
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
