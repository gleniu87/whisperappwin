"""Provider registry, model pairing, and credential isolation.

DeepSeek speaks the Anthropic Messages protocol, so the same client class serves
both — which makes it easy to accidentally send one provider's model name, or
one provider's key, to the other. These cover that seam.
"""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from whisperdictate import i18n
from whisperdictate.config import Config
from whisperdictate.controller import DictationController
from whisperdictate.enhance import credentials, providers, registry
from whisperdictate.enhance.providers import (
    ClaudeCliProvider,
    MessagesApiProvider,
    OpenAiApiProvider,
)


class RegistryTest(unittest.TestCase):
    def test_deepseek_uses_the_anthropic_compatible_endpoint(self):
        self.assertEqual(
            registry.spec("deepseek").base_url, "https://api.deepseek.com/anthropic"
        )

    def test_anthropic_uses_the_sdk_default_base_url(self):
        self.assertIsNone(registry.spec("anthropic").base_url)

    def test_providers_have_distinct_env_vars(self):
        env_vars = [s.env_var for s in registry.PROVIDERS.values() if s.env_var]
        self.assertEqual(len(env_vars), len(set(env_vars)))

    def test_cli_provider_needs_no_key(self):
        self.assertIsNone(registry.spec("claude_cli").env_var)

    def test_every_provider_declares_where_traffic_goes(self):
        """Hosting is a decision input, not a footnote — it must never be blank."""
        for key in registry.PROVIDERS:
            self.assertTrue(registry.hosting(key).strip(), key)

    def test_hosting_note_flags_the_jurisdiction_in_both_languages(self):
        """The one fact that must survive translation, because acting on the
        wrong reading of it means sending work material to another country."""
        i18n.use("pl")
        self.assertIn("Chiny", registry.hosting("deepseek"))
        i18n.use("en")
        self.assertIn("China", registry.hosting("deepseek"))

    def test_menu_label_carries_the_product_name_and_the_hint(self):
        i18n.use("pl")
        self.assertEqual(registry.label_with_hint("deepseek"), "DeepSeek API (~10x tańszy)")

    def test_hosting_of_an_unknown_provider_describes_the_fallback(self):
        self.assertEqual(registry.hosting("nonsense"), registry.hosting(registry.DEFAULT_PROVIDER))

    def test_unknown_provider_falls_back_to_the_default(self):
        self.assertEqual(registry.spec("nonsense").key, registry.DEFAULT_PROVIDER)

    def test_model_membership_is_per_provider(self):
        self.assertTrue(registry.supports_model("deepseek", "deepseek-v4-flash"))
        self.assertFalse(registry.supports_model("deepseek", "claude-haiku-4-5"))
        self.assertFalse(registry.supports_model("anthropic", "deepseek-v4-flash"))

    def test_default_model_is_the_first_listed(self):
        self.assertEqual(registry.default_model("deepseek"), "deepseek-v4-flash")
        self.assertEqual(registry.default_model("anthropic"), "claude-haiku-4-5")

    def test_the_local_provider_points_at_ollama_by_default(self):
        self.assertEqual(registry.spec("ollama").base_url, "http://localhost:11434/v1")

    def test_the_local_provider_needs_no_key(self):
        """There is no key to have: the request never leaves the machine."""
        self.assertIsNone(registry.spec("ollama").env_var)

    def test_local_defaults_to_the_model_that_did_not_lose_dictations(self):
        """Measured over 76 real transcripts: qwen3.5:4b reduced 3 of them to
        nothing, one being the valid command "Zaproponuj następne zadania", and
        qwen3.5:9b only 1 while cleaning Polish fillers at least as well as
        DeepSeek. Reordering this puts the losing model back in front."""
        self.assertEqual(registry.default_model("ollama"), "qwen3.5:9b")

    def test_local_hosting_says_nothing_leaves_the_machine_in_both_languages(self):
        """The counterpart to the DeepSeek jurisdiction test, and the entire
        reason this provider exists - so it has to survive translation too."""
        i18n.use("pl")
        self.assertIn("nie opuszcza", registry.hosting("ollama"))
        i18n.use("en")
        self.assertIn("nothing leaves", registry.hosting("ollama"))

    def test_cli_defaults_to_sonnet_because_haiku_times_out_there(self):
        """Measured, not assumed: haiku through the CLI ran 19.8-60+ s over 5
        runs and timed out twice; sonnet stayed at 4.2-5.7 s. Flipping this back
        makes the default CLI model the one that misses the 60 s ceiling."""
        self.assertEqual(registry.default_model("claude_cli"), "claude-sonnet-5")


