"""Which clean-up providers exist and how to reach them.

Kept free of package imports so both `config` and `providers` can read it
without a cycle.

DeepSeek speaks the Anthropic Messages protocol at a different base URL, so it
needs no client of its own — only a different endpoint and a different key.
"""

from __future__ import annotations

from dataclasses import dataclass

MESSAGES_API = "messages_api"
CLI = "cli"


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    kind: str
    #: None means the SDK's own default (api.anthropic.com).
    base_url: str | None
    #: Environment variable checked before Credential Manager. None for the CLI.
    env_var: str | None
    models: tuple[str, ...]
    #: Where the request goes. Surfaced in --check and the docs so the choice
    #: is made knowingly rather than discovered later.
    hosting: str


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        key="anthropic",
        label="Anthropic API (~1 s)",
        kind=MESSAGES_API,
        base_url=None,
        env_var="ANTHROPIC_API_KEY",
        models=("claude-haiku-4-5", "claude-sonnet-5"),
        hosting="Anthropic (USA)",
    ),
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek API (~10x tanszy)",
        kind=MESSAGES_API,
        # Anthropic-compatible endpoint. Ignores anthropic-beta,
        # anthropic-version, top_k and cache_control; we send none of them.
        # DeepSeek's prompt caching is automatic server-side, so the ignored
        # cache_control does not cost us the cache-hit rate.
        base_url="https://api.deepseek.com/anthropic",
        env_var="DEEPSEEK_API_KEY",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        hosting="DeepSeek (Chiny) - nie uzywac do tresci sluzbowych",
    ),
    "claude_cli": ProviderSpec(
        key="claude_cli",
        label="Claude Code CLI (~23 s, bez klucza)",
        kind=CLI,
        base_url=None,
        env_var=None,
        models=("claude-haiku-4-5", "claude-sonnet-5"),
        hosting="Anthropic, przez Twoja subskrypcje Claude Code",
    ),
}

DEFAULT_PROVIDER = "anthropic"
PROVIDER_KEYS: tuple[str, ...] = tuple(PROVIDERS)


def spec(key: str) -> ProviderSpec:
    return PROVIDERS.get(key, PROVIDERS[DEFAULT_PROVIDER])


def default_model(provider_key: str) -> str:
    return spec(provider_key).models[0]


def supports_model(provider_key: str, model: str) -> bool:
    return model in spec(provider_key).models
