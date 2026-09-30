from typing import Protocol


class MemoryBackend(Protocol):
    def add(self, payload: dict) -> None: ...
    def search(self, user_id: str, query: str, top_k: int) -> list[dict]: ...

