"""System tray icon: status at a glance, language/model switching, quit.

pystray owns a Win32 message loop, so the icon runs detached on its own thread
while Tk keeps the main thread. Menu callbacks therefore arrive off-thread and
must only touch thread-safe APIs (the controller's are).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from .. import APP_NAME, paths
from ..audio import DeviceInfo, list_input_devices, refresh_devices
from ..config import ENHANCEMENT_PROMPTS, ENHANCEMENT_PROVIDERS, LANGUAGES, MODEL_CHOICES, Config
from ..controller import DictationController, State
from ..enhance import PROVIDERS as PROVIDER_SPECS
from ..enhance.prompts import PROMPT_LABELS
from ..hotkey import DEFAULT_KEY, MENU_TRIGGERS

log = logging.getLogger(__name__)

ICON_SIZE = 64

# pystray menus are immutable once constructed - Menu stores its items as a tuple.
# Only per-item text/checked/enabled callables are re-evaluated on display. So a
# list that changes shape (microphones coming and going) requires rebuilding the
# whole menu and calling update_menu().
DEFAULT_DEVICE_LABEL = "Domyslne systemowe"
MAX_DEVICE_NAME = 44

STATE_COLOUR: dict[State, str] = {
    State.IDLE: "#9aa2b1",
    State.LOADING: "#f0b429",
    State.RECORDING: "#e5484d",
    State.TRANSCRIBING: "#3b82f6",
    State.ENHANCING: "#8b5cf6",
    State.ERROR: "#e5484d",
    State.PAUSED: "#4b5060",
}

STATE_LABEL: dict[State, str] = {
    State.IDLE: "Gotowy",
    State.LOADING: "Laduje model",
    State.RECORDING: "Nagrywanie",
    State.TRANSCRIBING: "Transkrybuje",
    State.ENHANCING: "Czyszcze tekst",
    State.ERROR: "Blad",
    State.PAUSED: "Wstrzymane",
}

LANGUAGE_LABEL = {"pl": "Polski", "en": "English", "auto": "Auto-detekcja"}


def _microphone_icon(colour: str) -> Image.Image:
    """A mic pictogram that stays readable when Windows scales it to 16x16."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Capsule
    draw.rounded_rectangle((24, 10, 40, 40), radius=8, fill=colour)
    # Cradle
    draw.arc((16, 22, 48, 48), start=0, end=180, fill=colour, width=5)
    # Stand
    draw.line((32, 46, 32, 54), fill=colour, width=5)
    draw.line((23, 55, 41, 55), fill=colour, width=5)
    return image


