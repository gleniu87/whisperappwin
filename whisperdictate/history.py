"""Append-only transcription log at %APPDATA%\\WhisperDictateWin\\history.jsonl.

JSONL rather than a database: it is greppable, survives partial writes (a torn
last line costs one entry, not the file), and needs no schema migrations.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Trimming rewrites the whole file, so amortise it rather than doing it per entry.
# The file may therefore exceed max_entries by up to this many lines between trims.
_TRIM_CHECK_INTERVAL = 100

# Filesystem failures we swallow. Beyond the obvious OSError, pathlib raises
# ValueError for structurally invalid paths (embedded null bytes, for one).
_FS_ERRORS = (OSError, ValueError)


class History:
    def __init__(self, path: Path, *, enabled: bool = True, max_entries: int = 5000):
        self.path = path
        self.enabled = enabled
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._appends_since_trim = 0
        # Scale the interval down for small limits, so max_entries=10 does not
        # mean "grow to 110 lines, then trim".
        self._trim_interval = min(_TRIM_CHECK_INTERVAL, max(1, max_entries))

    def append(self, **fields: Any) -> None:
        """Record one transcription. Never raises - a failed log must not lose the paste."""
        if not self.enabled:
            return
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
        line = json.dumps(entry, ensure_ascii=False)

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except _FS_ERRORS as exc:
                log.warning("Nie moge dopisac do historii: %s", exc)
                return

            self._appends_since_trim += 1
            if self._appends_since_trim >= self._trim_interval:
                self._appends_since_trim = 0
                self._trim()

    def recent(self, count: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_all()[-count:]

    # -- internals ------------------------------------------------------

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # torn write; skip the line, keep the file
        except _FS_ERRORS as exc:
            log.warning("Nie moge odczytac historii: %s", exc)
        return entries

    def _trim(self) -> None:
        """Caller holds the lock."""
        if self.max_entries <= 0:
            return
        entries = self._read_all()
        if len(entries) <= self.max_entries:
            return
        keep = entries[-self.max_entries :]
        tmp = self.path.with_suffix(".jsonl.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                for entry in keep:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            tmp.replace(self.path)
            log.info("Historia przycieta do %d wpisow", len(keep))
        except _FS_ERRORS as exc:
            log.warning("Nie moge przyciac historii: %s", exc)
