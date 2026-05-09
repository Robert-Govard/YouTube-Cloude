"""Модуль декодирования видео в файлы."""

import cv2
import numpy as np
import os
import re

from core.crypto import xor_decrypt

# 16 цветов (те же, что в encoder)
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

EOF_BYTES = b'\xe2\x96\x88' * 64
HEADER_PATTERN = re.compile(r'FILE:([^:]+):SIZE:(\d+)\|')


class YouTubeDecoder:
    def __init__(self, key: str | None = None):
        self.width = 1920
        self.height = 1080
        self.block_height = 16
        self.block_width = 24
        self.spacing = 4
        self.marker_size = 80

        self.key = key

        self.colors = COLORS
        self.color_values = np.array(list(self.colors.values()), dtype=np.int32)
        self.color_keys = list(self.colors.keys())
        self.color_cache: dict[tuple, str] = {}
        self.cache_hits = 0
        self.cache_misses = 0

        # Расчёт сетки
        self.blocks_x = (self.width - 2 * self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2 * self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y

        self._precompute_coordinates()

    # ── внутренние утилиты ──────────────────────────────────

    def _precompute_coordinates(self):
        """Предвычисляет координаты центров блоков."""
        ms = self.marker_size
        self.block_coords: list[tuple[int, int]] = []
        for idx in range(self.blocks_per_region):
            y = idx // self.blocks_x
            x = idx % self.blocks_x
            if y < self.blocks_y:
                cx = ms + x * (self.block_width + self.spacing) + self.block_width // 2
                cy = ms + y * (self.block_height + self.spacing) + self.block_height // 2
                self.block_coords.append((cx, cy))

    def _color_to_bits(self, color: tuple) -> str:
        """Оптимизированный поиск ближайшего цвета."""
        color_key = (color[0], color[1], color[2])
        if color_key in self.color_cache:
            self.cache_hits += 1
            return self.color_cache[color_key]

        self.cache_misses += 1

        # Быстрая проверка на синий фон
        if color[0] > 200 and color[1] < 50 and color[2] < 50:
            self.color_cache[color_key] = '0000'
            return '0000'

        color_arr = np.array([color[0], color[1], color[2]], dtype=np.int32)
        distances = np.sum((self.color_values - color_arr) ** 2, axis=1)
        best_idx = int(np.argmin(distances))
        result = self.color_keys[best_idx]
        self.color_cache[color_key] = result
        return result

    def _decode_frame(self, frame: np.ndarray) -> list[str]:
        """Декодирует один кадр в список 4-битных строк."""
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_NEAREST)
        h, w = frame.shape[:2]
        blocks: list[str] = []
        for cx, cy in self.block_coords:
            if cx < w and cy < h:
                blocks.append(self._color_to_bits(frame[cy, cx]))
            else:
                blocks.append('0000')
        return blocks

    @staticmethod
    def _blocks_to_bytes(blocks: list[str]) -> bytearray:
        """4-битные блоки -> байты."""
        all_bits = ''.join(blocks)
        result = bytearray()
        for i in range(0, len(all_bits) - 7, 8):
            byte_str = all_bits[i:i + 8]
            if len(byte_str) == 8:
                try:
                    result.append(int(byte_str, 2))
                except ValueError:
                    result.append(0)
        return result

    @staticmethod
    def _find_eof(data: bytearray) -> int:
        """Поиск маркера конца в данных. Возвращает позицию или -1."""
        for i in range(len(data) - len(EOF_BYTES)):
            if data[i:i + len(EOF_BYTES)] == EOF_BYTES:
                return i
        return -1

    # ── основная логика ─────────────────────────────────────

    def decode(
        self,
        video_file: str,
        output_dir: str = '.',
        on_log: callable = None,
        on_progress: callable = None,
    ) -> bool:
        """
        Декодирует видео в файл.

        on_log(str)       — вызывается для каждого текстового сообщения
        on_progress(pct)  — вызывается с процентом прогресса (0..100)
        """
        def log(msg: str):
            if on_log:
                on_log(msg)

        if not os.path.exists(video_file):
            log(f"Файл не найден: {video_file}")
            return False

        cap = cv2.VideoCapture(video_file)
        if not cap.isOpened():
            log("Не удалось открыть видео")
            return False

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        log(f"Кадров: {total_frames} | FPS: {fps:.1f} | Разрешение: {width}x{height}")

        # Сброс кэша
        self.color_cache.clear()
        self.cache_hits = 0
        self.cache_misses = 0

        all_blocks: list[str] = []
        frames_processed = 0

        for frame_num in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break
            frames_processed += 1

            all_blocks.extend(self._decode_frame(frame))

            pct = int((frame_num + 1) / total_frames * 80)
            if on_progress:
                on_progress(pct)
            if frame_num % 100 == 0:
                log(f"Прогресс: {frame_num + 1}/{total_frames}")

        cap.release()

        log(f"Обработано кадров: {frames_processed}, блоков: {len(all_blocks)}")

        # Конвертация
        bytes_data = self._blocks_to_bytes(all_blocks)
        log(f"Получено байт: {len(bytes_data)}")

        # Маркер конца
        eof_pos = self._find_eof(bytes_data)
        if eof_pos > 0:
            bytes_data = bytes_data[:eof_pos]
            log(f"Маркер конца найден на позиции {eof_pos}")
        else:
            log("Маркер конца не найден")

        # Заголовок
        data_str = bytes_data[:1000].decode('latin-1', errors='ignore')
        match = HEADER_PATTERN.search(data_str)

        if match:
            filename = match.group(1)
            filesize = int(match.group(2))
            log(f"Заголовок: {filename}, размер: {filesize} байт")

            header_bytes = match.group(0).encode('latin-1')
            header_pos = bytes_data.find(header_bytes)

            if header_pos >= 0:
                encrypted_data = bytes_data[header_pos + len(header_bytes):
                                            header_pos + len(header_bytes) + filesize]

                if self.key:
                    file_data = xor_decrypt(encrypted_data, self.key)
                    log("Данные расшифрованы")
                else:
                    file_data = encrypted_data
                    log("Данные без расшифровки (ключ не указан)")

                # Сохраняем файл
                output_path = os.path.join(output_dir, filename)
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(output_path):
                    output_path = os.path.join(output_dir, f"{base}_{counter}{ext}")
                    counter += 1

                with open(output_path, 'wb') as f:
                    f.write(file_data)

                log(f"Файл восстановлен: {output_path}")
                log(f"Размер: {len(file_data)} байт")
                if len(file_data) == filesize:
                    log("Размер совпадает с оригиналом")
                else:
                    log(f"Размер не совпадает: {len(file_data)} != {filesize}")

                if on_progress:
                    on_progress(100)
                return True
        else:
            log("Заголовок не найден")

        # Fallback — сохраняем сырые данные
        output_path = os.path.join(output_dir, "decoded_data.bin")
        with open(output_path, 'wb') as f:
            f.write(bytes_data)
        log(f"Сырые данные сохранены: {output_path}")

        if on_progress:
            on_progress(100)
        return False
