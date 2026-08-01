"""Backends that turn a system prompt + transcript into cleaned text."""

from __future__ import annotations

import logging
import subprocess
from typing import Protocol

from . import credentials

log = logging.getLogger(__name__)

# Models that run adaptive thinking when `thinking` is omitted. For a cleanup
# task thinking is pure latency, so these get it switched off explicitly.
# Haiku 4.5 and other pre-4.6 models are absent on purpose: they do not think
# unless asked, and they reject the `effort` parameter outright.
_THINKS_BY_DEFAULT = frozenset(
    {"claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-mythos-5"}
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


class AnthropicProvider:
    """Direct Messages API call. Fast (~1 s on Haiku), needs an API key."""

    name = "anthropic"

    def complete(self, system: str, user: str, model: str, timeout: float) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("Brak pakietu anthropic. Uruchom: pip install anthropic") from exc

        key = credentials.get_api_key()
        if not key:
            raise ProviderError(
                "Brak klucza API. Ustaw go: .\\run.ps1 -SetApiKey (albo z menu tray)"
            )

        # max_retries=1: a dictation is interactive. Two retries with backoff can
        # outlast the user's patience, and the fail-soft path already pastes the
        # raw transcript.
        client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)

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
            raise ProviderError(f"Klucz API odrzucony: {exc}") from exc
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

        text = "".join(block.text for block in message.content if block.type == "text")
        if message.stop_reason == "max_tokens":
            log.warning("Odpowiedz LLM ucieta na max_tokens - wklejam surowy tekst")
            raise ProviderError("Odpowiedz LLM zostala ucieta")
        return text

    def check(self) -> str | None:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "brak pakietu anthropic"
        if not credentials.get_api_key():
            return "brak klucza API"
        return None


class ClaudeCliProvider:
    """Shells out to the Claude Code CLI. No API key, but ~6 s per call."""

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
        return completed.stdout.strip()

    def check(self) -> str | None:
        import shutil

        if shutil.which(self.executable) is None:
            return f"nie znaleziono {self.executable!r} w PATH"
        return None


PROVIDERS: dict[str, str] = {
    "anthropic": "Anthropic API (szybkie, wymaga klucza)",
    "claude_cli": "Claude Code CLI (bez klucza, wolniejsze)",
}


def build(name: str, *, cli_path: str = "") -> Provider:
    if name == "claude_cli":
        return ClaudeCliProvider(cli_path)
    return AnthropicProvider()


def _max_tokens_for(user_text: str) -> int:
    """Cleaned output is never much longer than the input; size to it with slack.

    A flat 4096 truncates a long dictation, and a flat 64000 would let a runaway
    response burn tokens before max_tokens catches it.
    """
    return max(1024, min(8192, 512 + len(user_text) // 2))
