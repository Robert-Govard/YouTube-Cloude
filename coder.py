# youtube_storage_fixed.py
import cv2
import numpy as np
import os
import math
import subprocess
import tempfile
import shutil
import sys
import re
import hashlib
import argparse
import zipfile
import io
from collections import Counter

class YouTubeEncoder:
    def __init__(self, key=None):
        self.width = 1920
        self.height = 1080
        self.fps = 6  # ИЗМЕНЕНО: теперь 6 кадров в секунду
        
        # Параметры
        self.block_height = 16
        self.block_width = 24
        self.spacing = 4
        
        # Ключ шифрования
        self.key = key
        self.use_encryption = key is not None
        
        # 16 цветов
        self.colors = {
            '0000': (255, 0, 0),      # Ярко-синий
            '0001': (0, 255, 0),      # Ярко-зеленый
            '0010': (0, 0, 255),      # Ярко-красный
            '0011': (255, 255, 0),    # Желтый
            '0100': (255, 0, 255),    # Пурпурный
            '0101': (0, 255, 255),    # Голубой
            '0110': (255, 128, 0),    # Оранжевый
            '0111': (128, 0, 255),    # Фиолетовый
            '1000': (0, 128, 128),    # Бирюзовый
            '1001': (128, 128, 0),    # Оливковый
            '1010': (128, 0, 128),    # Темно-пурпурный
            '1011': (0, 128, 0),      # Темно-зеленый
            '1100': (128, 0, 0),      # Бордовый
            '1101': (0, 0, 128),      # Темно-синий
            '1110': (192, 192, 192),  # Светло-серый
            '1111': (255, 255, 255)   # Белый
        }
        
        # Маркеры по углам
        self.marker_size = 80
        
        # Расчет сетки
        self.blocks_x = (self.width - 2*self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2*self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y
        self.blocks_per_frame = self.blocks_per_region * 3
        
        # Маркер конца
        self.eof_marker = "█" * 64
        self.eof_bytes = self.eof_marker.encode('utf-8')
        
        print("="*60)
        print("🎬 КОДИРОВЩИК YouTube (6 FPS)")
        print("="*60)
        print(f"📊 Сетка: {self.blocks_x} x {self.blocks_y} блоков на регион")
        print(f"🎞️  FPS: {self.fps}")
        print(f"🔐 Шифрование: {'ВКЛ' if self.use_encryption else 'ВЫКЛ'}")
    
    def _find_ffmpeg(self):
        """Ищет FFmpeg: сначала в системе, затем в imageio-ffmpeg"""
        # Проверяем системный FFmpeg
        try:
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            return 'ffmpeg'
        except Exception:
            pass
        
        # Проверяем imageio-ffmpeg
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            if os.path.isfile(ffmpeg_exe):
                print(f"📦 FFmpeg найден через imageio-ffmpeg: {ffmpeg_exe}")
                return ffmpeg_exe
        except Exception:
            pass
        
        return None
    
    def _write_opencv_video(self, temp_dir, frames_needed, output_file):
        """Записывает видео через OpenCV (запасной вариант)"""
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_file, fourcc, self.fps, (self.width, self.height))
        
        for frame_num in range(frames_needed):
            frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
            frame = cv2.imread(frame_file)
            if frame is not None:
                out.write(frame)
        out.release()
    
    def _encrypt_data(self, data):
        """XOR шифрование с ключом"""
        if not self.use_encryption:
            return data
        
        key_bytes = self.key.encode()
        result = bytearray()
        
        for i, byte in enumerate(data):
            key_byte = key_bytes[i % len(key_bytes)]
            result.append(byte ^ key_byte)
        
        return result
    
    def _draw_markers(self, frame):
        """Рисует маркеры по углам"""
        cv2.rectangle(frame, (0, 0), (self.marker_size, self.marker_size), (255, 255, 255), -1)
        cv2.rectangle(frame, (self.width-self.marker_size, 0), (self.width, self.marker_size), (255, 255, 255), -1)
        cv2.rectangle(frame, (0, self.height-self.marker_size), (self.marker_size, self.height), (255, 255, 255), -1)
        cv2.rectangle(frame, (self.width-self.marker_size, self.height-self.marker_size), (self.width, self.height), (255, 255, 255), -1)
        
        cv2.rectangle(frame, (0, 0), (self.marker_size, self.marker_size), (0, 0, 0), 2)
        cv2.rectangle(frame, (self.width-self.marker_size, 0), (self.width, self.marker_size), (0, 0, 0), 2)
        cv2.rectangle(frame, (0, self.height-self.marker_size), (self.marker_size, self.height), (0, 0, 0), 2)
        cv2.rectangle(frame, (self.width-self.marker_size, self.height-self.marker_size), (self.width, self.height), (0, 0, 0), 2)
        
        return frame
    
    def _draw_block(self, frame, x, y, color):
        """Рисует один блок"""
        x1 = self.marker_size + x * (self.block_width + self.spacing)
        y1 = self.marker_size + y * (self.block_height + self.spacing)
        x2 = x1 + self.block_width
        y2 = y1 + self.block_height
        
        if x2 > self.width - self.marker_size or y2 > self.height - self.marker_size:
            return False
        
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 1)
        return True
    
    def _bits_to_color(self, bits):
        """4 бита -> цвет"""
        while len(bits) < 4:
            bits = '0' + bits
        return self.colors.get(bits, (255, 0, 0))
    
    def _data_to_blocks(self, data):
        """Конвертирует данные в 4-битные блоки"""
        all_bits = []
        for byte in data:
            for i in range(7, -1, -1):
                all_bits.append(str((byte >> i) & 1))
        
        while len(all_bits) % 4 != 0:
            all_bits.append('0')
        
        blocks = [''.join(all_bits[i:i+4]) for i in range(0, len(all_bits), 4)]
        return blocks
    
    def encode(self, input_file, output_file):
        """Кодирует файл в видео с опциональным шифрованием (с предварительным ZIP-сжатием)"""
        
        print("\n📤 КОДИРОВАНИЕ ФАЙЛА")
        print("-" * 40)
        
        # Сжатие в ZIP
        original_name = os.path.basename(input_file)
        original_size = os.path.getsize(input_file)
        print(f"📄 Файл: {input_file}")
        print(f"📦 Размер: {original_size} байт")
        
        print(f"🗜️  Сжатие в ZIP...")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(input_file, original_name)
        zip_data = buf.getvalue()
        
        zip_name = original_name + '.zip'
        zip_size = len(zip_data)
        ratio = (1 - zip_size / original_size) * 100 if original_size > 0 else 0
        print(f"📦 ZIP: {zip_size} байт | Сжатие: {ratio:.1f}%")
        
        # Шифруем ZIP-данные если нужно
        if self.use_encryption:
            encrypted_data = self._encrypt_data(zip_data)
            print(f"🔐 Данные зашифрованы")
        else:
            encrypted_data = zip_data
        
        del zip_data
        
        # Создаем заголовок (с информацией об оригинале)
        header = f"FILE:{zip_name}:SIZE:{len(encrypted_data)}:ORIG:{original_name}:ORIGSIZE:{original_size}|"
        header_bytes = header.encode('latin-1')
        print(f"📋 Заголовок: {header}")
        
        # Конвертируем в блоки
        header_blocks = self._data_to_blocks(header_bytes)
        data_blocks = self._data_to_blocks(encrypted_data)
        eof_blocks = self._data_to_blocks(self.eof_bytes)
        all_blocks = header_blocks + data_blocks + eof_blocks
        
        print(f"🎨 Всего блоков: {len(all_blocks)}")
        print(f"🏁 Маркер конца: {len(eof_blocks)} блоков")
        
        # Рассчитываем количество кадров
        frames_needed = math.ceil(len(all_blocks) / self.blocks_per_region)
        # Добавляем 5 защитных кадров
        frames_needed += 5
        print(f"🎬 Требуется кадров: {frames_needed}")
        print(f"⏱️  Длительность видео: {frames_needed/self.fps:.1f} сек")
        
        # Создаем временную папку
        temp_dir = tempfile.mkdtemp()
        print(f"📁 Временная папка: {temp_dir}")
        
        # Создаем кадры
        for frame_num in range(frames_needed - 5):
            print(f"\n🖼️  Кадр {frame_num + 1}/{frames_needed}")
            
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = self._draw_markers(frame)
            
            start_idx = frame_num * self.blocks_per_region
            end_idx = min(start_idx + self.blocks_per_region, len(all_blocks))
            frame_blocks = all_blocks[start_idx:end_idx]
            
            # Основные блоки
            for idx, bits in enumerate(frame_blocks):
                y = idx // self.blocks_x
                x = idx % self.blocks_x
                if y < self.blocks_y:
                    color = self._bits_to_color(bits)
                    self._draw_block(frame, x, y, color)
            
            # Резерв 1
            for idx, bits in enumerate(frame_blocks):
                y = idx // self.blocks_x
                x = idx % self.blocks_x + self.blocks_x
                if x < self.blocks_x * 2 and y < self.blocks_y:
                    color = self._bits_to_color(bits)
                    self._draw_block(frame, x, y, color)
            
            # Резерв 2
            for idx, bits in enumerate(frame_blocks):
                y = idx // self.blocks_x + self.blocks_y
                x = idx % self.blocks_x
                if x < self.blocks_x and y < self.blocks_y * 2:
                    color = self._bits_to_color(bits)
                    self._draw_block(frame, x, y, color)
            
            # Сохраняем кадр
            frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
            cv2.imwrite(frame_file, frame)
        
        # Создаем защитные кадры (синий фон)
        print("\n🛡️  Создание защитных кадров...")
        for i in range(5):
            frame_num = frames_needed - 5 + i
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = self._draw_markers(frame)
            for y in range(self.blocks_y * 2):
                for x in range(self.blocks_x * 2):
                    self._draw_block(frame, x, y, (255, 0, 0))
            frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
            cv2.imwrite(frame_file, frame)
            print(f"  🟦 Защитный кадр {i+1}/5")
        
        # Конвертируем в MP4
        print("\n🎞️  Конвертация в MP4...")
        
        # Определяем путь к FFmpeg
        ffmpeg_path = self._find_ffmpeg()
        
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
                output_file
            ]
            
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                print("✅ FFmpeg конвертация успешна")
            except Exception as e:
                print(f"⚠️ Ошибка FFmpeg: {e}, использую OpenCV...")
                self._write_opencv_video(temp_dir, frames_needed, output_file)
        else:
            print("⚠️ FFmpeg не найден, использую OpenCV...")
            self._write_opencv_video(temp_dir, frames_needed, output_file)
        
        # Удаляем временные файлы
        shutil.rmtree(temp_dir)
        print("🧹 Временные файлы удалены")
        
        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            print(f"\n✅ Видео сохранено: {output_file}")
            print(f"📊 Размер: {size} байт ({size/1024/1024:.2f} MB)")
            print(f"🎬 Кадров: {frames_needed}")
            print(f"⏱️  Длительность: {frames_needed/self.fps:.1f} сек")
            return True
        return False


