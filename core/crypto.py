"""Модуль шифрования/дешифрования XOR."""


def xor_encrypt(data: bytes, key: str) -> bytes:
    """XOR-шифрование данных строковым ключом."""
    if not key:
        return data
    key_bytes = key.encode()
    result = bytearray()
    for i, byte in enumerate(data):
        result.append(byte ^ key_bytes[i % len(key_bytes)])
    return bytes(result)


def xor_decrypt(data: bytes, key: str) -> bytes:
    """XOR-дешифрование данных строковым ключом (симметрично шифрованию)."""
    return xor_encrypt(data, key)