class Tray:
    """Tray icon bound to a controller. Implements the UiSink protocol."""

    def __init__(
        self,
        *,
        controller: DictationController,
        config: Config,
        on_quit: Callable[[], None],
        dispatcher=None,  # noqa: ANN001 - MainThreadDispatcher; optional for tests
    ):
        self.controller = controller
        self.config = config
        self._on_quit = on_quit
        self._dispatcher = dispatcher
        self._state = State.IDLE
        self._detail = ""
        self._devices: list[DeviceInfo] = list_input_devices()
        self._icon = pystray.Icon(
            name=APP_NAME,
            icon=_microphone_icon(STATE_COLOUR[State.IDLE]),
            title=self._tooltip(),
            menu=self._build_menu(),
        )

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        self._icon.run_detached()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001 - stopping an unstarted icon
            log.debug("Tray juz zatrzymany")

    # -- UiSink ---------------------------------------------------------

    def set_state(self, state: State, detail: str = "") -> None:
        self._state = state
        self._detail = detail
        try:
            self._icon.icon = _microphone_icon(STATE_COLOUR.get(state, STATE_COLOUR[State.IDLE]))
            self._icon.title = self._tooltip()
        except Exception:  # noqa: BLE001 - icon not yet realised
            log.debug("Nie moge zaktualizowac ikony tray")

    def notify(self, message: str, *, error: bool = False) -> None:
        try:
            self._icon.notify(message, f"{APP_NAME} - blad" if error else APP_NAME)
        except Exception:  # noqa: BLE001 - balloon tips can be disabled by policy
            log.debug("Powiadomienie tray niedostepne: %s", message)

    # -- menu -----------------------------------------------------------

    def _tooltip(self) -> str:
        language = self.config.get("transcription.language", "pl")
        device = self.config.get("audio.device") or DEFAULT_DEVICE_LABEL
        parts = [
            f"{APP_NAME} - {STATE_LABEL.get(self._state, self._state.value)}",
            f"{LANGUAGE_LABEL.get(language, language)} | {self.controller.transcriber.model_name}",
            f"Mikrofon: {_shorten(str(device), 40)}",
        ]
        if self._detail:
            parts.append(self._detail)
        # Windows truncates tray tooltips at 127 characters.
        return "\n".join(parts)[:127]

    def _build_menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: self._status_line(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Jezyk", pystray.Menu(*self._language_items())),
            item("Model", pystray.Menu(*self._model_items())),
            item("Mikrofon", pystray.Menu(*self._device_items())),
            item(lambda _: f"Hotkey: {self._hotkey_label()}",
                 pystray.Menu(*self._hotkey_items())),
            item("Czyszczenie tekstu", pystray.Menu(*self._enhancement_items())),
            pystray.Menu.SEPARATOR,
            item(
                "Wstrzymaj dyktowanie",
                self._toggle_pause,
                checked=lambda _: self.controller.paused,
            ),
            pystray.Menu.SEPARATOR,
            item("Otworz konfiguracje", lambda: _open(paths.config_path())),
            item("Otworz historie", lambda: _open(paths.history_path())),
            item("Otworz log", lambda: _open(paths.log_path())),
            pystray.Menu.SEPARATOR,
            item("Zakoncz", self._quit),
        )

    def _status_line(self) -> str:
        label = STATE_LABEL.get(self._state, self._state.value)
        cleanup = self.controller.enhancement.describe()
        return f"{label} - {self.controller.transcriber.description} | czyszczenie: {cleanup}"

    def _language_items(self) -> list[pystray.MenuItem]:
        def make(code: str) -> pystray.MenuItem:
            return pystray.MenuItem(
                LANGUAGE_LABEL.get(code, code),
                lambda: self.controller.set_language(code),
                checked=lambda _, c=code: self.config.get("transcription.language") == c,
                radio=True,
            )

        return [make(code) for code in LANGUAGES]

    def _model_items(self) -> list[pystray.MenuItem]:
        def make(name: str) -> pystray.MenuItem:
            return pystray.MenuItem(
                name,
                lambda: self.controller.set_model(name),
                checked=lambda _, n=name: self.controller.transcriber.model_name == n,
                radio=True,
            )

        return [make(name) for name in MODEL_CHOICES]

    def _hotkey_items(self) -> list[pystray.MenuItem]:
        """Push-to-talk key. See MENU_TRIGGERS for why right Ctrl leads."""
        return [
            self._radio("hotkey.key", key, label, self.controller.set_hotkey_key)
            for key, label in MENU_TRIGGERS
        ]

    def _hotkey_label(self) -> str:
        key = str(self.config.get("hotkey.key", DEFAULT_KEY))
        for name, label in MENU_TRIGGERS:
            if name == key:
                return label.split(" (")[0].split(" - ")[0]
        return key  # an F-key set by hand in the config

    def _device_items(self) -> list[pystray.MenuItem]:
        """Microphone picker, built from the cached device list.

        Devices are stored in the config by *name*, not index: PortAudio indices
        shift whenever hardware is added or removed, so an index saved today
        points at a different microphone tomorrow.
        """
        selected = self.config.get("audio.device")

        def make(name: str | None, label: str) -> pystray.MenuItem:
            # A closure, NOT `lambda n=name: ...`. pystray inspects
            # __code__.co_argcount to decide how to call an action: 0 means
            # "call with nothing", 1 means "pass the Icon". A default argument
            # still counts, so the lambda form would receive the Icon as `n`.
            def select() -> None:
                self.controller.set_audio_device(name)

            return pystray.MenuItem(
                label,
                select,
                checked=lambda _, n=name: self.config.get("audio.device") == n,
                radio=True,
            )

        items = [make(None, DEFAULT_DEVICE_LABEL)]
        items += [make(d.name, _shorten(d.name, MAX_DEVICE_NAME)) for d in self._devices]

        # A device saved earlier but absent now (unplugged webcam) would otherwise
        # vanish from the menu with no radio button checked - confusing, because
        # it is still the configured device and still what the app will look for.
        if isinstance(selected, str) and not any(d.name == selected for d in self._devices):
            items.append(make(selected, f"{_shorten(selected, MAX_DEVICE_NAME)} (niepodlaczony)"))

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Odswiez liste", self._refresh_devices))
        return items

    def _enhancement_items(self) -> list[pystray.MenuItem]:
        """Clean-up settings, grouped.

        Provider, model and style used to sit in one flat list of nine entries
        with nothing to say which was which — the model picker was there, but
        unfindable, and a provider switch silently changed what the middle group
        meant. Each group is now its own submenu, labelled with the current
        choice so the state reads without opening anything.

        Closures throughout — see the arity note in _device_items.
        """
        item = pystray.MenuItem
        items = [
            item(
                "Wlacz czyszczenie",
                self._toggle_enhancement,
                checked=lambda _: bool(self.config.get("enhancement.enabled")),
            ),
            pystray.Menu.SEPARATOR,
            item(
                lambda _: f"Provider: {self._current_provider_label()}",
                pystray.Menu(*self._provider_items()),
            ),
            item(
                lambda _: f"Model: {self.config.get('enhancement.model')}",
                pystray.Menu(*self._enhancement_model_items()),
            ),
            item(
                lambda _: f"Styl: {self._current_style_label()}",
                pystray.Menu(*self._style_items()),
            ),
        ]

        if self._dispatcher is not None:
            items.append(pystray.Menu.SEPARATOR)
            items.append(item("Nazwy wlasne...", self._open_vocabulary))
            for provider_key in ENHANCEMENT_PROVIDERS:
                if PROVIDER_SPECS[provider_key].env_var is None:
                    continue  # the CLI needs no key
                items.append(self._api_key_entry(provider_key))
        return items

    def _open_vocabulary(self) -> None:
        from . import dialogs

        self._dispatcher.call(
            lambda: dialogs.edit_vocabulary(self._dispatcher.root, self.controller)
        )

    def _radio(self, dotted: str, value: str, label: str, setter) -> pystray.MenuItem:  # noqa: ANN001
        def select() -> None:
            setter(value)

        return pystray.MenuItem(
            label,
            select,
            checked=lambda _, d=dotted, v=value: self.config.get(d) == v,
            radio=True,
        )

    def _provider_items(self) -> list[pystray.MenuItem]:
        return [
            self._radio("enhancement.provider", key, PROVIDER_SPECS[key].label,
                        self.controller.set_enhancement_provider)
            for key in ENHANCEMENT_PROVIDERS
        ]

    def _enhancement_model_items(self) -> list[pystray.MenuItem]:
        """Every provider's models, each visible only under its own provider.

        pystray menus are immutable once built, but `visible` is re-evaluated on
        each display — so this stays correct after a provider switch without
        rebuilding the menu.
        """
        def model_entry(provider_key: str, model_name: str) -> pystray.MenuItem:
            def select() -> None:
                self.controller.set_enhancement_model(model_name)

            return pystray.MenuItem(
                model_name,
                select,
                checked=lambda _, m=model_name: self.config.get("enhancement.model") == m,
                radio=True,
                visible=lambda _, p=provider_key: self.config.get("enhancement.provider") == p,
            )

        items: list[pystray.MenuItem] = []
        for provider_key in ENHANCEMENT_PROVIDERS:
            items += [
                model_entry(provider_key, name)
                for name in PROVIDER_SPECS[provider_key].models
            ]
        return items

    def _style_items(self) -> list[pystray.MenuItem]:
        return [
            self._radio("enhancement.prompt", key, PROMPT_LABELS[key],
                        self.controller.set_enhancement_prompt)
            for key in ENHANCEMENT_PROMPTS
        ]

    def _current_provider_label(self) -> str:
        key = str(self.config.get("enhancement.provider", ""))
        spec = PROVIDER_SPECS.get(key)
        return spec.label.split(" (")[0] if spec else key

    def _current_style_label(self) -> str:
        key = str(self.config.get("enhancement.prompt", ""))
        return PROMPT_LABELS.get(key, key).split(" (")[0]

    def _api_key_entry(self, provider_key: str) -> pystray.MenuItem:
        def open_dialog() -> None:
            from . import dialogs

            self._dispatcher.call(
                lambda: dialogs.manage_api_key(self._dispatcher.root, provider_key)
            )

        return pystray.MenuItem(f"Klucz API: {provider_key}...", open_dialog)

    # -- actions --------------------------------------------------------

    def _toggle_enhancement(self) -> None:
        self.controller.set_enhancement_enabled(
            not bool(self.config.get("enhancement.enabled"))
        )

    def _refresh_devices(self) -> None:
        """Re-enumerate microphones and rebuild the menu around the new list."""
        if self.controller.state is State.RECORDING:
            self.notify("Nie moge odswiezyc listy w trakcie nagrywania.", error=True)
            return

        refresh_devices()
        self._devices = list_input_devices()
        self._icon.menu = self._build_menu()
        self._icon.update_menu()
        log.info("Znaleziono %d mikrofon(ow)", len(self._devices))
        self.notify(f"Znaleziono {len(self._devices)} mikrofon(ow).")

    def _toggle_pause(self) -> None:
        self.controller.set_paused(not self.controller.paused)

    def _quit(self) -> None:
        log.info("Zamykanie z menu tray")
        self.stop()
        self._on_quit()


def _shorten(text: str, limit: int) -> str:
    """Keep the tail: device names are prefixed with a generic 'Mikrofon (' word."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _open(path) -> None:  # noqa: ANN001
    """Open a file with its default handler, creating it if it does not exist yet."""
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        os.startfile(str(path))  # noqa: S606 - intentional shell-open of a known path
    except OSError as exc:
        log.warning("Nie moge otworzyc %s: %s", path, exc)
