"""Write CamCraft ``.camtools`` tool library files.

The envelope is CamCraft's own (``crates/app/src/tool_library.rs``)::

    {"format": "camcraft-tools", "version": N, "units": "mm",
     "tools": [{"name": ..., "definition": {...}}, ...]}
"""

from __future__ import annotations

import json
from pathlib import Path

from .convert import Conversion

FORMAT = "camcraft-tools"
EXTENSION = ".camtools"


def to_json(conversion: Conversion) -> str:
    if not conversion.tools:
        raise ValueError("no tools to write")
    doc = {
        "format": FORMAT,
        "version": conversion.version,
        "units": "mm",
        "tools": conversion.tools,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write(conversion: Conversion, path: str | Path) -> Path:
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(EXTENSION)
    path.write_text(to_json(conversion), encoding="utf-8")
    return path
