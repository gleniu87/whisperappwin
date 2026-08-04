"""Backends that turn a system prompt + transcript into cleaned text."""

from __future__ import annotations

import http.client
import json
import logging
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from ..i18n import t
from . import credentials
from .registry import CLI, MESSAGES_API, OPENAI_API, ProviderSpec, spec

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

# check() runs on the pystray menu-action thread (controller.set_enhancement_*),
# so it gets its own short ceiling rather than enhancement.timeout_seconds. A
# dead localhost port refuses instantly, but a base_url aimed at a firewalled
# host would otherwise freeze the tray menu for a minute.
_CHECK_TIMEOUT = 2.0

# The local provider gets its own opener, and this is a privacy control, not a
# tidiness one. urllib.request.urlopen uses the default opener, which installs a
# ProxyHandler built from the environment - and on Windows `localhost` gets no
# implicit exemption, so with HTTP_PROXY set the whole transcript is delivered to
# the proxy while the tray still says nothing leaves the machine. Measured on
# this machine: proxy_bypass("localhost:11434") is False once HTTP_PROXY is set.
#
# Passing an empty ProxyHandler is what disables proxying: build_opener then skips
# installing the default, environment-reading one. (The empty instance registers
# no methods of its own, so it does not appear among the opener's handlers - the
# absence of any ProxyHandler there is the property that matters.)
class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses to follow redirects, so the address vetted is the address used.

    A 30x is otherwise a way for a server - compromised, or merely a reverse
    proxy someone pointed elsewhere - to move the transcript off the machine
    after `base_url` had already been checked and reported as local. Returning
    None makes urllib raise the HTTPError instead of following it.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_LOCAL_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirects()
)

#: Failures urllib can raise that are not URLError. http.client exceptions escape
#: it entirely (a plain-TCP or TLS port answered over http:// raises
#: BadStatusLine), and ValueError covers urllib's own InvalidURL - "nonnumeric
#: port" for a base_url left as http://host:port/v1. Without these, check() would
#: propagate out of the pystray callback and --check would end in a traceback.
_TRANSPORT_ERRORS = (OSError, http.client.HTTPException, ValueError)


