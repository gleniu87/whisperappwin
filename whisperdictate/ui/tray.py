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
from ..config import LANGUAGES, MODEL_CHOICES, Config
from ..controller import DictationController, State

log = logging.getLogger(__name__)

ICON_SIZE = 64

STATE_COLOUR: dict[State, str] = {
    State.IDLE: "#9aa2b1",
    State.LOADING: "#f0b429",
    State.RECORDING: "#e5484d",
    State.TRANSCRIBING: "#3b82f6",
    State.ERROR: "#e5484d",
    State.PAUSED: "#4b5060",
}

STATE_LABEL: dict[State, str] = {
    State.IDLE: "Gotowy",
    State.LOADING: "Laduje model",
    State.RECORDING: "Nagrywanie",
    State.TRANSCRIBING: "Transkrybuje",
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
    ):
        self.controller = controller
        self.config = config
        self._on_quit = on_quit
        self._state = State.IDLE
        self._detail = ""
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
        parts = [
            f"{APP_NAME} - {STATE_LABEL.get(self._state, self._state.value)}",
            f"{LANGUAGE_LABEL.get(language, language)} | {self.controller.transcriber.model_name}",
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
        return f"{label} - {self.controller.transcriber.description}"

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

    # -- actions --------------------------------------------------------

    def _toggle_pause(self) -> None:
        self.controller.set_paused(not self.controller.paused)

    def _quit(self) -> None:
        log.info("Zamykanie z menu tray")
        self.stop()
        self._on_quit()


def _open(path) -> None:  # noqa: ANN001
    """Open a file with its default handler, creating it if it does not exist yet."""
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        os.startfile(str(path))  # noqa: S606 - intentional shell-open of a known path
    except OSError as exc:
        log.warning("Nie moge otworzyc %s: %s", path, exc)
