"""Компонент лог-вывода с прогресс-баром."""

import sys
import customtkinter as ctk
from ..theme import COLORS, FONT_LOG


class LogViewer(ctk.CTkFrame):
    """Текстовое поле для логов + прогресс-бар."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)

        # Прогресс-бар
        self._progress = ctk.CTkProgressBar(self, height=8, corner_radius=4,
                                              fg_color=COLORS['input_border'],
                                              progress_color=COLORS['accent'])
        self._progress.pack(fill='x', pady=(0, 6))
        self._progress.set(0)

        # Текстовое поле
        self._textbox = ctk.CTkTextbox(
            self,
            font=FONT_LOG,
            height=200,
            corner_radius=8,
            fg_color=COLORS['log_bg'],
            text_color=COLORS['text_primary'],
            wrap='word',
            activate_scrollbars=True,
        )
        self._textbox.pack(fill='both', expand=True)
        self._textbox.configure(state='disabled')

    # ── публичный API ───────────────────────────────────────

    def log(self, message: str):
        """Добавляет строку в лог (консоль получает вывод автоматически через перехват stdout)."""
        print(message, flush=True)
        self._textbox.configure(state='normal')
        self._textbox.insert('end', message + '\n')
        self._textbox.see('end')
        self._textbox.configure(state='disabled')

    def set_progress(self, pct: float):
        """Устанавливает прогресс (0..100)."""
        self._progress.set(max(0, min(pct, 100)) / 100)

    def clear(self):
        """Очищает лог и прогресс."""
        self._textbox.configure(state='normal')
        self._textbox.delete('0.0', 'end')
        self._textbox.configure(state='disabled')
        self._progress.set(0)

    def show_success(self, message: str = 'Операция завершена успешно!'):
        """Показывает зелёный баннер об успехе поверх лога."""
        print(message, flush=True)

        banner = ctk.CTkFrame(self, fg_color=COLORS['success'], corner_radius=10, height=50)
        banner.place(relx=0.5, rely=0.5, anchor='center', relwidth=0.9)

        label = ctk.CTkLabel(
            banner,
            text=message,
            font=('Segoe UI', 16, 'bold'),
            text_color='#000000',
        )
        label.pack(expand=True, pady=10)

        # Авто-скрытие через 4 секунды
        self.after(4000, banner.destroy)
