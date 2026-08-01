"""LLM post-processing: turn a raw transcript into clean, pasteable text.

Mirrors the macOS original's `Enhancement/` subsystem — off by default, opt-in
from the tray, and fail-soft: if the provider errors out, the raw transcript is
pasted rather than nothing.
"""

from .service import EnhancementService, EnhancementResult
from .providers import ProviderError
from .registry import PROVIDERS, PROVIDER_KEYS, ProviderSpec, default_model, spec

__all__ = [
    "EnhancementService",
    "EnhancementResult",
    "ProviderError",
    "PROVIDERS",
    "PROVIDER_KEYS",
    "ProviderSpec",
    "default_model",
    "spec",
]
