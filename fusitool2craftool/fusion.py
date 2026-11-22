"""Read Fusion 360 tool libraries.

Fusion exports a library either as a plain ``.json`` file or as a ``.tools``
file. A ``.tools`` file is usually a zip archive holding one ``tools.json``,
but some exports are plain JSON with the ``.tools`` extension, so both are
accepted whatever the extension says.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


class FusionLibraryError(ValueError):
    """The file is not a Fusion 360 tool library."""


def load_library(path: str | Path) -> list[dict]:
    """Return the library's entries (tools and holders) as Fusion stores them."""
    path = Path(path)
    try:
        if zipfile.is_zipfile(path):
            text = _read_zip(path)
        else:
            text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise FusionLibraryError(f"cannot read {path}: {e}") from e
    return parse_library(text, source=str(path))


def parse_library(text: str, source: str = "<text>") -> list[dict]:
    try:
        root = json.loads(text)
    except json.JSONDecodeError as e:
        raise FusionLibraryError(f"{source}: not JSON ({e})") from e
    if isinstance(root, dict):
        data = root.get("data")
    elif isinstance(root, list):
        data = root
    else:
        data = None
    if not isinstance(data, list):
        raise FusionLibraryError(f"{source}: no \"data\" list - not a Fusion 360 tool library")
    return [entry for entry in data if isinstance(entry, dict)]


def _read_zip(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".json")]
        if not names:
            raise FusionLibraryError(f"{path}: zip archive holds no .json file")
        name = "tools.json" if "tools.json" in names else names[0]
        return z.read(name).decode("utf-8-sig")
