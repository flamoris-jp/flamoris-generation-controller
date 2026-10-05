"""Provider-neutral execution contracts and provider implementations."""

from .base import (
    GenerationProvider,
    GenerationRequest,
    JobSnapshot,
    OutputRole,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
)
from .registry import ProviderRegistry

__all__ = [
    "GenerationProvider",
    "GenerationRequest",
    "JobSnapshot",
    "OutputRole",
    "ProviderError",
    "ProviderHealth",
    "ProviderJob",
    "ProviderOutput",
    "ProviderRegistry",
]
