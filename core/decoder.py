"""Модуль декодирования видео в файлы."""

import cv2
import numpy as np
import os
import re
import zipfile
import io

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

# Заголовок: FILE:name:SIZE:num|  (старый формат)
# или       FILE:name.zip:SIZE:num:ORIG:origname:ORIGSIZE:origsize|  (новый, ZIP)
# Из-за цветовых искажений при сжатии видео буквы могут искажаться
# (например O -> N), поэтому ищем гибким паттерном
HEADER_PATTERN = re.compile(
    r'FILE:([^:]+)\.z[i1]p:SIZE:(\d+):.{0,5}RIG:([^:]+):.{0,8}RIG[S5]IZE:(\d+)\|'
)
# Старый формат (без ZIP)
HEADER_PATTERN_OLD = re.compile(r'FILE:([^:]+):SIZE:(\d+)\|')

# Предвычисленные таблицы для быстрого декодирования
_COLOR_VALUES = np.array(list(COLORS.values()), dtype=np.int32)
_COLOR_KEYS = list(COLORS.keys())
# Таблица: индекс ближайшего цвета -> 4-битная строка
# Для быстрого поиска используем квантование: округляем RGB до 64 уровней
_QUANT_SHIFT = 5  # делим на 32, получаем 8 уровней на канал (16 уникальных бакетов)


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
        self.color_cache: dict[tuple, str] = {}

        # Расчёт сетки
        self.blocks_x = (self.width - 2 * self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2 * self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y

        self._precompute_coordinates()

        # Предвычисление таблицы квантованных цветов
        self._quant_lut = self._build_quant_lut()

    # ── предвычисления ─────────────────────────────────────

    def _precompute_coordinates(self):
        """Предвычисляет координаты центров блоков в виде numpy-массивов."""
        ms = self.marker_size
        bx, by = self.blocks_x, self.blocks_y
        stride_x = self.block_width + self.spacing
        stride_y = self.block_height + self.spacing

        cx_list = []
        cy_list = []
        for idx in range(self.blocks_per_region):
            row = idx // bx
            col = idx % bx
            if row < by:
                cx = ms + col * stride_x + self.block_width // 2
                cy = ms + row * stride_y + self.block_height // 2
                cx_list.append(cx)
                cy_list.append(cy)

        self._cx_arr = np.array(cx_list, dtype=np.int32)
        self._cy_arr = np.array(cy_list, dtype=np.int32)
        self._n_blocks = len(cx_list)

    def _build_quant_lut(self) -> dict:
        """Строит lookup-таблицу: квантованный цвет -> 4-битная строка."""
        lut: dict[tuple, str] = {}
        for bits, color in self.colors.items():
            q = (color[0] >> _QUANT_SHIFT, color[1] >> _QUANT_SHIFT, color[2] >> _QUANT_SHIFT)
            if q not in lut:
                lut[q] = bits
        return lut

    # ── быстрое декодирование ─────────────────────────────

    def _decode_frame_vectorized(self, frame: np.ndarray) -> list[str]:
        """Векторизованное декодирование одного кадра."""
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_NEAREST)

        # Читаем все пиксели центров блоков одним векторным доступом
        pixels = frame[self._cy_arr, self._cx_arr]  # shape: (n_blocks, 3)

        blocks: list[str] = []
        cache = self.color_cache
        quant_lut = self._quant_lut
        color_values = _COLOR_VALUES
        color_keys = _COLOR_KEYS

        for i in range(self._n_blocks):
            r, g, b = int(pixels[i, 0]), int(pixels[i, 1]), int(pixels[i, 2])
            color_key = (r, g, b)

            # Кэш
            if color_key in cache:
                blocks.append(cache[color_key])
                continue

            # Быстрый поиск через квантование
            q = (r >> _QUANT_SHIFT, g >> _QUANT_SHIFT, b >> _QUANT_SHIFT)
            if q in quant_lut:
                result = quant_lut[q]
                cache[color_key] = result
                blocks.append(result)
                continue

            # Fallback: точный поиск ближайшего
            color_arr = np.array([r, g, b], dtype=np.int32)
            distances = np.sum((color_values - color_arr) ** 2, axis=1)
            best_idx = int(np.argmin(distances))
            result = color_keys[best_idx]
            cache[color_key] = result
            blocks.append(result)

        return blocks

    @staticmethod
    def _blocks_to_bytes(blocks: list[str]) -> bytearray:
        """4-битные блоки -> байты. Оптимизировано через numpy."""
        # Конвертируем 4-битные строки в индексы 0..15
        nibble_arr = np.array([int(b, 2) for b in blocks], dtype=np.uint8)
        # Чередуем пары nibble'ов в байты
        n = len(nibble_arr)
        if n % 2 != 0:
            nibble_arr = np.append(nibble_arr, np.uint8(0))

        high = nibble_arr[0::2].astype(np.uint8) << 4
        low = nibble_arr[1::2]
        result = (high | low).astype(np.uint8)
        return bytearray(result.tobytes())

    @staticmethod
    def _find_eof(data: bytearray) -> int:
        """Поиск маркера конца. Использует bytes.find() — C-оптимизированный."""
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
        Поддерживает новый формат (ZIP-сжатие) и старый (без сжатия).

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

        all_blocks: list[str] = []
        frames_processed = 0
        last_progress_pct = -1

        for frame_num in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break
            frames_processed += 1

            all_blocks.extend(self._decode_frame_vectorized(frame))

            # Обновляем прогресс не чаще чем раз в 1%
            pct = int((frame_num + 1) / total_frames * 80)
            if pct != last_progress_pct:
                last_progress_pct = pct
                if on_progress:
                    on_progress(pct)
            if frame_num % 200 == 0:
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

        # Заголовок — сначала пробуем новый формат (ZIP-сжатие)
        data_str = bytes_data[:2000].decode('latin-1', errors='ignore')
        match_new = HEADER_PATTERN.search(data_str)

        if match_new:
            # Новый формат: ZIP-сжатые данные
            zip_base = match_new.group(1)
            zip_size = int(match_new.group(2))
            original_name = match_new.group(3)
            original_size = int(match_new.group(4))
            zip_name = zip_base + '.zip'
            log(f"Заголовок (ZIP): {zip_name}, размер: {zip_size} байт")
            log(f"Оригинал: {original_name}, размер: {original_size} байт")

            header_bytes = match_new.group(0).encode('latin-1')
            header_pos = bytes_data.find(header_bytes)

            if header_pos >= 0:
                encrypted_data = bytes_data[header_pos + len(header_bytes):
                                            header_pos + len(header_bytes) + zip_size]

                if self.key:
                    zip_data = xor_decrypt(encrypted_data, self.key)
                    log("Данные расшифрованы")
                else:
                    zip_data = encrypted_data
                    log("Данные без расшифровки (ключ не указан)")

                # Распаковка ZIP
                log("Распаковка ZIP...")
                try:
                    buf = io.BytesIO(zip_data)
                    with zipfile.ZipFile(buf, 'r') as zf:
                        # Извлекаем первый файл из архива
                        names = zf.namelist()
                        if not names:
                            log("Ошибка: ZIP-архив пуст")
                            return False

                        # Ищем файл с оригинальным именем
                        extract_name = original_name if original_name in names else names[0]
                        file_data = zf.read(extract_name)

                    log(f"ZIP распакован: {extract_name} ({len(file_data)} байт)")
                except zipfile.BadZipFile:
                    log("Ошибка: повреждённый ZIP-архив")
                    return False
                except Exception as e:
                    log(f"Ошибка распаковки ZIP: {e}")
                    return False

                del zip_data

                # Сохраняем оригинальный файл
                output_path = os.path.join(output_dir, original_name)
                base, ext = os.path.splitext(original_name)
                counter = 1
                while os.path.exists(output_path):
                    output_path = os.path.join(output_dir, f"{base}_{counter}{ext}")
                    counter += 1

                with open(output_path, 'wb') as f:
                    f.write(file_data)

                log(f"Файл восстановлен: {output_path}")
                log(f"Размер: {len(file_data)} байт")
                if len(file_data) == original_size:
                    log("Размер совпадает с оригиналом")
                else:
                    log(f"Размер не совпадает: {len(file_data)} != {original_size}")

                if on_progress:
                    on_progress(100)
                return True

        # Пробуем старый формат (без сжатия)
        match_old = HEADER_PATTERN_OLD.search(data_str)

        if match_old:
            filename = match_old.group(1)
            filesize = int(match_old.group(2))
            log(f"Заголовок (без сжатия): {filename}, размер: {filesize} байт")

            header_bytes = match_old.group(0).encode('latin-1')
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
