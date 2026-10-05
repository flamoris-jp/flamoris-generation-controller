"""Process-owned provider lookup and lifecycle management."""

import re

from .base import GenerationProvider, ProviderError


class ProviderRegistry:
    def __init__(self, providers: tuple[GenerationProvider, ...] = ()):
        self._providers: dict[str, GenerationProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: GenerationProvider) -> None:
        provider_id = provider.provider_id
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", provider_id):
            raise ValueError("Provider ID must be a stable lowercase identifier")
        if provider_id in self._providers:
            raise ValueError(f"Duplicate provider ID: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> GenerationProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ValueError(f"Unknown provider ID: {provider_id}") from None

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    async def health(self) -> list[dict[str, object]]:
        results = []
        for provider_id, provider in self._providers.items():
            try:
                health = (await provider.health()).as_dict()
            except ProviderError as exc:
                health = {"available": False, "error": str(exc)}
            results.append({**health, "id": provider_id})
        return results

    async def close(self) -> None:
        first_error: BaseException | None = None
        for provider in self._providers.values():
            try:
                await provider.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
