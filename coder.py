# youtube_storage_fixed.py — CLI-версия кодировщика/декодировщика
import cv2
import numpy as np
import os
import math
import subprocess
import sys
import re
import argparse

from core.crypto import xor_encrypt, xor_decrypt
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
EOF_BYTES = b'\xe2\x96\x88' * 64
HEADER_PATTERN = re.compile(r'FILE:([^:]+):SIZE:(\d+)\|')

# Предвычисленная таблица: индекс 0..15 -> RGB
_COLOR_TABLE = np.array([COLORS[f'{i:04b}'] for i in range(16)], dtype=np.uint8)

# Квантование для декодирования
_QUANT_SHIFT = 5


class YouTubeEncoder:
    def __init__(self, key=None):
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

        # Предвычисления
        self._marker_frame = self._build_marker_frame()
        self._guard_frame = self._build_guard_frame()
        self._precompute_block_coords()

        print("=" * 60)
        print("КОДИРОВЩИК YouTube (6 FPS)")
        print("=" * 60)
        print(f"Сетка: {self.blocks_x} x {self.blocks_y} блоков")
        print(f"Блоков на кадр: {self.blocks_per_frame} ({self.blocks_per_frame // 2} байт/кадр)")
        print(f"Шифрование: {'ВКЛ' if self.use_encryption else 'ВЫКЛ'}")

    # ── предвычисления ─────────────────────────────────────

    def _build_marker_frame(self):
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

    def _build_guard_frame(self):
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
    def _data_to_blocks(data):
        arr = np.frombuffer(data, dtype=np.uint8)
        high = (arr >> 4) & 0x0F
        low = arr & 0x0F
        nibbles = np.empty(len(arr) * 2, dtype=np.uint8)
        nibbles[0::2] = high
        nibbles[1::2] = low
        return nibbles

    def _render_frame(self, block_indices):
        frame = self._marker_frame.copy()
        bh = self.block_height
        bw = self.block_width
        n = min(len(block_indices), self._actual_blocks)
        colors = _COLOR_TABLE[block_indices[:n]]
        for i in range(n):
            frame[self._block_y1[i]:self._block_y1[i] + bh,
                  self._block_x1[i]:self._block_x1[i] + bw] = colors[i]
        return frame

    # ── основная логика ─────────────────────────────────────

    def encode(self, input_file, output_file):
        print("\nКОДИРОВАНИЕ ФАЙЛА")
        print("-" * 40)

        with open(input_file, 'rb') as f:
            data = f.read()

        print(f"Файл: {input_file}")
        print(f"Размер: {len(data)} байт")

        if self.use_encryption:
            encrypted_data = xor_encrypt(data, self.key)
            print("Данные зашифрованы")
        else:
            encrypted_data = data

        header = f"FILE:{os.path.basename(input_file)}:SIZE:{len(data)}|"
        header_bytes = header.encode('latin-1')
        print(f"Заголовок: {header}")

        header_nibbles = self._data_to_blocks(header_bytes)
        data_nibbles = self._data_to_blocks(encrypted_data)
        eof_nibbles = self._data_to_blocks(self.eof_bytes)
        all_nibbles = np.concatenate([header_nibbles, data_nibbles, eof_nibbles])

        del data, encrypted_data, header_nibbles, data_nibbles, eof_nibbles

        print(f"Всего блоков: {len(all_nibbles)}")

        bpf = self._actual_blocks
        frames_needed = math.ceil(len(all_nibbles) / bpf) + 5
        data_frames = frames_needed - 5
        print(f"Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")

        ffmpeg_path = find_ffmpeg()
        use_ffmpeg = bool(ffmpeg_path)

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
                print("FFmpeg pipe открыт")
            except Exception as e:
                print(f"Ошибка FFmpeg: {e}, использую OpenCV...")
                use_ffmpeg = False

        if not use_ffmpeg:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            cv_writer = cv2.VideoWriter(output_file, fourcc, self.fps, (self.width, self.height))
            print("OpenCV VideoWriter открыт")

        def write_frame(frame):
            if use_ffmpeg and proc:
                proc.stdin.write(frame.data.tobytes())
            elif cv_writer:
                cv_writer.write(frame)

        for frame_num in range(data_frames):
            start_idx = frame_num * bpf
            end_idx = min(start_idx + bpf, len(all_nibbles))
            frame_nibbles = all_nibbles[start_idx:end_idx]
            frame = self._render_frame(frame_nibbles)
            write_frame(frame)
            del frame

            if frame_num % 50 == 0:
                print(f"Кадр {frame_num + 1}/{data_frames}")

        for _ in range(5):
            write_frame(self._guard_frame)

        if proc:
            try:
                proc.stdin.close()
                proc.wait(timeout=300)
                if proc.returncode == 0:
                    print("FFmpeg конвертация успешна")
                else:
                    print(f"FFmpeg вернул код {proc.returncode}")
            except Exception as e:
                print(f"Ошибка при закрытии FFmpeg: {e}")

        if cv_writer:
            cv_writer.release()
            print("OpenCV VideoWriter закрыт")

        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            print(f"\nВидео сохранено: {output_file}")
            print(f"Размер: {size / 1024 / 1024:.2f} MB | Кадров: {frames_needed} | Длительность: {frames_needed / self.fps:.1f} сек")
            return True

        print("Ошибка: выходной файл не создан")
        return False


