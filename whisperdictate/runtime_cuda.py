"""Make pip-installed NVIDIA runtime DLLs discoverable to CTranslate2.

CTranslate2 >= 4.5 links against cuBLAS 12 and cuDNN 9. A system-wide CUDA
Toolkit install satisfies that, but it is a heavy dependency to impose. The
`nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels ship the same DLLs inside
site-packages, where Windows will not find them on its own - the loader only
searches PATH and directories registered via `os.add_dll_directory`.

Must run *before* the first `import ctranslate2` (which faster-whisper does at
import time), otherwise the failed load is cached for the process lifetime.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_NVIDIA_PACKAGES = ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime")

# Windows wheels use bin/, Linux wheels use lib/. Check both so the module is
# harmless if this ever runs somewhere else.
_LIB_SUBDIRS = ("bin", "lib")

_added: list[Path] = []


def enable_cuda_dlls() -> list[Path]:
    """Register NVIDIA wheel DLL directories. Returns the paths added.

    Idempotent and never raises: a missing package just means CPU mode.
    """
    if _added:
        return list(_added)

    for package in _NVIDIA_PACKAGES:
        root = _package_dir(package)
        if root is None:
            continue
        for subdir in _LIB_SUBDIRS:
            candidate = root / subdir
            if not candidate.is_dir():
                continue
            try:
                os.add_dll_directory(str(candidate))
            except OSError as exc:  # pragma: no cover - defensive
                log.debug("Nie moge dodac %s do sciezki DLL: %s", candidate, exc)
                continue
            # PATH too: some loaders bypass the per-process directory list.
            os.environ["PATH"] = f"{candidate}{os.pathsep}{os.environ.get('PATH', '')}"
            _added.append(candidate)
            log.debug("Dodano do sciezki DLL: %s", candidate)

    if _added:
        log.info("Biblioteki CUDA znalezione w %d katalogach", len(_added))
    else:
        log.info("Brak pakietow nvidia-*-cu12 - CUDA zadziala tylko z systemowym CUDA Toolkit")
    return list(_added)


def _package_dir(dotted: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(dotted)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    if spec.origin:
        return Path(spec.origin).parent
    return None
