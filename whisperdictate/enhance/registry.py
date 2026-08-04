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
#: An OpenAI-compatible /chat/completions endpoint. Ollama is the only instance
#: today, but llama.cpp and LM Studio speak the same protocol, which is why the
#: kind is named after the protocol and not the product.
OPENAI_API = "openai_api"


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    #: Bare product name. The trade-off hint next to it in the menu, and the
    #: jurisdiction, are translated - see label_with_hint() and hosting().
    label: str
    kind: str
    #: For MESSAGES_API, None means the SDK's own default (api.anthropic.com).
    #: For OPENAI_API it is the *default* endpoint, which `enhancement.base_url`
    #: overrides - the server is the user's own, so its address has to be theirs
    #: too. The override never reaches a key-carrying provider; see providers.build().
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
    # Last on purpose: appending keeps the tray order and --benchmark output
    # stable for anyone already using the three above.
    "ollama": ProviderSpec(
        key="ollama",
        label="Ollama (local)",
        kind=OPENAI_API,
        base_url="http://localhost:11434/v1",
        # No key, and nothing to leak: the request never leaves the machine.
        env_var=None,
        # Order is measured, on 76 real transcripts from this machine's own
        # history (61 Polish) plus 8 hand-written English ones:
        #
        #   qwen3.5:9b  5.6 GB, 100% GPU beside a resident large-v3-turbo
        #               (7774/8192 MiB), p50 3.61 s / p90 5.86 s. Removes Polish
        #               fillers at least as well as DeepSeek (15/20 left against
        #               17/20), kept 15/15 identifiers, leaked no
        #               meta-commentary, and put no Polish into English output.
        #               Lost 1 of 76 transcripts ("Voilà.").
        #   gemma3:4b   the cheap fallback, and only that: fastest of all
        #               (p50 3.32 s) and lost nothing, but removed *no* Polish
        #               filler at all (20/20 left) and missed the EMPTY sentinel
        #               0/2. It smooths text rather than cleaning it.
        #   gemma4:12b  cleans best of everything measured - 3/20 fillers left,
        #               edit ratio 0.196 against DeepSeek's 0.198 - but does not
        #               fit beside Whisper: 31% of its layers land on the CPU and
        #               it costs p50 11.45 s / p90 32.98 s.
        #
        # Deliberately absent: qwen3.5:4b lost 3 of 76, among them
        # "Zaproponuj następne zadania" - a valid command - and it answered short
        # transcripts instead of cleaning them. bielik-4.5b:Q8_0 is Polish-first
        # and fast, but prefixed "Oto oczyszczony tekst:" onto 42 of 76 replies.
        # deepseek-r1:8b ignores reasoning_effort outright (an 821-character
        # reasoning field regardless) at 9.5 s, and translated English to Polish.
        models=("qwen3.5:9b", "gemma3:4b", "gemma4:12b"),
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


#: Hosts that mean "this machine". Anything else, however private the network,
#: is somewhere the transcript travels to.
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", ""})


def hosting(provider_key: str, base_url: str = "") -> str:
    """Where the request goes. Surfaced in --check, the key dialog and the docs,
    so the jurisdiction is chosen knowingly rather than discovered later.

    Keyed off spec().key rather than the argument, so an unknown provider name
    describes the fallback provider it will actually use instead of returning a
    lookup miss.

    `base_url` is the effective address when the user overrode it. It matters
    because "nothing leaves the machine" is the whole claim of the local provider
    and it stops being true the moment that address is not loopback - and a
    LAN address is a documented, supported configuration. Passed as a plain
    string so this module stays free of config imports.
    """
    provider = spec(provider_key)
    if provider.kind == OPENAI_API and base_url:
        host = _host_of(base_url)
        if host not in _LOOPBACK:
            return t("provider.ollama.hosting_remote", host=host)
    return t(f"provider.{provider.key}.hosting")


def _host_of(url: str) -> str:
    """Hostname only - no credentials, no port, nothing worth redacting."""
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return url


def default_model(provider_key: str) -> str:
    return spec(provider_key).models[0]


def supports_model(provider_key: str, model: str) -> bool:
    return model in spec(provider_key).models
