"""Встроенная консоль — перехватывает stdout/stderr и показывает в GUI."""

import sys
import io
import threading
import customtkinter as ctk
from ..theme import COLORS, FONT_LOG


class _NullStream:
    """Поток-заглушка для случаев, когда stdout/stderr = None (PyInstaller --windowed)."""

    def write(self, text):
        pass

    def flush(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **kw: None


class ConsolePanel(ctk.CTkFrame):
    """
    Панель консоли внутри приложения.

    Перехватывает stdout и stderr, отображая весь вывод в текстовом поле.
    Оригинальные потоки сохраняются, поэтому print() работает как обычно
    и одновременно появляется в GUI.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS['log_bg'], corner_radius=10, **kwargs)

        # ── Заголовок панели ────────────────────────────────
        header_row = ctk.CTkFrame(self, fg_color='transparent')
        header_row.pack(fill='x', padx=8, pady=(8, 4))

        title = ctk.CTkLabel(
            header_row,
            text='Консоль',
            font=('Segoe UI', 11, 'bold'),
            text_color=COLORS['text_secondary'],
        )
        title.pack(side='left')

        self._btn_clear = ctk.CTkButton(
            header_row,
            text='Очистить',
            width=70,
            height=24,
            font=('Segoe UI', 10),
            corner_radius=6,
            fg_color=COLORS['input_border'],
            hover_color=COLORS['bg_light'],
            text_color=COLORS['text_secondary'],
            command=self.clear,
        )
        self._btn_clear.pack(side='right')

        # ── Текстовое поле ──────────────────────────────────
        self._textbox = ctk.CTkTextbox(
            self,
            font=FONT_LOG,
            height=140,
            corner_radius=6,
            fg_color='#080814',
            text_color=COLORS['text_primary'],
            wrap='word',
            activate_scrollbars=True,
        )
        self._textbox.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self._textbox.configure(state='disabled')

        # ── Перехват stdout/stderr ──────────────────────────
        # При PyInstaller --windowed потоки могут быть None
        self._original_stdout = sys.stdout if sys.stdout else _NullStream()
        self._original_stderr = sys.stderr if sys.stderr else _NullStream()
        self._lock = threading.Lock()

        sys.stdout = _StreamInterceptor(self, 'out')
        sys.stderr = _StreamInterceptor(self, 'err')

    def write(self, text: str, source: str = 'out'):
        """Записывает текст в консоль GUI + оригинальный поток."""
        with self._lock:
            # Оригинальный поток (реальный терминал), безопасно если None
            try:
                if source == 'err':
                    self._original_stderr.write(text)
                    self._original_stderr.flush()
                else:
                    self._original_stdout.write(text)
                    self._original_stdout.flush()
            except (AttributeError, ValueError):
                pass

            # GUI — только если есть непустой текст
            if text and text.strip():
                self._append(text)

    def _append(self, text: str):
        """Потокобезопасная вставка текста в виджет."""
        try:
            self._textbox.configure(state='normal')
            self._textbox.insert('end', text)
            self._textbox.see('end')
            self._textbox.configure(state='disabled')
        except Exception:
            self.after(0, lambda: self._insert_safe(text))

    def _insert_safe(self, text: str):
        """Вставка через главный поток (thread-safe)."""
        try:
            self._textbox.configure(state='normal')
            self._textbox.insert('end', text)
            self._textbox.see('end')
            self._textbox.configure(state='disabled')
        except Exception:
            pass

    def clear(self):
        """Очищает консоль."""
        self._textbox.configure(state='normal')
        self._textbox.delete('0.0', 'end')
        self._textbox.configure(state='disabled')

    def restore_streams(self):
        """Восстанавливает оригинальные stdout/stderr."""
        if isinstance(sys.stdout, _StreamInterceptor):
            sys.stdout = self._original_stdout
        if isinstance(sys.stderr, _StreamInterceptor):
            sys.stderr = self._original_stderr

    def destroy(self):
        self.restore_streams()
        super().destroy()


class _StreamInterceptor:
    """Объект-перехватчик, совместимый с sys.stdout/stderr."""

    def __init__(self, panel: ConsolePanel, source: str):
        self._panel = panel
        self._source = source  # 'out' или 'err'

    def write(self, text: str):
        self._panel.write(text, self._source)

    def flush(self):
        try:
            original = self._panel._original_stdout if self._source == 'out' else self._panel._original_stderr
            original.flush()
        except (AttributeError, ValueError):
            pass

    def __getattr__(self, name):
        # Проксируем остальные атрибуты к оригинальному потоку
        original = self._panel._original_stdout if self._source == 'out' else self._panel._original_stderr
        try:
            return getattr(original, name)
        except AttributeError:
            return lambda *a, **kw: None