class YouTubeDecoder:
    def __init__(self, key=None):
        self.width = 1920
        self.height = 1080
        self.block_height = 16
        self.block_width = 24
        self.spacing = 4
        self.marker_size = 80
        
        # Ключ шифрования
        self.key = key
        
        # 16 цветов
        self.colors = {
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
            '1111': (255, 255, 255)
        }
        
        # Оптимизации
        self.color_values = np.array(list(self.colors.values()), dtype=np.int32)
        self.color_keys = list(self.colors.keys())
        self.color_cache = {}
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Расчет сетки
        self.blocks_x = (self.width - 2*self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2*self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y
        
        # Предвычисление координат
        self._precompute_coordinates()
        
        print("="*60)
        print("🎬 ДЕКОДЕР YouTube")
        print("="*60)
        print(f"📊 Сетка: {self.blocks_x} x {self.blocks_y} блоков")
        print(f"🔐 Ключ: {'ЕСТЬ' if self.key else 'НЕТ'}")
    
    def _precompute_coordinates(self):
        """Предвычисляет координаты блоков"""
        self.block_coords = []
        for idx in range(self.blocks_per_region):
            y = idx // self.blocks_x
            x = idx % self.blocks_x
            if y < self.blocks_y:
                cx = self.marker_size + x * (self.block_width + self.spacing) + self.block_width // 2
                cy = self.marker_size + y * (self.block_height + self.spacing) + self.block_height // 2
                self.block_coords.append((cx, cy))
    
    def _decrypt_data(self, data):
        """XOR дешифрование с ключом"""
        if not self.key:
            return data
        
        key_bytes = self.key.encode()
        result = bytearray()
        
        for i, byte in enumerate(data):
            key_byte = key_bytes[i % len(key_bytes)]
            result.append(byte ^ key_byte)
        
        return result
    
    def _color_to_bits_fast(self, color):
        """Оптимизированный поиск цвета"""
        color_key = (color[0], color[1], color[2])
        
        if color_key in self.color_cache:
            self.cache_hits += 1
            return self.color_cache[color_key]
        
        self.cache_misses += 1
        
        # Быстрая проверка на синий фон
        if color[0] > 200 and color[1] < 50 and color[2] < 50:
            self.color_cache[color_key] = '0000'
            return '0000'
        
        # NumPy векторизация
        color_arr = np.array([color[0], color[1], color[2]], dtype=np.int32)
        distances = np.sum((self.color_values - color_arr) ** 2, axis=1)
        best_idx = np.argmin(distances)
        result = self.color_keys[best_idx]
        
        self.color_cache[color_key] = result
        return result
    
    def decode_frame_fast(self, frame):
        """Быстрое декодирование одного кадра с масштабированием"""
        # Принудительное масштабирование к оригинальному размеру
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), 
                              interpolation=cv2.INTER_NEAREST)
        
        blocks = []
        h, w = frame.shape[:2]
        
        for cx, cy in self.block_coords:
            if cx < w and cy < h:
                color = frame[cy, cx]
                bits = self._color_to_bits_fast(color)
                blocks.append(bits)
            else:
                blocks.append('0000')
        
        return blocks
    
    def _blocks_to_bytes(self, blocks):
        """4-битные блоки -> байты"""
        all_bits = ''.join(blocks)
        bytes_data = bytearray()
        
        for i in range(0, len(all_bits) - 7, 8):
            byte_str = all_bits[i:i+8]
            if len(byte_str) == 8:
                try:
                    byte = int(byte_str, 2)
                    bytes_data.append(byte)
                except:
                    bytes_data.append(0)
        
        return bytes_data
    
    def _find_eof_marker(self, data):
        """Поиск маркера конца █████... в данных"""
        eof_bytes = b'\xe2\x96\x88' * 64
        
        for i in range(len(data) - len(eof_bytes)):
            if data[i:i+len(eof_bytes)] == eof_bytes:
                return i
        return -1
    
    def decode(self, video_file, output_dir='.'):
        """Декодирует видео"""
        
        print("\n📥 ДЕКОДИРОВАНИЕ ВИДЕО")
        print("-" * 40)
        
        if not os.path.exists(video_file):
            print(f"❌ Файл не найден: {video_file}")
            return False
        
        cap = cv2.VideoCapture(video_file)
        if not cap.isOpened():
            print("❌ Не удалось открыть видео")
            return False
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        print(f"📹 Всего кадров: {total_frames}")
        print(f"📹 FPS: {fps}")
        print(f"📹 Разрешение: {width}x{height}")
        
        # Сброс статистики
        self.cache_hits = 0
        self.cache_misses = 0
        start_time = cv2.getTickCount()
        
        # Сбор блоков
        all_blocks = []
        frames_processed = 0
        
        for frame_num in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break
            
            frames_processed += 1
            
            # Прогресс
            if frame_num % 100 == 0:
                elapsed = (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
                speed = frames_processed / elapsed if elapsed > 0 else 0
                cache_ratio = (self.cache_hits / (self.cache_hits + self.cache_misses) * 100) if (self.cache_hits + self.cache_misses) > 0 else 0
                print(f"  Прогресс: {frame_num}/{total_frames} | "
                      f"Скорость: {speed:.1f} кадр/сек | "
                      f"Кэш: {cache_ratio:.1f}%")
            
            # Декодирование кадра с масштабированием
            frame_blocks = self.decode_frame_fast(frame)
            all_blocks.extend(frame_blocks)
        
        cap.release()
        
        # Статистика
        elapsed = (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
        print(f"\n📊 Статистика: {len(all_blocks)} блоков за {elapsed:.1f} сек")
        print(f"  🎯 Кэш: попаданий {self.cache_hits}, промахов {self.cache_misses}")
        print(f"  🔄 Кадров обработано: {frames_processed}")
        
        # Конвертация в байты
        bytes_data = self._blocks_to_bytes(all_blocks)
        print(f"📦 Получено байт: {len(bytes_data)}")
        
        # Поиск маркера конца
        eof_pos = self._find_eof_marker(bytes_data)
        if eof_pos > 0:
            bytes_data = bytes_data[:eof_pos]
            print(f"✅ Найден маркер конца на позиции {eof_pos}")
            print(f"📦 Байт после обрезки: {len(bytes_data)}")
        else:
            print("⚠️ Маркер конца не найден")
        
        # Поиск заголовка — сначала новый формат (ZIP-сжатие)
        # Из-за цветовых искажений при сжатии видео буквы могут искажаться
        # (например O -> N), поэтому ищем гибким паттерном
        data_str = bytes_data[:2000].decode('latin-1', errors='ignore')
        pattern_new = r'FILE:([^:]+)\.z[i1]p:SIZE:(\d+):.{0,5}RIG:([^:]+):.{0,8}RIG[S5]IZE:(\d+)\|'
        pattern_old = r'FILE:([^:]+):SIZE:(\d+)\|'
        match_new = re.search(pattern_new, data_str)
        
        if match_new:
            zip_base = match_new.group(1)
            zip_size = int(match_new.group(2))
            original_name = match_new.group(3)
            original_size = int(match_new.group(4))
            zip_name = zip_base + '.zip'
            
            print(f"\n✅ Найден заголовок (ZIP): {zip_name}, размер: {zip_size} байт")
            print(f"📄 Оригинал: {original_name}, размер: {original_size} байт")
            
            header_str = match_new.group(0)
            header_bytes = header_str.encode('latin-1')
            header_pos = bytes_data.find(header_bytes)
            
            if header_pos >= 0:
                # Извлекаем зашифрованные данные
                encrypted_data = bytes_data[header_pos + len(header_bytes):header_pos + len(header_bytes) + zip_size]
                
                # Дешифруем если есть ключ
                if self.key:
                    zip_data = self._decrypt_data(encrypted_data)
                    print(f"🔓 Данные расшифрованы")
                else:
                    zip_data = encrypted_data
                    print(f"⚠️ Данные без расшифровки")
                
                # Распаковка ZIP
                print(f"🗜️  Распаковка ZIP...")
                try:
                    buf = io.BytesIO(zip_data)
                    with zipfile.ZipFile(buf, 'r') as zf:
                        names = zf.namelist()
                        if not names:
                            print("❌ ZIP-архив пуст")
                            return False
                        extract_name = original_name if original_name in names else names[0]
                        file_data = zf.read(extract_name)
                    print(f"📦 ZIP распакован: {extract_name} ({len(file_data)} байт)")
                except zipfile.BadZipFile:
                    print("❌ Повреждённый ZIP-архив")
                    return False
                except Exception as e:
                    print(f"❌ Ошибка распаковки ZIP: {e}")
                    return False
                
                del zip_data
                
                # Сохраняем оригинальный файл
                output_path = os.path.join(output_dir, original_name)
                counter = 1
                base, ext = os.path.splitext(original_name)
                while os.path.exists(output_path):
                    output_path = os.path.join(output_dir, f"{base}_{counter}{ext}")
                    counter += 1
                
                with open(output_path, 'wb') as f:
                    f.write(file_data)
                
                print(f"\n✅ Файл восстановлен: {output_path}")
                print(f"📏 Размер: {len(file_data)} байт")
                
                # Проверка размера
                if len(file_data) == original_size:
                    print("✅ Размер совпадает с оригиналом")
                else:
                    print(f"⚠️ Размер не совпадает: {len(file_data)} != {original_size}")
                
                return True
        else:
            # Пробуем старый формат (без сжатия)
            match_old = re.search(pattern_old, data_str)
            
            if match_old:
                filename = match_old.group(1)
                filesize = int(match_old.group(2))
                
                print(f"\n✅ Найден заголовок (без сжатия): {filename}, размер: {filesize} байт")
                
                header_str = match_old.group(0)
                header_bytes = header_str.encode('latin-1')
                header_pos = bytes_data.find(header_bytes)
                
                if header_pos >= 0:
                    # Извлекаем зашифрованные данные
                    encrypted_data = bytes_data[header_pos + len(header_bytes):header_pos + len(header_bytes) + filesize]
                    
                    # Дешифруем если есть ключ
                    if self.key:
                        file_data = self._decrypt_data(encrypted_data)
                        print(f"🔓 Данные расшифрованы")
                    else:
                        file_data = encrypted_data
                        print(f"⚠️ Данные без расшифровки")
                    
                    # Сохраняем файл
                    output_path = os.path.join(output_dir, filename)
                    counter = 1
                    base, ext = os.path.splitext(filename)
                    while os.path.exists(output_path):
                        output_path = os.path.join(output_dir, f"{base}_{counter}{ext}")
                        counter += 1
                    
                    with open(output_path, 'wb') as f:
                        f.write(file_data)
                    
                    print(f"\n✅ Файл восстановлен: {output_path}")
                    print(f"📏 Размер: {len(file_data)} байт")
                    
                    # Проверка размера
                    if len(file_data) == filesize:
                        print("✅ Размер совпадает с оригиналом")
                    else:
                        print(f"⚠️ Размер не совпадает: {len(file_data)} != {filesize}")
                    
                    return True
            else:
                print("❌ Заголовок не найден")
        
        # Если не нашли заголовок
        output_path = os.path.join(output_dir, "decoded_data.bin")
        with open(output_path, 'wb') as f:
            f.write(bytes_data)
        print(f"\n💾 Данные сохранены: {output_path}")
        return False


def read_key_from_file(key_file='key.txt'):
    """Читает ключ из файла key.txt"""
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
                if key:
                    print(f"🔑 Ключ загружен из {key_file}")
                    return key
                else:
                    print(f"⚠️ Файл {key_file} пуст")
        else:
            print(f"ℹ️ Файл {key_file} не найден, шифрование не используется")
    except Exception as e:
        print(f"⚠️ Ошибка чтения ключа: {e}")
    
    return None


def save_key_to_file(key, key_file='key.txt'):
    """Сохраняет ключ в файл key.txt"""
    try:
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        print(f"🔑 Ключ сохранён в {key_file}")
    except Exception as e:
        print(f"⚠️ Ошибка сохранения ключа: {e}")


def main():
    # Поддержка запуска с аргументами командной строки (старый режим)
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description='🎥 YouTube File Storage (6 FPS) — кодирование/декодирование файлов в видео',
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
        
        # encode
        enc = subparsers.add_parser('encode', help='Закодировать файл в видео')
        enc.add_argument('input', help='Путь до входного файла')
        enc.add_argument('-o', '--output', default='output.mp4', help='Название выходного видео (по умолчанию: output.mp4)')
        enc.add_argument('-k', '--key', default=None, help='Ключ шифрования (сохраняется в key.txt)')
        
        # decode
        dec = subparsers.add_parser('decode', help='Декодировать видео в файл')
        dec.add_argument('input', help='Путь до видеофайла')
        dec.add_argument('-o', '--output', default='.', help='Папка для сохранения результата (по умолчанию: текущая папка)')
        dec.add_argument('-k', '--key', default=None, help='Ключ шифрования (сохраняется в key.txt)')
        
        args = parser.parse_args()
        
        if not args.command:
            parser.print_help()
            return
        
        # Определяем ключ: из аргумента или из файла
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

    # Интерактивный режим (запуск без аргументов)
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
        print("⚠️  Введите 1 или 2")

    # Ввод пути до файла
    while True:
        input_file = input("Путь до входного файла: ").strip()
        if input_file and os.path.exists(input_file):
            break
        if input_file:
            print(f"❌ Файл не найден: {input_file}")
        else:
            print("⚠️  Путь не может быть пустым")

    # Ввод названия выходного файла
    if choice == '1':
        default_output = 'output.mp4'
        prompt_text = "Название выходного видео"
    else:
        default_output = '.'
        prompt_text = "Папка для сохранения результата"

    output_val = input(f"{prompt_text} [по умолчанию: {default_output}]: ").strip()
    if not output_val:
        output_val = default_output

    # Ввод ключа шифрования
    saved_key = read_key_from_file()
    if saved_key:
        print(f"🔑 Найден сохранённый ключ: {saved_key}")
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
            print("ℹ️  Шифрование отключено")

    # Запуск
    if choice == '1':
        encoder = YouTubeEncoder(key)
        encoder.encode(input_file, output_val)
    else:
        decoder = YouTubeDecoder(key)
        decoder.decode(input_file, output_val)


if __name__ == "__main__":
    main()
