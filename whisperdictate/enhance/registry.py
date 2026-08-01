"""Which clean-up providers exist and how to reach them.

Kept free of package imports (i18n aside, which imports nothing itself) so both
`config` and `providers` can read it without a cycle.

DeepSeek speaks the Anthropic Messages protocol at a different base URL, so it
needs no client of its own — only a different endpoint and a different key.

Anything a human reads lives in `i18n`, and only the product name stays here:
"DeepSeek API" is the same string in every language, "~10x cheaper" is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import t

MESSAGES_API = "messages_api"
CLI = "cli"


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    #: Bare product name. The trade-off hint next to it in the menu, and the
    #: jurisdiction, are translated - see label_with_hint() and hosting().
    label: str
    kind: str
    #: None means the SDK's own default (api.anthropic.com).
    base_url: str | None
    #: Environment variable checked before Credential Manager. None for the CLI.
    env_var: str | None
    models: tuple[str, ...]


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        key="anthropic",
        label="Anthropic API",
        kind=MESSAGES_API,
        base_url=None,
        env_var="ANTHROPIC_API_KEY",
        models=("claude-haiku-4-5", "claude-sonnet-5"),
    ),
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek API",
        kind=MESSAGES_API,
        # Anthropic-compatible endpoint. Ignores anthropic-beta,
        # anthropic-version, top_k and cache_control; we send none of them.
        # DeepSeek's prompt caching is automatic server-side, so the ignored
        # cache_control does not cost us the cache-hit rate.
        base_url="https://api.deepseek.com/anthropic",
        env_var="DEEPSEEK_API_KEY",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
    ),
    "claude_cli": ProviderSpec(
        key="claude_cli",
        label="Claude Code CLI",
        kind=CLI,
        base_url=None,
        env_var=None,
        # Sonnet first, against intuition, because it was measured: over 5 runs
        # of the same transcript, haiku-4-5 through the CLI took 19.8-60+ s and
        # timed out twice, while sonnet-5 stayed at 4.2-5.7 s. Whatever the CLI
        # does around a haiku call dominates the model's own speed, so listing
        # haiku first made the default choice the one that reliably times out.
        models=("claude-sonnet-5", "claude-haiku-4-5"),
    ),
}

DEFAULT_PROVIDER = "anthropic"
PROVIDER_KEYS: tuple[str, ...] = tuple(PROVIDERS)


def spec(key: str) -> ProviderSpec:
    return PROVIDERS.get(key, PROVIDERS[DEFAULT_PROVIDER])


def label_with_hint(provider_key: str) -> str:
    """Product name plus the reason to pick it - what the tray menu offers."""
    provider = spec(provider_key)
    return f"{provider.label} ({t(f'provider.{provider.key}.hint')})"


def hosting(provider_key: str) -> str:
    """Where the request goes. Surfaced in --check, the key dialog and the docs,
    so the jurisdiction is chosen knowingly rather than discovered later.

    Keyed off spec().key rather than the argument, so an unknown provider name
    describes the fallback provider it will actually use instead of returning a
    lookup miss.
    """
    return t(f"provider.{spec(provider_key).key}.hosting")


def default_model(provider_key: str) -> str:
    return spec(provider_key).models[0]


def supports_model(provider_key: str, model: str) -> bool:
    return model in spec(provider_key).models
