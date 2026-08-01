"""Tray menu wiring.

The subtle failure this guards against: pystray decides how to invoke a menu
action by reading `action.__code__.co_argcount`. Zero means "call with no
arguments"; one means "pass the Icon". A default argument counts towards that
number, so the usual late-binding idiom

    lambda name=device.name: do_something(name)

is silently called as `action(icon)` and `name` becomes the Icon object. Every
action here must therefore be a real closure or a bound method.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whisperdictate import audio, i18n
from whisperdictate.audio import DeviceInfo
from whisperdictate.config import Config
from whisperdictate.controller import State
from whisperdictate.enhance import EnhancementService

FAKE_DEVICES = [
    DeviceInfo(25, "Mikrofon (Anker PowerConf C200)", 2, 48000.0, "Windows WASAPI", False),
    DeviceInfo(26, "Zestaw mikrofonow (Intel Smart Sound)", 2, 48000.0, "Windows WASAPI", False),
]

SENTINEL_ICON = object()  # stands in for the pystray Icon pystray would pass


class FakeController:
    """Records what the menu asked for, without touching audio or models."""

    def __init__(self, config):
        self.config = config
        self.state = State.IDLE
        self.paused = False
        self.calls = []
        self._pending = []
        self.transcriber = mock.Mock(model_name="large-v3-turbo", description="large-v3-turbo @ cpu/int8")
        self.enhancement = EnhancementService(config)

    def set_enhancement_enabled(self, enabled):
        self.calls.append(("enhancement_enabled", enabled))
        self.config.set("enhancement.enabled", enabled)

    def set_enhancement_provider(self, provider):
        self.calls.append(("enhancement_provider", provider))
        self.config.set("enhancement.provider", provider)

    def set_enhancement_model(self, model):
        self.calls.append(("enhancement_model", model))
        self.config.set("enhancement.model", model)

    def set_enhancement_prompt(self, prompt):
        self.calls.append(("enhancement_prompt", prompt))
        self.config.set("enhancement.prompt", prompt)

    def set_audio_device(self, spec):
        self.calls.append(("device", spec))
        self.config.set("audio.device", spec)

    def set_language(self, code):
        self.calls.append(("language", code))
        self.config.set("transcription.language", code)

    def set_ui_language(self, code):
        self.calls.append(("ui_language", code))
        self.config.set("ui.language", i18n.use(code))

    def set_model(self, name):
        self.calls.append(("model", name))

    def set_hotkey_key(self, key):
        self.calls.append(("hotkey_key", key))
        self.config.set("hotkey.key", key)

    def set_vocabulary(self, raw):
        self.calls.append(("vocabulary", raw))
        self.config.set("transcription.vocabulary", raw)

    def set_suggest_vocabulary(self, enabled):
        self.calls.append(("suggest_vocabulary", enabled))
        self.config.set("transcription.suggest_vocabulary", enabled)

    @property
    def pending_vocabulary(self):
        return self._pending

    def set_paused(self, paused):
        self.calls.append(("paused", paused))
        self.paused = paused

    def copy_last_transcription(self):
        self.calls.append(("copy_last", None))


class TrayMenuTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        # Pinned: a fresh config takes its interface language from Windows, so on
        # an English machine every Polish label asserted below would be English.
        # Restored afterwards, because the language is module state and one test
        # here deliberately switches it.
        self.addCleanup(i18n.use, i18n.language())
        self.config.set("ui.language", "pl")
        i18n.use("pl")

        patcher = mock.patch.object(audio, "list_input_devices", lambda **_: FAKE_DEVICES)
        patcher.start()
        self.addCleanup(patcher.stop)
        # tray.py imported the symbol directly, so patch it there too.
        from whisperdictate.ui import tray as tray_module

        patcher2 = mock.patch.object(tray_module, "list_input_devices", lambda **_: FAKE_DEVICES)
        patcher2.start()
        self.addCleanup(patcher2.stop)

        self.controller = FakeController(self.config)
        self.tray = tray_module.Tray(
            controller=self.controller, config=self.config, on_quit=lambda: None
        )

    def submenu(self, title):
        for item in self.tray._icon.menu:
            if str(item.text) == title:
                return list(item.submenu)
        raise AssertionError(f"brak podmenu {title!r}")

    def nested_top(self, prefix):
        """A top-level submenu whose label carries its current selection."""
        for item in self.tray._icon.menu:
            if str(item.text).startswith(prefix):
                return list(item.submenu)
        raise AssertionError(f"brak pozycji {prefix!r} w menu glownym")

    def nested(self, title, prefix):
        """A submenu inside 'Czyszczenie tekstu', found by its label prefix.

        Those labels carry the current selection ("Model: deepseek-v4-flash"),
        so they are matched on prefix rather than equality.

        Note: iterating a pystray Menu yields only the items whose `visible`
        currently evaluates true, so the model list returned here is already
        filtered down to the selected provider's models.
        """
        for item in self.submenu(title):
            if str(item.text).startswith(prefix):
                return list(item.submenu)
        raise AssertionError(f"brak pozycji {prefix!r} w {title!r}")

    # -- the arity trap -------------------------------------------------

    def test_device_action_receives_the_name_not_the_icon(self):
        anker = next(i for i in self.submenu("Mikrofon") if "Anker" in str(i.text))
        anker(SENTINEL_ICON)  # exactly how pystray invokes it
        self.assertEqual(self.controller.calls, [("device", "Mikrofon (Anker PowerConf C200)")])

    def test_default_device_action_passes_none(self):
        default = next(i for i in self.submenu("Mikrofon") if str(i.text) == "Domyślne systemowe")
        default(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("device", None)])

    def test_language_action_receives_the_code(self):
        english = next(
            i for i in self.submenu("Język dyktowania") if str(i.text) == "Angielski"
        )
        english(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("language", "en")])

    def test_model_action_receives_the_name(self):
        medium = next(i for i in self.nested_top("Model:") if str(i.text) == "medium")
        medium(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("model", "medium")])

    def test_enhancement_provider_action_receives_the_key(self):
        cli = next(i for i in self.nested("Czyszczenie tekstu", "Provider:") if "CLI" in str(i.text))
        cli(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("enhancement_provider", "claude_cli")])

    def test_enhancement_model_action_receives_the_name(self):
        sonnet = next(
            i for i in self.nested("Czyszczenie tekstu", "Model:")
            if str(i.text) == "claude-sonnet-5"
        )
        sonnet(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("enhancement_model", "claude-sonnet-5")])

    def test_enhancement_style_action_receives_the_key(self):
        chat = next(i for i in self.nested("Czyszczenie tekstu", "Styl:") if "Czat" in str(i.text))
        chat(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("enhancement_prompt", "chat")])

    def test_enhancement_toggle_flips_the_flag(self):
        toggle = next(
            i for i in self.submenu("Czyszczenie tekstu") if str(i.text) == "Włącz czyszczenie"
        )
        self.assertFalse(toggle.checked)
        toggle(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("enhancement_enabled", True)])

    def test_api_key_items_hidden_without_a_dispatcher(self):
        """Without a Tk thread to open a dialog on, the items would be dead."""
        labels = [str(i.text) for i in self.submenu("Czyszczenie tekstu")]
        self.assertNotIn("Ustaw klucz API...", labels)

    def test_every_action_in_the_tree_survives_being_invoked_by_pystray(self):
        """Any action with the wrong arity raises TypeError when pystray calls it.

        Recursive on purpose: the clean-up settings are now nested two levels
        deep, and a two-level walk would silently stop checking exactly the
        items that were just moved.
        """
        skip = {
            "Zakończ", "Otwórz konfigurację", "Otwórz historię", "Otwórz log", "Odśwież listę",
        }
        visited = []

        def walk(items):
            for target in items:
                if target.submenu:
                    walk(list(target.submenu))
                    continue
                if str(target.text) in skip or not target.enabled:
                    continue
                visited.append(str(target.text))
                target(SENTINEL_ICON)  # must not raise

        walk(list(self.tray._icon.menu))
        # Guard against the walk silently skipping the newly nested level.
        # Only the *selected* provider's models are iterable (see the note on
        # nested()), so this checks the default provider's model, not DeepSeek's.
        self.assertIn("claude-haiku-4-5", visited)
        self.assertIn("Czat / Slack", visited)

    # -- radio state ----------------------------------------------------

    def test_selected_device_is_checked(self):
        self.config.set("audio.device", "Mikrofon (Anker PowerConf C200)")
        anker = next(i for i in self.submenu("Mikrofon") if "Anker" in str(i.text))
        default = next(i for i in self.submenu("Mikrofon") if str(i.text) == "Domyślne systemowe")
        self.assertTrue(anker.checked)
        self.assertFalse(default.checked)

    def test_default_is_checked_when_no_device_configured(self):
        default = next(i for i in self.submenu("Mikrofon") if str(i.text) == "Domyślne systemowe")
        self.assertTrue(default.checked)

    # -- unplugged device -----------------------------------------------

    def test_configured_but_absent_device_still_appears_and_stays_checked(self):
        self.config.set("audio.device", "Blue Yeti")
        from whisperdictate.ui import tray as tray_module

        # Kept on self, not in a local. pystray names its Win32 window class
        # "<name><id(icon)>SystemTrayIcon" and only unregisters it when an icon
        # that was actually run stops - never here. Let this one be collected and
        # a later Icon can land on the same address, hit the same class name and
        # fail construction with ERROR_CLASS_ALREADY_EXISTS, in whichever test
        # happens to run then. Holding the reference keeps the address taken.
        self._extra_tray = tray = tray_module.Tray(
            controller=self.controller, config=self.config, on_quit=lambda: None
        )
        labels = [str(i.text) for i in tray._icon.menu if i.text == "Mikrofon"]
        self.assertEqual(labels, ["Mikrofon"])

        mic_items = next(list(i.submenu) for i in tray._icon.menu if i.text == "Mikrofon")
        ghost = next(i for i in mic_items if "Blue Yeti" in str(i.text))
        self.assertIn("niepodłączony", str(ghost.text))
        self.assertTrue(ghost.checked)

    # -- hotkey picker ---------------------------------------------------

    def test_hotkey_choice_is_wired_through(self):
        alt = next(i for i in self.nested_top("Hotkey:") if "Prawy Alt" in str(i.text))
        alt(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("hotkey_key", "alt_r")])

    def test_right_ctrl_is_checked_by_default(self):
        items = {str(i.text): i for i in self.nested_top("Hotkey:")}
        checked = [text for text, i in items.items() if i.checked]
        self.assertEqual(checked, ["Prawy Ctrl (zalecany)"])

    def test_hotkey_label_shows_the_current_key(self):
        self.config.set("hotkey.key", "alt_r")
        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertIn("Hotkey: Prawy Alt / AltGr", labels)

    def test_altgr_option_warns_about_diacritics(self):
        """The trade-off belongs next to the option, not only in the README."""
        alt = next(i for i in self.nested_top("Hotkey:") if "Prawy Alt" in str(i.text))
        self.assertIn("ą", str(alt.text))

    # -- clean-up model picker ------------------------------------------

    def test_deepseek_models_are_offered_when_deepseek_is_selected(self):
        self.config.set("enhancement.provider", "deepseek")
        labels = [str(i.text) for i in self.nested("Czyszczenie tekstu", "Model:")]
        self.assertEqual(labels, ["deepseek-v4-flash", "deepseek-v4-pro"])

    def test_choosing_the_deepseek_model_is_wired_through(self):
        self.config.set("enhancement.provider", "deepseek")
        pro = next(
            i for i in self.nested("Czyszczenie tekstu", "Model:")
            if str(i.text) == "deepseek-v4-pro"
        )
        pro(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("enhancement_model", "deepseek-v4-pro")])

    def test_other_providers_models_stay_hidden(self):
        """Visibility is re-evaluated per display, so a switch needs no rebuild."""
        self.config.set("enhancement.provider", "claude_cli")
        visible = [str(i.text) for i in self.nested("Czyszczenie tekstu", "Model:")]
        self.assertNotIn("deepseek-v4-flash", visible)
        self.assertIn("claude-sonnet-5", visible)

    def test_selected_model_is_checked(self):
        self.config.set("enhancement.provider", "deepseek")
        self.config.set("enhancement.model", "deepseek-v4-flash")
        items = {str(i.text): i for i in self.nested("Czyszczenie tekstu", "Model:")}
        self.assertTrue(items["deepseek-v4-flash"].checked)
        self.assertFalse(items["deepseek-v4-pro"].checked)

    def test_group_labels_show_the_current_choice_without_opening_them(self):
        self.config.set("enhancement.provider", "deepseek")
        self.config.set("enhancement.model", "deepseek-v4-flash")
        labels = [str(i.text) for i in self.submenu("Czyszczenie tekstu")]
        self.assertIn("Model: deepseek-v4-flash", labels)
        self.assertIn("Provider: DeepSeek API", labels)

    def test_every_provider_has_at_least_one_model_offered(self):
        """A provider whose models were all hidden would show an empty submenu."""
        from whisperdictate.config import ENHANCEMENT_PROVIDERS

        for key in ENHANCEMENT_PROVIDERS:
            self.config.set("enhancement.provider", key)
            self.assertTrue(self.nested("Czyszczenie tekstu", "Model:"), key)

    # -- the rescue ------------------------------------------------------

    def test_copy_last_transcription_is_offered(self):
        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertIn("Skopiuj ostatnią transkrypcję", labels)

    def test_copy_last_transcription_is_wired_through(self):
        item = next(
            i for i in self.tray._icon.menu
            if str(i.text) == "Skopiuj ostatnią transkrypcję"
        )
        item(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("copy_last", None)])

    def test_copy_last_transcription_hides_when_history_is_off(self):
        """With nothing being recorded it has nothing to hand back, and a menu
        entry that cannot work is worse than no entry."""
        self.config.set("history.enabled", False)
        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertNotIn("Skopiuj ostatnią transkrypcję", labels)

    # -- master switch ---------------------------------------------------

    def test_master_switch_leads_the_menu_and_is_on_by_default(self):
        first = list(self.tray._icon.menu)[0]
        self.assertEqual(str(first.text), "Włącz dyktowanie")
        self.assertTrue(first.checked)

    def test_master_switch_stops_dictation_without_stopping_the_app(self):
        """The point of it: a click instead of a restart when you need quiet."""
        switch = list(self.tray._icon.menu)[0]
        switch(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("paused", True)])
        self.assertFalse(list(self.tray._icon.menu)[0].checked)

    def test_master_switch_turns_dictation_back_on(self):
        self.controller.paused = True
        switch = list(self.tray._icon.menu)[0]
        self.assertFalse(switch.checked)
        switch(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("paused", False)])

    def test_the_menu_carries_no_status_line(self):
        """It was one disabled entry holding state + model + provider, and Windows
        sizes a popup to its longest entry - so it made the whole menu as wide as
        that sentence. The state lives in the icon colour and the tooltip now."""
        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertNotIn("Gotowy", labels)
        self.assertFalse(
            [i for i in self.tray._icon.menu if not i.enabled and str(i.text).strip()],
            "a disabled entry is back in the top-level menu",
        )

    def test_no_top_level_entry_is_absurdly_wide(self):
        """A ceiling, not a measurement: the widest legitimate entry is a
        microphone name, and those are already capped at MAX_DEVICE_NAME."""
        from whisperdictate.ui.tray import MAX_DEVICE_NAME

        self.config.set("enhancement.enabled", True)
        for label in (str(i.text) for i in self.tray._icon.menu):
            self.assertLessEqual(len(label), MAX_DEVICE_NAME, label)

    def test_the_model_submenu_names_the_loaded_model(self):
        """The one thing the status line said that no other label did. Taken from
        the transcriber, not the config, so a reload in progress does not claim a
        model that is not there yet - same source as the radio dot inside."""
        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertIn("Model: large-v3-turbo", labels)

    # -- interface language ---------------------------------------------

    def test_both_language_pickers_are_present_and_tell_each_other_apart(self):
        """One menu, two languages: what Whisper hears and what the menu says.
        A single entry called "Język" is how the two got confused in the first
        place, so they are named in full and sit side by side."""
        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertIn("Język dyktowania", labels)
        self.assertIn("Język aplikacji", labels)
        self.assertEqual(
            labels.index("Język aplikacji"), labels.index("Język dyktowania") + 1
        )

    def test_app_language_offers_endonyms(self):
        """Someone who switched to a language he cannot read has to find his way
        back, so each language is named in itself rather than translated."""
        labels = [str(i.text) for i in self.submenu("Język aplikacji")]
        self.assertEqual(labels, ["Polski", "English"])

    def test_active_interface_language_is_checked(self):
        items = {str(i.text): i for i in self.submenu("Język aplikacji")}
        self.assertTrue(items["Polski"].checked)
        self.assertFalse(items["English"].checked)

    def test_choosing_a_language_is_wired_through_and_persisted(self):
        english = next(i for i in self.submenu("Język aplikacji") if str(i.text) == "English")
        english(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("ui_language", "en")])
        self.assertEqual(self.config.get("ui.language"), "en")

    def test_switching_the_language_relabels_the_whole_menu(self):
        """The point of the feature. pystray menus are immutable once built, so
        this only works because the switch rebuilds rather than repaints."""
        english = next(i for i in self.submenu("Język aplikacji") if str(i.text) == "English")
        english(SENTINEL_ICON)

        labels = [str(i.text) for i in self.tray._icon.menu]
        self.assertIn("Dictation language", labels)
        self.assertIn("Quit", labels)
        self.assertNotIn("Język dyktowania", labels)
        self.assertEqual(
            [str(i.text) for i in self.submenu("Microphone")][0], "System default"
        )

    def test_english_menu_keeps_no_polish_leftovers(self):
        """A label missed by the translation shows up as a Polish word in an
        otherwise English menu - the failure mode this switch invites."""
        i18n.use("en")
        self.tray._rebuild_menu()

        def walk(items):
            for item in items:
                yield str(item.text)
                if item.submenu:
                    yield from walk(list(item.submenu))

        # The one deliberate exception: the Polish entry in the language picker
        # names itself, and the AltGr warning names the letters it clashes with.
        every_label = " ".join(
            text for text in walk(list(self.tray._icon.menu))
            if text != "Polski" and "AltGr" not in text
        )
        self.assertNotRegex(every_label, "[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")

    # -- tooltip --------------------------------------------------------

    def test_tooltip_fits_the_windows_limit(self):
        self.config.set("audio.device", "X" * 300)
        self.assertLessEqual(len(self.tray._tooltip()), 127)

    def test_tooltip_speaks_the_interface_language(self):
        self.assertIn("Gotowy", self.tray._tooltip())
        self.assertIn("Mikrofon:", self.tray._tooltip())
        i18n.use("en")
        self.assertIn("Ready", self.tray._tooltip())
        self.assertIn("Microphone:", self.tray._tooltip())


class SuggestionMenuTest(unittest.TestCase):
    """The suggestion entries only exist when there is a Tk thread to open a
    dialog on, so this builds the tray with a dispatcher."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        # Pinned: a fresh config takes its interface language from Windows, so on
        # an English machine every Polish label asserted below would be English.
        # Restored afterwards, because the language is module state and one test
        # here deliberately switches it.
        self.addCleanup(i18n.use, i18n.language())
        self.config.set("ui.language", "pl")
        i18n.use("pl")

        from whisperdictate.ui import tray as tray_module

        patcher = mock.patch.object(tray_module, "list_input_devices", lambda **_: FAKE_DEVICES)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.controller = FakeController(self.config)
        self.tray = tray_module.Tray(
            controller=self.controller,
            config=self.config,
            on_quit=lambda: None,
            dispatcher=mock.Mock(),
        )

    def cleanup_items(self):
        for item in self.tray._icon.menu:
            if str(item.text) == "Czyszczenie tekstu":
                return list(item.submenu)
        raise AssertionError("brak podmenu")

    def test_counter_is_hidden_when_there_is_nothing_to_review(self):
        labels = [str(i.text) for i in self.cleanup_items()]
        self.assertFalse([t for t in labels if t.startswith("Propozycje")], labels)

    def test_counter_appears_and_counts(self):
        from whisperdictate.vocabulary import Suggestion

        self.controller._pending = [
            Suggestion("dipsyka", "DeepSeeka"),
            Suggestion("antropica", "Anthropica"),
        ]
        labels = [str(i.text) for i in self.cleanup_items()]
        self.assertIn("Propozycje słownika (2)...", labels)

    def test_suggestions_can_be_switched_off(self):
        toggle = next(
            i for i in self.cleanup_items() if str(i.text) == "Proponuj nazwy własne"
        )
        self.assertTrue(toggle.checked)  # on by default
        toggle(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("suggest_vocabulary", False)])

    def test_vocabulary_entry_is_present(self):
        labels = [str(i.text) for i in self.cleanup_items()]
        self.assertIn("Nazwy własne...", labels)


if __name__ == "__main__":
    unittest.main()
