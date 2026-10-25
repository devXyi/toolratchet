"""Resolve a source spec (file, URL, ``cmd:`` or argv list) into a Surface."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Optional

from .capture import PROTOCOL_VERSION, capture_http, capture_stdio
from .ingest import parse_surface
from .model import Surface


class SourceError(Exception):
    pass


def load_surface(
    spec: Any,
    *,
    headers: Optional[dict] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    timeout: float = 30.0,
    protocol_version: str = PROTOCOL_VERSION,
) -> Surface:
    if isinstance(spec, (list, tuple)):
        return capture_stdio(list(spec), env, cwd, timeout, protocol_version)
    if spec.startswith(("http://", "https://")):
        return capture_http(spec, headers, timeout, protocol_version)
    if spec.startswith("cmd:"):
        try:
            argv = shlex.split(spec[4:])
        except ValueError as exc:
            raise SourceError(f"cannot parse command in {spec!r}: {exc}") from exc
        return capture_stdio(argv, env, cwd, timeout, protocol_version)
    path = Path(spec)
    if not path.is_file():
        raise SourceError(f"{spec!r} is not a file, an http(s) URL, or a 'cmd:...' spec")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError(f"cannot read {spec}: {exc}") from exc
    return parse_surface(data, source=str(path))


def dumps_lock(surface: Surface) -> str:
    doc: dict = {"toolratchet": 1}
    if surface.server:
        doc["server"] = surface.server
    doc["tools"] = [t.to_dict() for t in sorted(surface.tools, key=lambda t: t.name)]
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
