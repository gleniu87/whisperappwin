"""Backends that turn a system prompt + transcript into cleaned text."""

from __future__ import annotations

import logging
import subprocess
from typing import Protocol

from ..i18n import t
from . import credentials
from .registry import CLI, MESSAGES_API, ProviderSpec, spec

log = logging.getLogger(__name__)

# Models that run adaptive thinking when `thinking` is omitted. For a clean-up
# task thinking is pure latency, so these get it switched off explicitly.
#
# The DeepSeek models are here because they were measured, not assumed: every
# one of 4 probe calls came back with a `thinking` block. On v4-flash the
# reasoning ate the whole token budget (stop_reason=max_tokens, 1024 output
# tokens, zero text) on 100% of calls; v4-pro thought briefly but on one call in
# two returned *only* a thinking block. With thinking disabled, 6 of 6 calls
# returned clean text in 35-44 output tokens.
#
# Haiku 4.5 stays out: it does not think unless asked, and it rejects the
# `effort` parameter outright.
_THINKS_BY_DEFAULT = frozenset(
    {
        "claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-mythos-5",
        "deepseek-v4-flash", "deepseek-v4-pro",
    }
)

# Windows-only: keep a console window from flashing when the app runs under
# pythonw.exe and shells out to the CLI.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ProviderError(RuntimeError):
    """The provider could not produce a result. Always fail-soft to raw text."""


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str, model: str, timeout: float) -> str: ...

    def check(self) -> str | None:
        """Return a human-readable reason the provider is unusable, or None."""


class MessagesApiProvider:
    """Anthropic Messages API, or anything that speaks it at another base URL.

    DeepSeek's `/anthropic` endpoint is protocol-compatible, so it needs no
    separate client — only a different `base_url` and a different key.
    """

    def __init__(self, provider: ProviderSpec):
        self.spec = provider
        self.name = provider.key

    def complete(self, system: str, user: str, model: str, timeout: float) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("Brak pakietu anthropic. Uruchom: pip install anthropic") from exc

        key = credentials.get_api_key(self.name)
        if not key:
            raise ProviderError(
                f"Brak klucza API ({self.name}). Ustaw go: .\\run.ps1 -SetApiKey {self.name}"
            )

        # max_retries=1: a dictation is interactive. Two retries with backoff can
        # outlast the user's patience, and the fail-soft path already pastes the
        # raw transcript.
        client_args = {"api_key": key, "timeout": timeout, "max_retries": 1}
        if self.spec.base_url:
            client_args["base_url"] = self.spec.base_url
        client = anthropic.Anthropic(**client_args)

        request = {
            "model": model,
            "max_tokens": _max_tokens_for(user),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if model in _THINKS_BY_DEFAULT:
            request["thinking"] = {"type": "disabled"}

        try:
            message = client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise ProviderError(f"Klucz API ({self.name}) odrzucony: {exc}") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"Nieznany model {model!r}: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(f"Limit zapytan API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Blad API ({exc.status_code}): {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Brak polaczenia z API: {exc}") from exc

        # A safety decline returns HTTP 200 with an empty or partial body, so
        # this has to be checked before reading content, not caught as an error.
        if message.stop_reason == "refusal":
            raise ProviderError("Model odmowil przetworzenia tej transkrypcji")
        if message.stop_reason == "max_tokens":
            raise ProviderError("Odpowiedz LLM zostala ucieta na max_tokens")

        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            # A reply with no text block is a failure, and it must be raised as
            # one. Returning "" would reach `output_filter` looking exactly like
            # the EMPTY sentinel — "that was only noise, paste nothing" — so a
            # broken provider would silently swallow the dictation instead of
            # falling back to the raw transcript. Observed live: deepseek-v4-pro
            # returned a lone `thinking` block with stop_reason=end_turn.
            kinds = ", ".join(sorted({block.type for block in message.content})) or "brak"
            raise ProviderError(f"Odpowiedz LLM nie zawiera tekstu (bloki: {kinds})")
        return text

    def check(self) -> str | None:
        """Translated: this one goes into a tray balloon, unlike ProviderError."""
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return t("provider.problem.no_package")
        if not credentials.get_api_key(self.name):
            return t("provider.problem.no_key", provider=self.name)
        return None


class ClaudeCliProvider:
    """Shells out to the Claude Code CLI. No API key, but ~23 s per call."""

    name = "claude_cli"

    def __init__(self, executable: str = "claude"):
        self.executable = executable or "claude"

    def complete(self, system: str, user: str, model: str, timeout: float) -> str:
        argv = [
            self.executable,
            "--print",
            "--model", model,
            "--system-prompt", system,
            user,
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                # claude waits on stdin when --print is set; hand it EOF so it
                # starts immediately instead of blocking for its input timeout.
                stdin=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise ProviderError(f"Nie znaleziono {self.executable!r} w PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"Claude CLI nie odpowiedzial w {timeout:.0f} s") from exc
        except OSError as exc:
            raise ProviderError(f"Nie moge uruchomic {self.executable!r}: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or "").strip() or f"kod wyjscia {completed.returncode}"
            raise ProviderError(f"Claude CLI: {detail}")

        text = completed.stdout.strip()
        if not text:
            # Same trap as in MessagesApiProvider: an empty result would read as
            # the EMPTY sentinel and drop the dictation instead of pasting it raw.
            detail = (completed.stderr or "").strip()
            raise ProviderError(
                "Claude CLI zwrocil pusta odpowiedz" + (f": {detail}" if detail else "")
            )
        return text

    def check(self) -> str | None:
        import shutil

        if shutil.which(self.executable) is None:
            return t("provider.problem.not_in_path", executable=repr(self.executable))
        return None


def build(name: str, *, cli_path: str = "") -> Provider:
    provider = spec(name)
    if provider.kind == CLI:
        return ClaudeCliProvider(cli_path)
    assert provider.kind == MESSAGES_API
    return MessagesApiProvider(provider)


def _max_tokens_for(user_text: str) -> int:
    """Cleaned output is never much longer than the input; size to it with slack.

    A flat 4096 truncates a long dictation, and a flat 64000 would let a runaway
    response burn tokens before max_tokens catches it.
    """
    return max(1024, min(8192, 512 + len(user_text) // 2))
