"""One error type for every SMS provider, so the messenger handles a refused send the same
way whichever provider refused it."""
from typing import List


class ProviderError(Exception):
    def __init__(self, provider: str, status: int, errors: List[dict]):
        self.provider = provider
        self.status = status
        self.errors = errors or [{"title": f"HTTP {status}"}]
        first = self.errors[0]
        self.code = str(first.get("code", ""))
        self.title = first.get("title", "")
        self.detail = first.get("detail", "")
        super().__init__(f"{provider} {status} {self.code} {self.title}: {self.detail}".strip())