class YouTubeDecoder:
    def __init__(self, key=None):
        self.width = 1920
        self.height = 1080
        self.block_height = 16
        self.block_width = 24
        self.spacing = 1
        self.marker_size = 80

        self.key = key
        self.colors = COLORS

        self.blocks_x = (self.width - 2 * self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2 * self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_frame = self.blocks_x * self.blocks_y

        self._precompute_coordinates()
        self._build_rgb_lut()

        print("=" * 60)
        print("ДЕКОДЕР YouTube")
        print("=" * 60)
        print(f"Сетка: {self.blocks_x} x {self.blocks_y} блоков")
        print(f"Ключ: {'ЕСТЬ' if self.key else 'НЕТ'}")

    # ── предвычисления ─────────────────────────────────────

    def _precompute_coordinates(self):
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
        self._flat_lut = np.full(512, 0, dtype=np.uint8)
        for i, (bits, color) in enumerate(self.colors.items()):
            rq = color[0] >> _QUANT_SHIFT
            gq = color[1] >> _QUANT_SHIFT
            bq = color[2] >> _QUANT_SHIFT
            key = rq * 64 + gq * 8 + bq
            self._flat_lut[key] = i

    # ── быстрое декодирование ─────────────────────────────

    def _decode_frame_vectorized(self, frame):
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_NEAREST)

        pixels = frame[self._cy_arr, self._cx_arr]

        r_q = (pixels[:, 0].astype(np.int32) >> _QUANT_SHIFT)
        g_q = (pixels[:, 1].astype(np.int32) >> _QUANT_SHIFT)
        b_q = (pixels[:, 2].astype(np.int32) >> _QUANT_SHIFT)

        keys = r_q * 64 + g_q * 8 + b_q
        return self._flat_lut[keys]

    @staticmethod
    def _nibbles_to_bytes(nibble_indices):
        n = len(nibble_indices)
        if n % 2 != 0:
            nibble_indices = np.append(nibble_indices, np.uint8(0))

        high = nibble_indices[0::2].astype(np.uint8) << 4
        low = nibble_indices[1::2]
        result = (high | low).astype(np.uint8)
        return bytearray(result.tobytes())

    # ── основная логика ─────────────────────────────────────

    def decode(self, video_file, output_dir='.'):
        print("\nДЕКОДИРОВАНИЕ ВИДЕО")
        print("-" * 40)

        if not os.path.exists(video_file):
            print(f"Файл не найден: {video_file}")
            return False

        cap = cv2.VideoCapture(video_file)
        if not cap.isOpened():
            print("Не удалось открыть видео")
            return False

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        print(f"Кадров: {total_frames} | FPS: {fps:.1f} | Разрешение: {width}x{height}")

        estimated_nibbles = total_frames * self._n_blocks
        all_nibbles = np.empty(estimated_nibbles, dtype=np.uint8)
        nibble_count = 0

        for frame_num in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break

            frame_nibbles = self._decode_frame_vectorized(frame)
            n = len(frame_nibbles)
            all_nibbles[nibble_count:nibble_count + n] = frame_nibbles
            nibble_count += n

            if frame_num % 200 == 0:
                print(f"Прогресс: {frame_num + 1}/{total_frames}")

        cap.release()

        all_nibbles = all_nibbles[:nibble_count]
        print(f"Обработано блоков: {nibble_count}")

        bytes_data = self._nibbles_to_bytes(all_nibbles)
        del all_nibbles
        print(f"Получено байт: {len(bytes_data)}")

        eof_pos = bytes_data.find(EOF_BYTES)
        if eof_pos > 0:
            bytes_data = bytes_data[:eof_pos]
            print(f"Маркер конца найден на позиции {eof_pos}")
        else:
            print("Маркер конца не найден")

        data_str = bytes_data[:1000].decode('latin-1', errors='ignore')
        match = HEADER_PATTERN.search(data_str)

        if match:
            filename = match.group(1)
            filesize = int(match.group(2))
            print(f"Заголовок: {filename}, размер: {filesize} байт")

            header_bytes = match.group(0).encode('latin-1')
            header_pos = bytes_data.find(header_bytes)

            if header_pos >= 0:
                encrypted_data = bytes_data[header_pos + len(header_bytes):
                                            header_pos + len(header_bytes) + filesize]

                if self.key:
                    file_data = xor_decrypt(encrypted_data, self.key)
                    print("Данные расшифрованы")
                else:
                    file_data = encrypted_data
                    print("Данные без расшифровки")

                output_path = os.path.join(output_dir, filename)
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(output_path):
                    output_path = os.path.join(output_dir, f"{base}_{counter}{ext}")
                    counter += 1

                with open(output_path, 'wb') as f:
                    f.write(file_data)

                print(f"Файл восстановлен: {output_path}")
                print(f"Размер: {len(file_data)} байт")
                if len(file_data) == filesize:
                    print("Размер совпадает с оригиналом")
                else:
                    print(f"Размер не совпадает: {len(file_data)} != {filesize}")
                return True
        else:
            print("Заголовок не найден")

        output_path = os.path.join(output_dir, "decoded_data.bin")
        with open(output_path, 'wb') as f:
            f.write(bytes_data)
        print(f"Сырые данные сохранены: {output_path}")
        return False


