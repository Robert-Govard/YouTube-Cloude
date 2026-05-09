"""Компонент ввода ключа шифрования."""

import customtkinter as ctk
from ..theme import COLORS, FONT_LABEL, FONT_INPUT, FONT_SMALL


class KeyInput(ctk.CTkFrame):
    """Поле ввода ключа с чекбоксом «Показать ключ» и индикатором сохранённого ключа."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)

        self._saved_key: str | None = None

        # Метка
        self._label = ctk.CTkLabel(self, text='Ключ шифрования', font=FONT_LABEL,
                                    anchor='w', text_color=COLORS['text_primary'])
        self._label.pack(anchor='w', pady=(0, 4))

        # Строка: чекбокс + поле ввода
        row = ctk.CTkFrame(self, fg_color='transparent')
        row.pack(fill='x')

        self._show_var = ctk.BooleanVar(value=False)
        self._show_cb = ctk.CTkCheckBox(
            row, text='', variable=self._show_var, width=24,
            command=self._toggle_visibility,
            checkbox_width=20, checkbox_height=20,
            fg_color=COLORS['accent'], hover_color=COLORS['accent_hover'],
            border_color=COLORS['input_border'],
        )
        self._show_cb.pack(side='left', padx=(0, 6))

        self._entry = ctk.CTkEntry(
            row,
            placeholder_text='Введите ключ (пусто — без шифрования)',
            font=FONT_INPUT,
            height=38,
            corner_radius=8,
            show='*',
            border_color=COLORS['input_border'],
            fg_color=COLORS['input_bg'],
            text_color=COLORS['text_primary'],
        )
        self._entry.pack(side='left', fill='x', expand=True)

        # Индикатор сохранённого ключа
        self._saved_label = ctk.CTkLabel(
            self, text='', font=FONT_SMALL,
            text_color=COLORS['text_secondary'],
        )
        self._saved_label.pack(anchor='w', pady=(4, 0))

        # Кнопка «Использовать сохранённый»
        self._use_saved_btn = ctk.CTkButton(
            self,
            text='Использовать сохранённый ключ',
            width=220,
            height=30,
            font=FONT_SMALL,
            corner_radius=8,
            fg_color=COLORS['bg_light'],
            hover_color=COLORS['accent'],
            command=self._apply_saved,
        )
        self._use_saved_btn.pack(anchor='w', pady=(4, 0))

        # Первичная загрузка
        self._load_saved()

    # ── публичный API ───────────────────────────────────────

    def get(self) -> str | None:
        """Возвращает ключ или None (без шифрования)."""
        val = self._entry.get().strip()
        return val if val else None

    # ── внутренние ──────────────────────────────────────────

    def _toggle_visibility(self):
        self._entry.configure(show='' if self._show_var.get() else '*')

    def _load_saved(self):
        from utils.key_storage import read_key
        key = read_key()
        if key:
            self._saved_key = key
            self._saved_label.configure(text=f'Сохранённый ключ: {key}')
            self._use_saved_btn.pack(anchor='w', pady=(4, 0))
        else:
            self._saved_key = None
            self._saved_label.configure(text='Сохранённый ключ отсутствует')
            self._use_saved_btn.pack_forget()

    def _apply_saved(self):
        if self._saved_key:
            self._entry.delete(0, 'end')
            self._entry.insert(0, self._saved_key)
