"""Вкладка «Декодировать»."""

import multiprocessing as mp
import customtkinter as ctk

from .components.file_picker import FilePicker
from .components.key_input import KeyInput
from .components.log_viewer import LogViewer
from .theme import COLORS, FONT_TAB, FONT_BUTTON
from core.decoder import YouTubeDecoder
from utils.key_storage import save_key

_LOG = 'log'
_PROGRESS = 'progress'
_SUCCESS = 'success'
_ERROR = 'error'
_FINISH = 'finish'


def _decode_worker(input_file: str, output_dir: str, key: str | None, q: mp.Queue):
    """Выполняется в отдельном процессе — свой GIL, не блокирует GUI."""
    try:
        decoder = YouTubeDecoder(key=key)

        def on_log(msg):
            q.put((_LOG, msg))

        def on_progress(pct):
            q.put((_PROGRESS, pct))

        success = decoder.decode(input_file, output_dir,
                                  on_log=on_log, on_progress=on_progress)
        if success:
            q.put((_SUCCESS, 'Декодирование завершено успешно!'))
        else:
            q.put((_ERROR, 'Ошибка при декодировании'))
    except Exception as e:
        q.put((_ERROR, f'Ошибка: {e}'))
    finally:
        q.put((_FINISH, None))


class DecodeFrame(ctk.CTkFrame):
    """Вкладка декодирования видео в файл."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)

        self._running = False
        self._queue: mp.Queue | None = None
        self._process: mp.Process | None = None
        self._poll_id = None

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

        if key:
            save_key(key)

        self._running = True
        self._btn_start.configure(state='disabled', text='Декодирование...')
        self._log.clear()

        # Запуск в отдельном ПРОЦЕССЕ (обходит GIL)
        self._queue = mp.Queue()
        self._process = mp.Process(
            target=_decode_worker,
            args=(input_file, output_dir, key, self._queue),
            daemon=True,
        )
        self._process.start()

        # Запуск поллера
        self._poll()

    def _poll(self):
        """Забирает события из очереди. Один after на 200мс."""
        q = self._queue
        log = self._log

        while True:
            try:
                kind, data = q.get_nowait()
            except Exception:
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
            elif kind == _FINISH:
                self._running = False

        if self._process and self._process.is_alive():
            self._poll_id = self.after(200, self._poll)
        else:
            self._on_finish()

    def _on_finish(self):
        self._running = False
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=3)
        self._process = None
        self._queue = None
        self._btn_start.configure(state='normal', text='Декодировать')