def read_key_from_file(key_file='key.txt'):
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
                if key:
                    print(f"Ключ загружен из {key_file}")
                    return key
                else:
                    print(f"Файл {key_file} пуст")
        else:
            print(f"Файл {key_file} не найден, шифрование не используется")
    except Exception as e:
        print(f"Ошибка чтения ключа: {e}")
    return None


def save_key_to_file(key, key_file='key.txt'):
    try:
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        print(f"Ключ сохранён в {key_file}")
    except Exception as e:
        print(f"Ошибка сохранения ключа: {e}")


def main():
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description='YouTube File Storage (6 FPS) — кодирование/декодирование файлов в видео',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='''Примеры:
  python coder.py encode data.zip -o video.mp4 -k mypass123
  python coder.py decode video.mp4 -o ./restored -k mypass123
  python coder.py encode data.zip -o video.mp4
  python coder.py decode video.mp4

Если ключ (-k) не указан, программа попытается прочитать его из key.txt.
Если ключ (-k) указан, он будет сохранён в key.txt для дальнейшего использования.'''
        )

        subparsers = parser.add_subparsers(dest='command', help='Команда')

        enc = subparsers.add_parser('encode', help='Закодировать файл в видео')
        enc.add_argument('input', help='Путь до входного файла')
        enc.add_argument('-o', '--output', default='output.mp4', help='Название выходного видео (по умолчанию: output.mp4)')
        enc.add_argument('-k', '--key', default=None, help='Ключ шифрования (сохраняется в key.txt)')

        dec = subparsers.add_parser('decode', help='Декодировать видео в файл')
        dec.add_argument('input', help='Путь до видеофайла')
        dec.add_argument('-o', '--output', default='.', help='Папка для сохранения результата (по умолчанию: текущая папка)')
        dec.add_argument('-k', '--key', default=None, help='Ключ шифрования (сохраняется в key.txt)')

        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            return

        if args.key:
            key = args.key
            save_key_to_file(key)
        else:
            key = read_key_from_file()

        if args.command == 'encode':
            encoder = YouTubeEncoder(key)
            encoder.encode(args.input, args.output)
        elif args.command == 'decode':
            decoder = YouTubeDecoder(key)
            decoder.decode(args.input, args.output)
        return

    print()
    print("=" * 60)
    print("  YouTube File Storage — кодирование/декодирование")
    print("=" * 60)
    print()
    print("Выберите режим:")
    print("  1 — Кодировать файл в видео (encode)")
    print("  2 — Декодировать видео в файл (decode)")
    print()

    while True:
        choice = input("Введите номер режима (1 или 2): ").strip()
        if choice in ('1', '2'):
            break
        print("Введите 1 или 2")

    while True:
        input_file = input("Путь до входного файла: ").strip()
        if input_file and os.path.exists(input_file):
            break
        if input_file:
            print(f"Файл не найден: {input_file}")
        else:
            print("Путь не может быть пустым")

    if choice == '1':
        default_output = 'output.mp4'
        prompt_text = "Название выходного видео"
    else:
        default_output = '.'
        prompt_text = "Папка для сохранения результата"

    output_val = input(f"{prompt_text} [по умолчанию: {default_output}]: ").strip()
    if not output_val:
        output_val = default_output

    saved_key = read_key_from_file()
    if saved_key:
        print(f"Найден сохранённый ключ: {saved_key}")
        use_saved = input("Использовать этот ключ? (д/н) [по умолчанию: д]: ").strip().lower()
        if use_saved in ('', 'д', 'да', 'y', 'yes'):
            key = saved_key
        else:
            key = None
    else:
        key = None

    if key is None:
        key_input = input("Ключ шифрования (Enter — без шифрования): ").strip()
        if key_input:
            key = key_input
            save_key_to_file(key)
        else:
            key = None
            print("Шифрование отключено")

    if choice == '1':
        encoder = YouTubeEncoder(key)
        encoder.encode(input_file, output_val)
    else:
        decoder = YouTubeDecoder(key)
        decoder.decode(input_file, output_val)


if __name__ == "__main__":
    main()