class ProviderBuildTest(unittest.TestCase):
    def test_builds_a_messages_client_for_deepseek(self):
        provider = providers.build("deepseek")
        self.assertIsInstance(provider, MessagesApiProvider)
        self.assertEqual(provider.name, "deepseek")
        self.assertEqual(provider.spec.base_url, "https://api.deepseek.com/anthropic")

    def test_builds_the_cli_provider(self):
        self.assertIsInstance(providers.build("claude_cli"), ClaudeCliProvider)

    def test_cli_path_override_is_honoured(self):
        self.assertEqual(providers.build("claude_cli", cli_path="C:/x/claude.exe").executable,
                         "C:/x/claude.exe")

    def test_builds_the_local_provider(self):
        provider = providers.build("ollama")
        self.assertIsInstance(provider, OpenAiApiProvider)
        self.assertEqual(provider.name, "ollama")
        self.assertEqual(provider.base_url, "http://localhost:11434/v1")

    def test_a_configured_base_url_replaces_the_default(self):
        provider = providers.build("ollama", base_url="http://10.0.0.5:8080/v1")
        self.assertEqual(provider.base_url, "http://10.0.0.5:8080/v1")

    def test_a_trailing_slash_does_not_double_up(self):
        provider = providers.build("ollama", base_url="http://localhost:1234/v1/")
        self.assertEqual(provider.base_url, "http://localhost:1234/v1")

    def test_the_base_url_override_never_reaches_a_provider_with_a_key(self):
        """Redirecting a keyed provider would post the user's API key to whatever
        host was typed into the config, and make registry.hosting() - the thing
        they judge the jurisdiction by - a lie."""
        provider = providers.build("deepseek", base_url="http://evil.example/v1")
        self.assertEqual(provider.spec.base_url, "https://api.deepseek.com/anthropic")

    def test_the_configured_model_is_threaded_in_for_check(self):
        provider = providers.build("ollama", model="qwen3.5:9b")
        self.assertEqual(provider.configured_model, "qwen3.5:9b")


