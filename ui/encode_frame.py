"""Вкладка «Кодировать»."""

import threading
import queue
import customtkinter as ctk

from .components.file_picker import FilePicker
from .components.key_input import KeyInput
from .components.log_viewer import LogViewer
from .theme import COLORS, FONT_TAB, FONT_BUTTON, FONT_LABEL
from core.encoder import YouTubeEncoder
from utils.key_storage import save_key

_LOG = 'log'
_PROGRESS = 'progress'
_SUCCESS = 'success'
_ERROR = 'error'
_FINISH = 'finish'


class EncodeFrame(ctk.CTkFrame):
    """Вкладка кодирования файла в видео."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)

        self._running = False
        self._queue: queue.Queue = queue.Queue()
        self._poll_id = None

        # ── Заголовок ──────────────────────────────────────
        header = ctk.CTkLabel(
            self, text='Кодировать файл в видео',
            font=FONT_TAB, text_color=COLORS['accent'],
        )
        header.pack(anchor='w', pady=(0, 16))

        # ── Поля ввода ─────────────────────────────────────
        self._input_picker = FilePicker(
            self,
            label='Входной файл',
            placeholder='Выберите файл для кодирования...',
            file_mode=True,
            file_types=[('Все файлы', '*.*')],
        )
        self._input_picker.pack(fill='x', pady=(0, 12))

        self._output_picker = FilePicker(
            self,
            label='Выходное видео',
            placeholder='output.mp4',
            file_mode=True,
            file_types=[('MP4 видео', '*.mp4'), ('Все файлы', '*.*')],
        )
        self._output_picker.pack(fill='x', pady=(0, 12))

        self._key_input = KeyInput(self)
        self._key_input.pack(fill='x', pady=(0, 16))

        # ── Кнопка запуска ─────────────────────────────────
        self._btn_start = ctk.CTkButton(
            self,
            text='Кодировать',
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
            self._log.log('Укажите входной файл')
            return

        output_file = self._output_picker.get() or 'output.mp4'
        key = self._key_input.get()

        if key:
            save_key(key)

        self._running = True
        self._btn_start.configure(state='disabled', text='Кодирование...')
        self._log.clear()

        # Запуск рабочего потока
        thread = threading.Thread(
            target=self._run_encode,
            args=(input_file, output_file, key),
            daemon=True,
        )
        thread.start()

        # Запуск поллера (один after на всё время работы)
        self._poll()

    def _run_encode(self, input_file: str, output_file: str, key: str | None):
        """Выполняется в фоновом потоке. Кладёт события в очередь — не трогает tkinter."""
        q = self._queue
        encoder = YouTubeEncoder(key=key)

        def on_log(msg):
            q.put((_LOG, msg))

        def on_progress(pct):
            q.put((_PROGRESS, pct))

        try:
            success = encoder.encode(input_file, output_file,
                                      on_log=on_log, on_progress=on_progress)
            if success:
                q.put((_SUCCESS, 'Кодирование завершено успешно!'))
            else:
                q.put((_ERROR, 'Ошибка при кодировании'))
        except Exception as e:
            q.put((_ERROR, f'Ошибка: {e}'))
        finally:
            q.put((_FINISH, None))

    def _poll(self):
        """Забирает ВСЕ накопленные события из очереди за один вызов. Один after на 200мс."""
        q = self._queue
        log = self._log

        # Выгребаем всё что накопилось
        while True:
            try:
                kind, data = q.get_nowait()
            except queue.Empty:
                break

            if kind == _LOG:
                log.log(data)
            elif kind == _PROGRESS:
                log.set_progress(data)
            elif kind == _SUCCESS:
                log.log(data)
                log.show_success(data)
            elif kind == _ERROR:
                log.log(data)

        # Если работа ещё идёт — планируем следующий опрос
        if self._running:
            self._poll_id = self.after(200, self._poll)
        else:
            self._on_finish()

    def _on_finish(self):
        self._running = False
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self._btn_start.configure(state='normal', text='Кодировать')
