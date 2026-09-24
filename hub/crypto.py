"""Шифрование сессий и номеров. Ключ живёт в .env, не в БД."""
from cryptography.fernet import Fernet


class Box:
    def __init__(self, key: str) -> None:
        self._f = Fernet(key.encode())

    def seal(self, text: str) -> bytes:
        return self._f.encrypt(text.encode())

    def open(self, blob: bytes) -> str:
        return self._f.decrypt(blob).decode()
