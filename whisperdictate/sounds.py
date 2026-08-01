"""Short non-blocking beeps for start / success / error.

winsound.Beep blocks for its full duration, so every cue runs on a throwaway
daemon thread - a 120 ms beep must not delay the start of recording.
"""

from __future__ import annotations

import logging
import threading
import winsound

log = logging.getLogger(__name__)

# (frequency Hz, duration ms) pairs, played in order.
_CUES = {
    "start": ((880, 70),),
    "stop": ((1180, 55),),
    "success": ((1320, 60),),
    "error": ((320, 140), (240, 160)),
    "cancel": ((520, 50),),
}


class Sounds:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def play(self, cue: str) -> None:
        if not self.enabled:
            return
        tones = _CUES.get(cue)
        if not tones:
            return
        thread = threading.Thread(target=_play_tones, args=(tones,), daemon=True)
        thread.start()


def _play_tones(tones: tuple[tuple[int, int], ...]) -> None:
    try:
        for frequency, duration in tones:
            winsound.Beep(frequency, duration)
    except RuntimeError as exc:  # no audio output device
        log.debug("Nie moge odtworzyc dzwieku: %s", exc)
