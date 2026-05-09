"""Вкладка «Декодировать»."""

import threading
import customtkinter as ctk

from .components.file_picker import FilePicker
from .components.key_input import KeyInput
from .components.log_viewer import LogViewer
from .theme import COLORS, FONT_TAB, FONT_BUTTON
from core.decoder import YouTubeDecoder
from utils.key_storage import save_key


class DecodeFrame(ctk.CTkFrame):
    """Вкладка декодирования видео в файл."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)

        self._running = False

        # ── Заголовок ──────────────────────────────────────
        header = ctk.CTkLabel(
            self, text='Декодировать видео в файл',
            font=FONT_TAB, text_color=COLORS['accent'],
        )
        header.pack(anchor='w', pady=(0, 16))

        # ── Поля ввода ─────────────────────────────────────
        self._input_picker = FilePicker(
            self,
            label='Видеофайл',
            placeholder='Выберите видео для декодирования...',
            file_mode=True,
            file_types=[('MP4 видео', '*.mp4'), ('AVI видео', '*.avi'), ('Все файлы', '*.*')],
        )
        self._input_picker.pack(fill='x', pady=(0, 12))

        self._output_picker = FilePicker(
            self,
            label='Папка для сохранения',
            placeholder='.',
            file_mode=False,
        )
        self._output_picker.pack(fill='x', pady=(0, 12))

        self._key_input = KeyInput(self)
        self._key_input.pack(fill='x', pady=(0, 16))

        # ── Кнопка запуска ─────────────────────────────────
        self._btn_start = ctk.CTkButton(
            self,
            text='Декодировать',
            font=FONT_BUTTON,
            height=44,
            corner_radius=10,
            fg_color=COLORS['accent'],
            hover_color=COLORS['accent_hover'],
            command=self._on_start,
        )
        self._btn_start.pack(fill='x', pady=(0, 12))

        # ── Лог ────────────────────────────────────────────
        self._log = LogViewer(self)
        self._log.pack(fill='both', expand=True)

    # ── обработчики ────────────────────────────────────────

    def _on_start(self):
        if self._running:
            return

        input_file = self._input_picker.get()
        if not input_file:
            self._log.log('Укажите видеофайл')
            return

        output_dir = self._output_picker.get() or '.'
        key = self._key_input.get()

        # Сохраняем ключ, если введён
        if key:
            save_key(key)

        self._running = True
        self._btn_start.configure(state='disabled', text='Декодирование...')
        self._log.clear()

        thread = threading.Thread(
            target=self._run_decode,
            args=(input_file, output_dir, key),
            daemon=True,
        )
        thread.start()

    def _run_decode(self, input_file: str, output_dir: str, key: str | None):
        decoder = YouTubeDecoder(key=key)

        def on_log(msg):
            self.after(0, lambda: self._log.log(msg))

        def on_progress(pct):
            self.after(0, lambda: self._log.set_progress(pct))

        try:
            success = decoder.decode(input_file, output_dir,
                                      on_log=on_log, on_progress=on_progress)
            if success:
                self.after(0, lambda: self._log.log('Декодирование завершено успешно!'))
                self.after(0, lambda: self._log.show_success('Декодирование завершено успешно!'))
            else:
                self.after(0, lambda: self._log.log('Ошибка при декодировании'))
        except Exception as e:
            self.after(0, lambda: self._log.log(f'Ошибка: {e}'))
        finally:
            self.after(0, self._on_finish)

    def _on_finish(self):
        self._running = False
        self._btn_start.configure(state='normal', text='Декодировать')
