"""LLM post-processing: turn a raw transcript into clean, pasteable text.

Mirrors the macOS original's `Enhancement/` subsystem — off by default, opt-in
from the tray, and fail-soft: if the provider errors out, the raw transcript is
pasted rather than nothing.
"""

from .service import EnhancementService, EnhancementResult
from .providers import PROVIDERS, ProviderError

__all__ = ["EnhancementService", "EnhancementResult", "PROVIDERS", "ProviderError"]
