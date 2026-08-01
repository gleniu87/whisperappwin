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

from whisperdictate import audio
from whisperdictate.audio import DeviceInfo
from whisperdictate.config import Config
from whisperdictate.controller import State

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
        self.transcriber = mock.Mock(model_name="large-v3-turbo", description="large-v3-turbo @ cpu/int8")

    def set_audio_device(self, spec):
        self.calls.append(("device", spec))
        self.config.set("audio.device", spec)

    def set_language(self, code):
        self.calls.append(("language", code))
        self.config.set("transcription.language", code)

    def set_model(self, name):
        self.calls.append(("model", name))

    def set_paused(self, paused):
        self.calls.append(("paused", paused))
        self.paused = paused


class TrayMenuTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")

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
            if item.text == title:
                return list(item.submenu)
        raise AssertionError(f"brak podmenu {title!r}")

    # -- the arity trap -------------------------------------------------

    def test_device_action_receives_the_name_not_the_icon(self):
        anker = next(i for i in self.submenu("Mikrofon") if "Anker" in str(i.text))
        anker(SENTINEL_ICON)  # exactly how pystray invokes it
        self.assertEqual(self.controller.calls, [("device", "Mikrofon (Anker PowerConf C200)")])

    def test_default_device_action_passes_none(self):
        default = next(i for i in self.submenu("Mikrofon") if str(i.text) == "Domyslne systemowe")
        default(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("device", None)])

    def test_language_action_receives_the_code(self):
        english = next(i for i in self.submenu("Jezyk") if str(i.text) == "English")
        english(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("language", "en")])

    def test_model_action_receives_the_name(self):
        medium = next(i for i in self.submenu("Model") if str(i.text) == "medium")
        medium(SENTINEL_ICON)
        self.assertEqual(self.controller.calls, [("model", "medium")])

    def test_every_action_in_the_tree_survives_being_invoked_by_pystray(self):
        """Any action with the wrong arity raises TypeError when pystray calls it."""
        skip = {"Zakoncz", "Otworz konfiguracje", "Otworz historie", "Otworz log", "Odswiez liste"}
        for item in self.tray._icon.menu:
            targets = list(item.submenu) if item.submenu else [item]
            for target in targets:
                if str(target.text) in skip or not target.enabled:
                    continue
                target(SENTINEL_ICON)  # must not raise

    # -- radio state ----------------------------------------------------

    def test_selected_device_is_checked(self):
        self.config.set("audio.device", "Mikrofon (Anker PowerConf C200)")
        anker = next(i for i in self.submenu("Mikrofon") if "Anker" in str(i.text))
        default = next(i for i in self.submenu("Mikrofon") if str(i.text) == "Domyslne systemowe")
        self.assertTrue(anker.checked)
        self.assertFalse(default.checked)

    def test_default_is_checked_when_no_device_configured(self):
        default = next(i for i in self.submenu("Mikrofon") if str(i.text) == "Domyslne systemowe")
        self.assertTrue(default.checked)

    # -- unplugged device -----------------------------------------------

    def test_configured_but_absent_device_still_appears_and_stays_checked(self):
        self.config.set("audio.device", "Blue Yeti")
        from whisperdictate.ui import tray as tray_module

        tray = tray_module.Tray(controller=self.controller, config=self.config, on_quit=lambda: None)
        labels = [str(i.text) for i in tray._icon.menu if i.text == "Mikrofon"]
        self.assertEqual(labels, ["Mikrofon"])

        mic_items = next(list(i.submenu) for i in tray._icon.menu if i.text == "Mikrofon")
        ghost = next(i for i in mic_items if "Blue Yeti" in str(i.text))
        self.assertIn("niepodlaczony", str(ghost.text))
        self.assertTrue(ghost.checked)

    # -- tooltip --------------------------------------------------------

    def test_tooltip_fits_the_windows_limit(self):
        self.config.set("audio.device", "X" * 300)
        self.assertLessEqual(len(self.tray._tooltip()), 127)


if __name__ == "__main__":
    unittest.main()
