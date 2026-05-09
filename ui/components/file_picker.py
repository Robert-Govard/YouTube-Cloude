"""Компонент выбора файла/папки."""

import customtkinter as ctk
from tkinter import filedialog
from ..theme import COLORS, FONT_LABEL, FONT_INPUT


class FilePicker(ctk.CTkFrame):
    """Строка: метка + поле ввода + кнопка «Обзор»."""

    def __init__(
        self,
        master,
        label: str,
        placeholder: str = '',
        file_mode: bool = True,
        file_types: list | None = None,
        **kwargs,
    ):
        """
        file_mode=True  — выбор файла
        file_mode=False — выбор папки
        """
        super().__init__(master, fg_color='transparent', **kwargs)

        self._file_mode = file_mode
        self._file_types = file_types or []

        # Метка
        self._label = ctk.CTkLabel(self, text=label, font=FONT_LABEL,
                                    anchor='w', text_color=COLORS['text_primary'])
        self._label.pack(anchor='w', pady=(0, 4))

        # Строка ввода + кнопка
        row = ctk.CTkFrame(self, fg_color='transparent')
        row.pack(fill='x')

        self._entry = ctk.CTkEntry(
            row,
            placeholder_text=placeholder,
            font=FONT_INPUT,
            height=38,
            corner_radius=8,
            border_color=COLORS['input_border'],
            fg_color=COLORS['input_bg'],
            text_color=COLORS['text_primary'],
        )
        self._entry.pack(side='left', fill='x', expand=True, padx=(0, 8))

        self._btn = ctk.CTkButton(
            row,
            text='Обзор',
            width=90,
            height=38,
            font=FONT_LABEL,
            corner_radius=8,
            fg_color=COLORS['bg_light'],
            hover_color=COLORS['accent'],
            command=self._browse,
        )
        self._btn.pack(side='right')

    # ── публичный API ───────────────────────────────────────

    def get(self) -> str:
        return self._entry.get().strip()

    def set(self, value: str):
        self._entry.delete(0, 'end')
        self._entry.insert(0, value)

    # ── внутренние ──────────────────────────────────────────

    def _browse(self):
        if self._file_mode:
            path = filedialog.askopenfilename(filetypes=self._file_types)
        else:
            path = filedialog.askdirectory()
        if path:
            self.set(path)
