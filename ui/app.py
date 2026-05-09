"""Главное окно приложения."""

import customtkinter as ctk

from .encode_frame import EncodeFrame
from .decode_frame import DecodeFrame
from .components.console_panel import ConsolePanel
from .theme import COLORS, FONT_TITLE, FONT_TAB, WINDOW_WIDTH, WINDOW_HEIGHT, MIN_WIDTH, MIN_HEIGHT


class App(ctk.CTk):
    """Главное окно с табами «Кодировать» / «Декодировать» и встроенной консолью."""

    def __init__(self):
        super().__init__()

        # ── Настройка окна ─────────────────────────────────
        self.title('YouTube File Storage')
        self.geometry(f'{WINDOW_WIDTH}x{WINDOW_HEIGHT}')
        self.minsize(MIN_WIDTH, MIN_HEIGHT)

        # Тёмная тема
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('dark-blue')

        self.configure(fg_color=COLORS['bg_dark'])

        # ── Заголовок ──────────────────────────────────────
        title_frame = ctk.CTkFrame(self, fg_color='transparent')
        title_frame.pack(fill='x', padx=24, pady=(20, 0))

        title = ctk.CTkLabel(
            title_frame,
            text='YouTube File Storage',
            font=FONT_TITLE,
            text_color=COLORS['text_primary'],
        )
        title.pack(side='left')

        subtitle = ctk.CTkLabel(
            title_frame,
            text='Кодирование / декодирование файлов в видео',
            font=FONT_TAB,
            text_color=COLORS['text_secondary'],
        )
        subtitle.pack(side='left', padx=(16, 0))

        # ── Табы ───────────────────────────────────────────
        self._tabview = ctk.CTkTabview(
            self,
            fg_color=COLORS['bg_medium'],
            segmented_button_fg_color=COLORS['bg_dark'],
            segmented_button_selected_color=COLORS['accent'],
            segmented_button_unselected_color=COLORS['bg_light'],
            corner_radius=12,
        )
        self._tabview.pack(fill='both', expand=True, padx=24, pady=(16, 8))

        tab_encode = self._tabview.add('Кодировать')
        tab_decode = self._tabview.add('Декодировать')

        self._encode_frame = EncodeFrame(tab_encode)
        self._encode_frame.pack(fill='both', expand=True, padx=12, pady=12)

        self._decode_frame = DecodeFrame(tab_decode)
        self._decode_frame.pack(fill='both', expand=True, padx=12, pady=12)

        # ── Встроенная консоль ─────────────────────────────
        self._console = ConsolePanel(self)
        self._console.pack(fill='x', padx=24, pady=(0, 16))

        # При закрытии окна — восстановить stdout/stderr
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    def _on_close(self):
        self._console.restore_streams()
        self.destroy()