def _redacted(url: str) -> str:
    """`url` with credentials and query removed, for logs and tray balloons.

    base_url is free text in a plaintext config, so it can carry a token -
    http://localhost:11434/v1?api_key=... or http://user:secret@host/v1. Both
    would otherwise reach the log through ProviderError and a notification
    through check(), which is exactly what this project keeps keys out of.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "(unparseable URL)"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


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
            raise ProviderError("The anthropic package is missing. Run: pip install anthropic") from exc

        key = credentials.get_api_key(self.name)
        if not key:
            raise ProviderError(
                f"No API key ({self.name}). Set one with: .\\run.ps1 -SetApiKey {self.name}"
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
            raise ProviderError(f"API key ({self.name}) rejected: {exc}") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"Unknown model {model!r}: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(f"API rate limit reached: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"API error ({exc.status_code}): {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Cannot reach the API: {exc}") from exc

        # A safety decline returns HTTP 200 with an empty or partial body, so
        # this has to be checked before reading content, not caught as an error.
        if message.stop_reason == "refusal":
            raise ProviderError("The model declined to process this transcript")
        if message.stop_reason == "max_tokens":
            raise ProviderError("The LLM reply was cut off at max_tokens")

        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            # A reply with no text block is a failure, and it must be raised as
            # one. Returning "" would reach `output_filter` looking exactly like
            # the EMPTY sentinel — "that was only noise, paste nothing" — so a
            # broken provider would silently swallow the dictation instead of
            # falling back to the raw transcript. Observed live: deepseek-v4-pro
            # returned a lone `thinking` block with stop_reason=end_turn.
            kinds = ", ".join(sorted({block.type for block in message.content})) or "none"
            raise ProviderError(f"The LLM reply contains no text (blocks: {kinds})")
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
            raise ProviderError(f"{self.executable!r} not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"Claude CLI did not answer within {timeout:.0f} s") from exc
        except OSError as exc:
            raise ProviderError(f"Cannot start {self.executable!r}: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or "").strip() or f"exit code {completed.returncode}"
            raise ProviderError(f"Claude CLI: {detail}")

        text = completed.stdout.strip()
        if not text:
            # Same trap as in MessagesApiProvider: an empty result would read as
            # the EMPTY sentinel and drop the dictation instead of pasting it raw.
            detail = (completed.stderr or "").strip()
            raise ProviderError(
                "Claude CLI returned an empty reply" + (f": {detail}" if detail else "")
            )
        return text

    def check(self) -> str | None:
        import shutil

        if shutil.which(self.executable) is None:
            return t("provider.problem.not_in_path", executable=repr(self.executable))
        return None


class OpenAiApiProvider:
    """An OpenAI-compatible /chat/completions server on this machine.

    Named after the protocol, not the product: Ollama is the only entry in the
    registry today, but llama.cpp's server and LM Studio answer the same shape,
    and `enhancement.base_url` is what points this at one of them.

    Uses urllib rather than an SDK. The request is one JSON POST, so a dependency
    would buy nothing, and the clean-up feature is meant to work on a machine
    where `pip install anthropic` was never run.
    """

    def __init__(self, provider: ProviderSpec, *, base_url: str = "", configured_model: str = ""):
        self.spec = provider
        self.name = provider.key
        # rstrip once, here, so every caller can be careless about the slash.
        self.base_url = (base_url or provider.base_url or "").rstrip("/")
        #: True when the user redirected this away from the registry's address.
        #: Two things hang off it: check() stops making an Ollama-specific claim
        #: about the model, and hosting() stops promising that nothing leaves the
        #: machine, which stops being true the moment the address is not local.
        self.overridden = bool(base_url) and base_url.rstrip("/") != provider.base_url
        #: Read only by check(), to answer "is that model actually pulled". The
        #: `model` argument of complete() stays authoritative for the request
        #: itself - these two come from the same Config within one call, because
        #: EnhancementService rebuilds the provider on every enhance().
        self.configured_model = configured_model

    def complete(self, system: str, user: str, model: str, timeout: float) -> str:
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": _max_tokens_for(user),
            # Cleaning a transcript has one right answer per input; sampling only
            # adds variance between two runs on the same dictation.
            "temperature": 0.0,
            # Explicit: with a single non-streaming response the socket timeout
            # below acts as an overall ceiling rather than a per-chunk one.
            "stream": False,
            # Measured, and the whole reason this provider is usable: qwen3.5:4b
            # left to think came back with finish_reason=length, 1024 completion
            # tokens and content='' on *every* call, taking 14.1 s to say
            # nothing. With reasoning_effort=none it answered correctly in 2.9 s.
            # The 1024 it burned is exactly _max_tokens_for's floor.
            #
            # Sent unconditionally rather than keyed off _THINKS_BY_DEFAULT: the
            # model list here is the user's, not ours, so we cannot enumerate
            # which of their models think. Servers and models that do not know
            # the field ignore it - and one measured model, deepseek-r1:8b,
            # ignores it while thinking anyway (821-character reasoning field,
            # 9.5 s). That is why the empty-content check below still has to
            # exist: this flag is a mitigation, not a contract.
            "reasoning_effort": "none",
        }

        body = self._post("/chat/completions", request, timeout)

        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Malformed reply from {self.base_url}: {exc}") from exc

        if choice.get("finish_reason") == "length":
            raise ProviderError("The LLM reply was cut off at max_tokens")

        text = (message.get("content") or "").strip()
        if not text:
            # This is the trap that makes the whole provider dangerous without
            # the check. Ollama returns reasoning in a *separate* `reasoning`
            # field, so a thinking model leaves content='' - and "" is exactly
            # what output_filter produces for the EMPTY sentinel, i.e. "that was
            # only noise, paste nothing". controller.py would then paste nothing
            # and drop the dictation instead of falling back to the raw text.
            # Measured on qwen3.5:4b before reasoning_effort was added.
            reasoning = str(message.get("reasoning") or "")
            detail = f", {len(reasoning)} characters of reasoning" if reasoning else ""
            raise ProviderError(f"The LLM reply contains no text{detail}")
        return text

    def check(self) -> str | None:
        """Translated: this one goes into a tray balloon, unlike ProviderError.

        Deliberately hits /models and not /chat/completions - a readiness probe
        that loads 5.6 GB into VRAM is not a readiness probe.
        """
        try:
            body = self._get("/models", _CHECK_TIMEOUT)
        except ProviderError:
            return t("provider.problem.server_unreachable", url=_redacted(self.base_url))

        # The name check is Ollama-specific, so it only runs against Ollama. A
        # llama.cpp server reports a file path as its model id and LM Studio its
        # own alias, so comparing would produce a permanent false "not pulled"
        # balloon - and --quality would skip the provider entirely - for exactly
        # the setup enhancement.base_url exists to support.
        if not self.configured_model or self.overridden:
            return None
        available = {
            str(entry.get("id")) for entry in (body.get("data") or []) if isinstance(entry, dict)
        }
        if available and self.configured_model not in available:
            return t("provider.problem.model_not_pulled", model=self.configured_model)
        return None

    # -- transport ------------------------------------------------------

    def _endpoint(self, path: str) -> str:
        """base_url + path, joined on the path component rather than the string.

        Concatenating breaks on anything after the path: a base_url carrying a
        query or fragment would produce ".../v1?api_key=x/chat/completions", which
        404s on every dictation while check() reports the server as unreachable.
        config._url_or_blank rejects those, so this is the second line of defence
        for a value that arrived some other way.
        """
        parts = urllib.parse.urlsplit(self.base_url)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + path, "", "")
        )

    def _post(self, path: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        request = urllib.request.Request(
            self._endpoint(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(request, timeout)

    def _get(self, path: str, timeout: float) -> dict[str, object]:
        return self._send(urllib.request.Request(self._endpoint(path), method="GET"), timeout)

    def _send(self, request: urllib.request.Request, timeout: float) -> dict[str, object]:
        where = _redacted(self.base_url)
        try:
            # _LOCAL_OPENER, not urlopen: see the note on it. A configured proxy
            # would otherwise receive the transcript.
            with _LOCAL_OPENER.open(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8", errors="replace")
        # HTTPError first: it subclasses URLError, so the order matters. Ollama
        # answers an un-pulled model with 404 and a body that names it. The body
        # read is inside its own try because a server that sends headers and then
        # stalls makes exc.read() raise, and that would escape this handler.
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200].strip()
            except _TRANSPORT_ERRORS:
                detail = ""
            raise ProviderError(f"Local server error ({exc.code}): {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            # Connection refused arrives here, and it is the common case: the
            # server simply is not running.
            raise ProviderError(f"Cannot reach the local server at {where}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError(f"The local server did not answer within {timeout:.0f} s") from exc
        except _TRANSPORT_ERRORS as exc:
            # Everything else the transport can throw, so that no failure escapes
            # as a non-ProviderError: check() is called from a pystray callback
            # and from --check, and neither survives an unexpected exception.
            raise ProviderError(f"Cannot reach the local server at {where}: {exc}") from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"The local server returned invalid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"The local server returned {type(body).__name__}, not an object")
        return body


def build(name: str, *, cli_path: str = "", base_url: str = "", model: str = "") -> Provider:
    provider = spec(name)
    if provider.kind == CLI:
        return ClaudeCliProvider(cli_path)
    if provider.kind == OPENAI_API:
        return OpenAiApiProvider(provider, base_url=base_url, configured_model=model)
    assert provider.kind == MESSAGES_API
    # base_url is deliberately not forwarded here. Redirecting a key-carrying
    # provider would ship the user's API key to an arbitrary host and make
    # registry.hosting() - which is how they judge the jurisdiction - a lie.
    return MessagesApiProvider(provider)


def _max_tokens_for(user_text: str) -> int:
    """Cleaned output is never much longer than the input; size to it with slack.

    A flat 4096 truncates a long dictation, and a flat 64000 would let a runaway
    response burn tokens before max_tokens catches it.
    """
    return max(1024, min(8192, 512 + len(user_text) // 2))
