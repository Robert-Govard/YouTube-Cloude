"""Чтение/сохранение ключа шифрования в файл."""

import os

DEFAULT_KEY_FILE = 'key.txt'


def read_key(key_file: str = DEFAULT_KEY_FILE) -> str | None:
    """Читает ключ из файла. Возвращает None, если файл пуст или не существует."""
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
                return key if key else None
    except Exception:
        pass
    return None


def save_key(key: str, key_file: str = DEFAULT_KEY_FILE) -> bool:
    """Сохраняет ключ в файл. Возвращает True при успехе."""
    try:
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        return True
    except Exception:
        return False
