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
from ..config import (
    ENHANCEMENT_MODELS,
    ENHANCEMENT_PROMPTS,
    ENHANCEMENT_PROVIDERS,
    LANGUAGES,
    MODEL_CHOICES,
    Config,
)
from ..controller import DictationController, State
from ..enhance import PROVIDERS as PROVIDER_LABELS
from ..enhance.prompts import PROMPT_LABELS

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
        """Clean-up settings. Closures throughout — see the arity note in _device_items."""
        item = pystray.MenuItem
        items = [
            item(
                "Wlacz czyszczenie",
                self._toggle_enhancement,
                checked=lambda _: bool(self.config.get("enhancement.enabled")),
            ),
            pystray.Menu.SEPARATOR,
        ]

        def radio(dotted: str, value: str, label: str, setter) -> pystray.MenuItem:  # noqa: ANN001
            def select() -> None:
                setter(value)

            return item(
                label,
                select,
                checked=lambda _, d=dotted, v=value: self.config.get(d) == v,
                radio=True,
            )

        items += [
            radio("enhancement.provider", key, PROVIDER_LABELS[key],
                  self.controller.set_enhancement_provider)
            for key in ENHANCEMENT_PROVIDERS
        ]
        items.append(pystray.Menu.SEPARATOR)
        items += [
            radio("enhancement.model", name, name, self.controller.set_enhancement_model)
            for name in ENHANCEMENT_MODELS
        ]
        items.append(pystray.Menu.SEPARATOR)
        items += [
            radio("enhancement.prompt", key, PROMPT_LABELS[key],
                  self.controller.set_enhancement_prompt)
            for key in ENHANCEMENT_PROMPTS
        ]

        if self._dispatcher is not None:
            items.append(pystray.Menu.SEPARATOR)
            items.append(item("Ustaw klucz API...", self._set_api_key))
            items.append(item("Usun klucz API", self._delete_api_key))
        return items

    # -- actions --------------------------------------------------------

    def _toggle_enhancement(self) -> None:
        self.controller.set_enhancement_enabled(
            not bool(self.config.get("enhancement.enabled"))
        )

    def _set_api_key(self) -> None:
        from . import dialogs

        self._dispatcher.call(lambda: dialogs.ask_api_key(self._dispatcher.root))

    def _delete_api_key(self) -> None:
        from . import dialogs

        self._dispatcher.call(lambda: dialogs.confirm_delete_api_key(self._dispatcher.root))

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