class CredentialIsolationTest(unittest.TestCase):
    """Each provider reads its own env var; neither shadows the other."""

    def test_env_vars_are_read_per_provider(self):
        with mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x", "DEEPSEEK_API_KEY": "sk-ds-y"}
        ):
            self.assertEqual(credentials.get_api_key("anthropic"), "sk-ant-x")
            self.assertEqual(credentials.get_api_key("deepseek"), "sk-ds-y")

    def test_anthropic_key_does_not_satisfy_deepseek(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}, clear=False):
            with mock.patch.object(credentials, "_read_credential", lambda _t: None):
                self.assertIsNone(credentials.get_api_key("deepseek"))

    def test_credential_targets_are_distinct(self):
        self.assertNotEqual(
            credentials.target_for("anthropic"), credentials.target_for("deepseek")
        )

    def test_blank_env_var_falls_through_to_the_store(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "   "}):
            with mock.patch.object(credentials, "_read_credential", lambda _t: "stored"):
                self.assertEqual(credentials.get_api_key("deepseek"), "stored")


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Message:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class EmptyReplyTest(unittest.TestCase):
    """A reply with no text must raise, never return "".

    `output_filter` maps "" to the EMPTY sentinel — "that was only noise, paste
    nothing". A provider that returns "" on failure would therefore delete the
    user's dictation instead of falling back to the raw transcript. Observed
    live: deepseek-v4-pro answered with a lone `thinking` block and
    stop_reason=end_turn, which is a success as far as the API is concerned.
    """

    def _complete(self, message):
        provider = MessagesApiProvider(registry.spec("deepseek"))
        client = mock.Mock()
        client.messages.create.return_value = message
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-ds-x"}):
            with mock.patch("anthropic.Anthropic", return_value=client):
                return provider.complete("sys", "user", "deepseek-v4-pro", 30.0)

    def test_thinking_only_reply_raises(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._complete(_Message([_Block("thinking")]))
        self.assertIn("contains no text", str(caught.exception))

    def test_error_names_the_blocks_that_did_arrive(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._complete(_Message([_Block("thinking")]))
        self.assertIn("thinking", str(caught.exception))

    def test_whitespace_only_text_raises(self):
        with self.assertRaises(providers.ProviderError):
            self._complete(_Message([_Block("text", "   \n  ")]))

    def test_reply_with_no_blocks_at_all_raises(self):
        with self.assertRaises(providers.ProviderError):
            self._complete(_Message([]))

    def test_real_text_alongside_thinking_is_returned(self):
        message = _Message([_Block("thinking", "considering"), _Block("text", "Done.")])
        self.assertEqual(self._complete(message), "Done.")

    def test_literal_empty_sentinel_still_gets_through(self):
        """EMPTY is a real answer; only an absent one is an error."""
        self.assertEqual(self._complete(_Message([_Block("text", "EMPTY")])), "EMPTY")


class ThinkingDisabledTest(unittest.TestCase):
    """DeepSeek thinks unless told not to — measured, not assumed.

    Left on, v4-flash spent its entire token budget reasoning and returned no
    text at all on every call.
    """

    def _request_for(self, model, provider_key="deepseek"):
        provider = MessagesApiProvider(registry.spec(provider_key))
        client = mock.Mock()
        client.messages.create.return_value = _Message([_Block("text", "Done.")])
        env = {"DEEPSEEK_API_KEY": "sk-ds-x", "ANTHROPIC_API_KEY": "sk-ant-x"}
        with mock.patch.dict("os.environ", env):
            with mock.patch("anthropic.Anthropic", return_value=client):
                provider.complete("sys", "user", model, 30.0)
        return client.messages.create.call_args.kwargs

    def test_deepseek_models_ask_for_thinking_off(self):
        for model in registry.spec("deepseek").models:
            self.assertEqual(
                self._request_for(model).get("thinking"), {"type": "disabled"}, model
            )

    def test_haiku_is_left_alone(self):
        """Haiku 4.5 does not think unless asked and rejects the parameter."""
        self.assertNotIn("thinking", self._request_for("claude-haiku-4-5", "anthropic"))


class CliEmptyOutputTest(unittest.TestCase):
    def _run(self, stdout, stderr="", returncode=0):
        completed = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch("subprocess.run", return_value=completed):
            return ClaudeCliProvider("claude").complete("sys", "user", "m", 30.0)

    def test_empty_stdout_raises_instead_of_reading_as_empty_sentinel(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._run("   \n ")
        self.assertIn("empty reply", str(caught.exception))

    def test_stderr_is_quoted_when_there_is_one(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._run("", stderr="usage limit reached")
        self.assertIn("usage limit reached", str(caught.exception))

    def test_normal_output_is_returned_stripped(self):
        self.assertEqual(self._run("  Done.\n"), "Done.")


def _reply(content="Done.", *, finish_reason="stop", **extra):
    message = {"role": "assistant", "content": content}
    message.update(extra)
    return {"choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}


class _LocalCall:
    """Captures what the provider put on the wire, and answers with `payload`."""

    def __init__(self, payload, *, raw=None):
        self.payload = payload
        self.raw = raw
        self.requests = []

    def __call__(self, request, timeout=None):  # noqa: ANN001 - stands in for urlopen
        self.requests.append((request, timeout))
        body = self.raw if self.raw is not None else json.dumps(self.payload)
        return io.BytesIO(body.encode("utf-8"))

    @property
    def sent(self) -> dict:
        return json.loads(self.requests[0][0].data.decode("utf-8"))

    @property
    def url(self) -> str:
        return self.requests[0][0].full_url


@contextlib.contextmanager
def _local(fake):
    # Patches the provider's own opener, not urllib.request.urlopen: the provider
    # deliberately uses a private opener with proxying disabled, and patching the
    # module function would let the real request through.
    with mock.patch.object(providers._LOCAL_OPENER, "open", new=fake):
        yield fake


def _complete(fake, *, model="qwen3.5:9b", user="<TRANSCRIPT>\nx\n</TRANSCRIPT>"):
    with _local(fake):
        return providers.build("ollama").complete("sys", user, model, 30.0)


class OpenAiApiRequestTest(unittest.TestCase):
    def test_thinking_is_switched_off_on_every_request(self):
        """Measured, and the reason this provider is usable at all: qwen3.5:4b
        left to think answered with finish_reason=length, 1024 completion tokens
        and content='' on every single call, taking 14.1 s to say nothing. With
        reasoning_effort=none it cleaned the same transcript in 2.9 s."""
        fake = _LocalCall(_reply())
        _complete(fake)
        self.assertEqual(fake.sent["reasoning_effort"], "none")

    def test_sampling_is_off_and_the_reply_is_not_streamed(self):
        fake = _LocalCall(_reply())
        _complete(fake)
        self.assertEqual(fake.sent["temperature"], 0.0)
        self.assertIs(fake.sent["stream"], False)

    def test_max_tokens_is_sized_to_the_transcript(self):
        fake = _LocalCall(_reply())
        user = "<TRANSCRIPT>\n" + ("a" * 4000) + "\n</TRANSCRIPT>"
        _complete(fake, user=user)
        self.assertEqual(fake.sent["max_tokens"], providers._max_tokens_for(user))

    def test_it_posts_to_the_chat_completions_path(self):
        fake = _LocalCall(_reply())
        _complete(fake)
        self.assertEqual(fake.url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(fake.requests[0][0].get_header("Content-type"), "application/json")

    def test_the_system_prompt_and_transcript_travel_as_two_messages(self):
        fake = _LocalCall(_reply())
        _complete(fake)
        roles = [m["role"] for m in fake.sent["messages"]]
        self.assertEqual(roles, ["system", "user"])

    def test_the_model_argument_wins_over_the_stored_one(self):
        """configured_model exists only so check() can ask "is it pulled". If it
        ever decided the request, the two copies could drift apart silently."""
        fake = _LocalCall(_reply())
        with _local(fake):
            providers.build("ollama", model="gemma3:4b").complete(
                "sys", "user", "qwen3.5:9b", 30.0
            )
        self.assertEqual(fake.sent["model"], "qwen3.5:9b")


class OpenAiApiEmptyReplyTest(unittest.TestCase):
    """A reply with no text must raise, never return "".

    Sharper here than for the other providers: Ollama returns reasoning in a
    *separate* `reasoning` field, so a thinking model leaves content='' - and ""
    is exactly what output_filter produces for the EMPTY sentinel. Returning it
    would make controller.py paste nothing and drop the dictation, instead of
    falling back to the raw transcript. Measured on qwen3.5:4b.
    """

    def test_empty_content_raises_instead_of_reading_as_the_sentinel(self):
        with self.assertRaises(providers.ProviderError) as caught:
            _complete(_LocalCall(_reply("")))
        self.assertIn("no text", str(caught.exception))

    def test_the_error_names_the_reasoning_that_did_arrive(self):
        """deepseek-r1:8b ignores reasoning_effort and returns an 821-character
        reasoning field regardless, so the size is the diagnostic."""
        fake = _LocalCall(_reply("", reasoning="x" * 821))
        with self.assertRaises(providers.ProviderError) as caught:
            _complete(fake)
        self.assertIn("821", str(caught.exception))

    def test_whitespace_only_content_raises(self):
        with self.assertRaises(providers.ProviderError):
            _complete(_LocalCall(_reply("   \n  ")))

    def test_a_truncated_reply_raises(self):
        with self.assertRaises(providers.ProviderError) as caught:
            _complete(_LocalCall(_reply("half a sen", finish_reason="length")))
        self.assertIn("max_tokens", str(caught.exception))

    def test_the_literal_empty_sentinel_still_gets_through(self):
        """EMPTY is a real answer; only an absent one is an error."""
        self.assertEqual(_complete(_LocalCall(_reply("EMPTY"))), "EMPTY")


class OpenAiApiFailureTest(unittest.TestCase):
    def _error(self, exc):
        with mock.patch.object(providers._LOCAL_OPENER, "open", side_effect=exc):
            with self.assertRaises(providers.ProviderError) as caught:
                providers.build("ollama").complete("sys", "user", "qwen3.5:9b", 30.0)
        return str(caught.exception)

    def test_a_server_that_is_not_running_is_reported_with_its_address(self):
        """The common case by far: Ollama simply is not started."""
        message = self._error(urllib.error.URLError(ConnectionRefusedError(61, "refused")))
        self.assertIn("localhost:11434", message)

    def test_a_timeout_is_reported_as_one(self):
        self.assertIn("did not answer", self._error(TimeoutError("timed out")))

    def test_an_unpulled_model_surfaces_the_servers_own_explanation(self):
        """Ollama answers 404 with a body naming the model and telling you to
        pull it - far more useful than the status code alone."""
        body = io.BytesIO(b'{"error":"model \'qwen3.5:9b\' not found, try pulling it first"}')
        error = urllib.error.HTTPError(
            "http://localhost:11434/v1/chat/completions", 404, "Not Found", {}, body
        )
        message = self._error(error)
        self.assertIn("404", message)
        self.assertIn("try pulling it first", message)

    def test_a_non_json_reply_raises_rather_than_crashing(self):
        fake = _LocalCall(None, raw="<html>proxy error</html>")
        with self.assertRaises(providers.ProviderError) as caught:
            _complete(fake)
        self.assertIn("invalid JSON", str(caught.exception))

    def test_a_reply_without_choices_raises(self):
        fake = _LocalCall({"object": "error"})
        with self.assertRaises(providers.ProviderError) as caught:
            _complete(fake)
        self.assertIn("Malformed", str(caught.exception))


class OpenAiApiCheckTest(unittest.TestCase):
    def setUp(self):
        i18n.use("en")
        self.addCleanup(i18n.use, "pl")

    def test_an_unreachable_server_is_named_in_the_interface_language(self):
        with mock.patch.object(
            providers._LOCAL_OPENER, "open", side_effect=urllib.error.URLError("down")
        ):
            problem = providers.build("ollama", model="qwen3.5:9b").check()
        self.assertIn("no local server", problem)
        self.assertIn("localhost:11434", problem)

    def test_a_model_that_is_not_pulled_is_named_with_the_pull_command(self):
        fake = _LocalCall({"data": [{"id": "gemma3:4b"}]})
        with _local(fake):
            problem = providers.build("ollama", model="qwen3.5:9b").check()
        self.assertIn("qwen3.5:9b", problem)
        self.assertIn("ollama pull", problem)

    def test_a_pulled_model_reports_no_problem(self):
        fake = _LocalCall({"data": [{"id": "qwen3.5:9b"}, {"id": "gemma3:4b"}]})
        with _local(fake):
            self.assertIsNone(providers.build("ollama", model="qwen3.5:9b").check())

    def test_the_probe_never_loads_the_model(self):
        """A readiness check that pulls 5.6 GB into VRAM is not a readiness
        check - it must ask /models, not /chat/completions."""
        fake = _LocalCall({"data": [{"id": "qwen3.5:9b"}]})
        with _local(fake):
            providers.build("ollama", model="qwen3.5:9b").check()
        self.assertTrue(fake.url.endswith("/models"), fake.url)

    def test_the_probe_uses_its_own_short_ceiling(self):
        """check() runs on the pystray menu-action thread, so it must not be able
        to freeze the tray for enhancement.timeout_seconds."""
        fake = _LocalCall({"data": []})
        with _local(fake):
            providers.build("ollama", model="qwen3.5:9b").check()
        self.assertEqual(fake.requests[0][1], providers._CHECK_TIMEOUT)
        self.assertLessEqual(providers._CHECK_TIMEOUT, 5.0)


class LocalTrafficStaysLocalTest(unittest.TestCase):
    """The feature's headline claim, defended at the transport layer."""

    def test_no_proxy_handler_is_installed_at_all(self):
        """urllib's default opener builds a ProxyHandler from the environment, and
        Windows gives localhost no implicit exemption - measured on this machine,
        `proxy_bypass("localhost:11434")` is False once HTTP_PROXY is set. Through
        the default opener the whole transcript is delivered to the proxy while
        the tray still says nothing leaves the machine.

        Asserting absence rather than an empty ProxyHandler: passing one is how
        build_opener is told to skip the default, but an instance with no proxies
        registers no methods and so never joins `handlers`.
        """
        proxies = [
            h for h in providers._LOCAL_OPENER.handlers
            if isinstance(h, urllib.request.ProxyHandler)
        ]
        self.assertEqual(proxies, [])

    def test_proxy_settings_in_the_environment_are_ignored(self):
        """The behavioural half of the test above, against the real thing."""
        captured = []

        def capture(request, timeout=None):  # noqa: ANN001
            captured.append(request.full_url)
            return io.BytesIO(json.dumps(_reply()).encode("utf-8"))

        with mock.patch.dict("os.environ", {"HTTP_PROXY": "http://proxy.corp:8080"}):
            with mock.patch.object(providers._LOCAL_OPENER, "open", new=capture):
                providers.build("ollama").complete("s", "u", "qwen3.5:9b", 5.0)
        self.assertTrue(captured[0].startswith("http://localhost:11434/"), captured)

    def test_the_opener_refuses_to_follow_redirects(self):
        """A redirect is how a server could move the transcript off-box after its
        address had already been vetted and reported as local."""
        redirect = next(
            h for h in providers._LOCAL_OPENER.handlers
            if isinstance(h, urllib.request.HTTPRedirectHandler)
        )
        self.assertIsInstance(redirect, providers._NoRedirects)
        self.assertIsNone(
            redirect.redirect_request(None, None, 302, "Found", {}, "http://evil.example/v1")
        )

    def test_a_token_in_the_address_is_kept_out_of_messages(self):
        """base_url is free text in a plaintext config, so it can carry a secret.
        ProviderError reaches the log and check() reaches a tray balloon, and this
        project keeps keys out of both."""
        for url in (
            "http://user:sekret@10.0.0.9/v1",
            "http://localhost:11434/v1?api_key=sk-abc",
        ):
            with mock.patch.object(
                providers._LOCAL_OPENER, "open", side_effect=urllib.error.URLError("down")
            ):
                with self.assertRaises(providers.ProviderError) as caught:
                    providers.build("ollama", base_url=url).complete("s", "u", "m", 5.0)
            self.assertNotIn("sekret", str(caught.exception))
            self.assertNotIn("sk-abc", str(caught.exception))


class LocalEndpointJoiningTest(unittest.TestCase):
    def test_the_path_is_joined_on_the_path_component(self):
        """String concatenation put "/chat/completions" after a query string,
        producing a URL that 404s on every dictation while check() blamed the
        server for being unreachable."""
        provider = providers.build("ollama", base_url="http://localhost:11434/v1")
        self.assertEqual(
            provider._endpoint("/chat/completions"),
            "http://localhost:11434/v1/chat/completions",
        )

    def test_a_bare_root_still_produces_a_usable_path(self):
        """Ollama's own docs show the bare root; /v1 is the OpenAI-compat prefix.
        Getting it wrong should not silently mangle the URL."""
        provider = providers.build("ollama", base_url="http://localhost:11434")
        self.assertEqual(
            provider._endpoint("/models"), "http://localhost:11434/models"
        )


class LocalCheckScopeTest(unittest.TestCase):
    def setUp(self):
        i18n.use("en")
        self.addCleanup(i18n.use, "pl")

    def test_a_redirected_server_is_not_told_to_run_ollama_pull(self):
        """llama.cpp reports a file path as its model id and LM Studio its own
        alias, so the name comparison would produce a permanent false "not
        pulled" balloon - and --quality would skip the provider entirely - for
        exactly the setup enhancement.base_url exists to support."""
        fake = _LocalCall({"data": [{"id": "/models/qwen.gguf"}]})
        with _local(fake):
            problem = providers.build(
                "ollama", base_url="http://localhost:8080/v1", model="qwen3.5:9b"
            ).check()
        self.assertIsNone(problem)

    def test_an_unreachable_redirected_server_is_still_reported(self):
        with mock.patch.object(
            providers._LOCAL_OPENER, "open", side_effect=urllib.error.URLError("down")
        ):
            problem = providers.build(
                "ollama", base_url="http://localhost:8080/v1", model="qwen3.5:9b"
            ).check()
        self.assertIn("no local server", problem)

    def test_a_transport_failure_urllib_does_not_wrap_becomes_a_provider_error(self):
        """http.client exceptions are not URLError, so they used to escape both
        complete() and check() - and check() runs inside a pystray callback and in
        --check, neither of which survives an unexpected exception. Reproduced by
        pointing base_url at a non-HTTP TCP service, which answers with a line
        urllib cannot parse."""
        import http.client

        with mock.patch.object(
            providers._LOCAL_OPENER, "open",
            side_effect=http.client.BadStatusLine("SSH-2.0-OpenSSH_9.6"),
        ):
            self.assertIsNotNone(providers.build("ollama", model="qwen3.5:9b").check())


class LocalJurisdictionTest(unittest.TestCase):
    """`hosting()` is what a user reads to decide whether a transcript may go
    somewhere, so it must stop promising locality once the address is not local."""

    def setUp(self):
        i18n.use("en")
        self.addCleanup(i18n.use, "pl")

    def test_the_default_address_still_promises_the_machine(self):
        self.assertIn("nothing leaves", registry.hosting("ollama"))
        self.assertIn("nothing leaves", registry.hosting("ollama", "http://localhost:11434/v1"))
        self.assertIn("nothing leaves", registry.hosting("ollama", "http://127.0.0.1:8080/v1"))

    def test_a_remote_address_says_the_transcript_leaves(self):
        for url in ("http://10.0.0.5:8080/v1", "https://api.openai.com/v1"):
            hosting = registry.hosting("ollama", url)
            self.assertIn("does leave", hosting, url)
        self.assertIn("10.0.0.5", registry.hosting("ollama", "http://10.0.0.5:8080/v1"))

    def test_a_remote_address_says_so_in_polish_too(self):
        i18n.use("pl")
        self.assertIn("opuszcza ten komputer", registry.hosting("ollama", "http://10.0.0.5/v1"))

    def test_an_override_does_not_change_what_a_keyed_provider_reports(self):
        self.assertIn("China", registry.hosting("deepseek", "http://10.0.0.5/v1"))


class ProviderSwitchTest(unittest.TestCase):
    """Switching provider must carry the model with it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        self.controller = DictationController(
            config=self.config,
            recorder=mock.Mock(is_recording=False, level=0.0),
            transcriber=mock.Mock(model_name="large-v3-turbo"),
            history=mock.Mock(),
            sounds=mock.Mock(),
        )

    def test_switching_to_deepseek_swaps_the_model(self):
        self.assertEqual(self.config.get("enhancement.model"), "claude-haiku-4-5")
        self.controller.set_enhancement_provider("deepseek")
        self.assertEqual(self.config.get("enhancement.model"), "deepseek-v4-flash")

    def test_switching_back_restores_an_anthropic_model(self):
        self.controller.set_enhancement_provider("deepseek")
        self.controller.set_enhancement_provider("anthropic")
        self.assertEqual(self.config.get("enhancement.model"), "claude-haiku-4-5")

    def test_a_valid_model_for_the_new_provider_is_kept(self):
        """claude_cli and anthropic share model names — don't reset needlessly."""
        self.config.set("enhancement.model", "claude-sonnet-5")
        self.controller.set_enhancement_provider("claude_cli")
        self.assertEqual(self.config.get("enhancement.model"), "claude-sonnet-5")

    def test_switching_to_the_local_provider_swaps_the_model(self):
        # Patched: set_enhancement_provider calls check(), and a unit test must not
        # depend on whether a local server happens to be listening on this box.
        with mock.patch.object(
            providers._LOCAL_OPENER, "open", side_effect=urllib.error.URLError("down")
        ):
            self.controller.set_enhancement_provider("ollama")
        self.assertEqual(self.config.get("enhancement.model"), "qwen3.5:9b")

    def test_switch_survives_a_reload(self):
        self.controller.set_enhancement_provider("deepseek")
        reloaded = Config.load(self.config.path)
        self.assertEqual(reloaded.get("enhancement.provider"), "deepseek")
        self.assertEqual(reloaded.get("enhancement.model"), "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
