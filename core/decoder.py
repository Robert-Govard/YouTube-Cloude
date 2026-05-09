"""Модуль декодирования видео в файлы."""

import cv2
import numpy as np
import os
import re

from core.crypto import xor_decrypt

# 16 цветов (те же, что в encoder)
# Палитра устойчива к ±1 искажениям при H.264 сжатии
COLORS = {
    '0000': (48, 48, 48),
    '0001': (240, 48, 48),
    '0010': (48, 240, 48),
    '0011': (240, 240, 48),
    '0100': (48, 48, 240),
    '0101': (240, 48, 240),
    '0110': (48, 240, 240),
    '0111': (240, 240, 240),
    '1000': (112, 112, 112),
    '1001': (176, 112, 112),
    '1010': (112, 176, 112),
    '1011': (176, 176, 112),
    '1100': (112, 112, 176),
    '1101': (176, 112, 176),
    '1110': (112, 176, 176),
    '1111': (176, 176, 176),
}

EOF_BYTES = b'\xe2\x96\x88' * 64
HEADER_PATTERN = re.compile(r'FILE:([^:]+):SIZE:(\d+)\|')

# Предвычисленные таблицы для быстрого декодирования
_COLOR_VALUES = np.array(list(COLORS.values()), dtype=np.int32)
_COLOR_KEYS = list(COLORS.keys())

# Квантованная LUT: RGB -> nibble-индекс (0..15)
# Shift=5 даёт 16 уникальных бакетов без коллизий
_QUANT_SHIFT = 5


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

        # Расчёт сетки — каждый блок уникален, без дублирования (как в encoder)
        self.blocks_x = (self.width - 2 * self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2 * self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_frame = self.blocks_x * self.blocks_y

        self._precompute_coordinates()
        self._build_rgb_lut()

    # ── предвычисления ─────────────────────────────────────

    def _precompute_coordinates(self):
        """Предвычисляет координаты центров блоков."""
        ms = self.marker_size
        bw = self.block_width
        bh = self.block_height
        stride_x = bw + self.spacing
        stride_y = bh + self.spacing

        cx_list = []
        cy_list = []
        for row in range(self.blocks_y):
            for col in range(self.blocks_x):
                y1 = ms + row * stride_y
                x1 = ms + col * stride_x
                cx_list.append(x1 + bw // 2)
                cy_list.append(y1 + bh // 2)

        self._cx_arr = np.array(cx_list, dtype=np.int32)
        self._cy_arr = np.array(cy_list, dtype=np.int32)
        self._n_blocks = len(cx_list)

    def _build_rgb_lut(self):
        """Строит LUT: квантованный RGB -> nibble-индекс (0..15).

        Используем квантование с shift=5 (деление на 32),
        что даёт 16 уникальных бакетов — по одному на каждый цвет.
        """
        # Прямая LUT: (r_quant, g_quant, b_quant) -> nibble index
        quant_lut = {}
        for i, (bits, color) in enumerate(self.colors.items()):
            q = (color[0] >> _QUANT_SHIFT, color[1] >> _QUANT_SHIFT, color[2] >> _QUANT_SHIFT)
            if q not in quant_lut:
                quant_lut[q] = i

        self._quant_lut = quant_lut

        # Также строим полную LUT для быстрого векторизованного декодирования
        # Квантуем каждый канал до 8 уровней (0..7), комбинируем в ключ
        # key = r_q * 64 + g_q * 8 + b_q  (8*8*8 = 512 записей)
        self._flat_lut = np.full(512, 0, dtype=np.uint8)
        for i, (bits, color) in enumerate(self.colors.items()):
            rq = color[0] >> _QUANT_SHIFT
            gq = color[1] >> _QUANT_SHIFT
            bq = color[2] >> _QUANT_SHIFT
            key = rq * 64 + gq * 8 + bq
            self._flat_lut[key] = i

    # ── быстрое декодирование ─────────────────────────────

    def _decode_frame_vectorized(self, frame: np.ndarray) -> np.ndarray:
        """Векторизованное декодирование одного кадра.

        Возвращает массив nibble-индексов (0..15) вместо списка строк.
        """
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_NEAREST)

        # Читаем все пиксели центров блоков одним векторным доступом
        pixels = frame[self._cy_arr, self._cx_arr]  # shape: (n_blocks, 3)

        # Квантуем и маппим через LUT
        r_q = (pixels[:, 0].astype(np.int32) >> _QUANT_SHIFT)
        g_q = (pixels[:, 1].astype(np.int32) >> _QUANT_SHIFT)
        b_q = (pixels[:, 2].astype(np.int32) >> _QUANT_SHIFT)

        keys = r_q * 64 + g_q * 8 + b_q
        nibble_indices = self._flat_lut[keys]

        return nibble_indices

    @staticmethod
    def _nibbles_to_bytes(nibble_indices: np.ndarray) -> bytearray:
        """Массив nibble-индексов (0..15) -> байты. Полностью векторизовано."""
        n = len(nibble_indices)
        if n % 2 != 0:
            nibble_indices = np.append(nibble_indices, np.uint8(0))

        high = nibble_indices[0::2].astype(np.uint8) << 4
        low = nibble_indices[1::2]
        result = (high | low).astype(np.uint8)
        return bytearray(result.tobytes())

    @staticmethod
    def _find_eof(data: bytearray) -> int:
        """Поиск маркера конца."""
        return data.find(EOF_BYTES)

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

        # Собираем все nibble-индексы напрямую в numpy-массив
        # Предварительно оцениваем размер
        estimated_nibbles = total_frames * self._n_blocks
        all_nibbles = np.empty(estimated_nibbles, dtype=np.uint8)
        nibble_count = 0

        last_progress_pct = -1

        for frame_num in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break

            frame_nibbles = self._decode_frame_vectorized(frame)
            n = len(frame_nibbles)
            all_nibbles[nibble_count:nibble_count + n] = frame_nibbles
            nibble_count += n

            pct = int((frame_num + 1) / total_frames * 80)
            if pct != last_progress_pct:
                last_progress_pct = pct
                if on_progress:
                    on_progress(pct)
            if frame_num % 200 == 0:
                log(f"Прогресс: {frame_num + 1}/{total_frames}")

        cap.release()

        all_nibbles = all_nibbles[:nibble_count]
        log(f"Обработано блоков: {nibble_count}")

        # Конвертация nibbles -> байты (векторизовано)
        bytes_data = self._nibbles_to_bytes(all_nibbles)
        del all_nibbles
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
