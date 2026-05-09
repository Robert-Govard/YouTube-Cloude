"""Модуль шифрования/дешифрования XOR (векторизованный через numpy)."""

import numpy as np


def xor_encrypt(data: bytes, key: str) -> bytes:
    """XOR-шифрование данных строковым ключом. Векторизовано через numpy."""
    if not key:
        return data
    key_bytes = np.frombuffer(key.encode(), dtype=np.uint8)
    data_arr = np.frombuffer(data, dtype=np.uint8)
    # Повторяем ключ до длины данных
    repeats = (len(data_arr) // len(key_bytes)) + 1
    key_tiled = np.tile(key_bytes, repeats)[:len(data_arr)]
    result = np.bitwise_xor(data_arr, key_tiled)
    return result.tobytes()


def xor_decrypt(data: bytes, key: str) -> bytes:
    """XOR-дешифрование данных строковым ключом (симметрично шифрованию)."""
    return xor_encrypt(data, key)
