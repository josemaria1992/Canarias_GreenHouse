"""Pages and static files of the dashboard and the twin, so a browser always runs the code it is served (0.6.11).

* The page (index.html, twin.html) is never cached, carries the software version in a meta tag, and names every
  script and stylesheet with `?v=<version>`: a new version is a new URL, so an upgrade can never leave an old
  script in the browser's cache.
* A versioned static file may be cached for long; an unversioned one (an image named in a script) is revalidated.
* Every response carries `X-AquaCrop-Version`; a page that sees a version other than its own reloads itself, so a
  tab left open across an upgrade picks up the new code without a hard reload.
"""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from fastapi import HTTPException, Request, Response

from .. import __version__

WEB_DIR = resources.files("aquacrop") / "web"
VERSION_HEADER = "X-AquaCrop-Version"
TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}
_REF = re.compile(r'((?:src|href)="(?:/)?static/[^"?]+\.(?:js|css|svg))"')


def page(name: str) -> Response:
    html = (WEB_DIR / name).read_text(encoding="utf-8")
    html = _REF.sub(lambda m: f'{m.group(1)}?v={__version__}"', html)
    html = html.replace("<head>", f'<head>\n<meta name="aquacrop-version" content="{__version__}">', 1)
    return Response(html.encode("utf-8"), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


def static(name: str, request: Request, allowed: dict[str, str] | None = None) -> Response:
    allowed = allowed or TYPES
    ext = Path(name).suffix
    if "/" in name or ".." in name or ext not in allowed:
        raise HTTPException(404)
    f = WEB_DIR / name
    if not f.is_file():
        raise HTTPException(404)
    versioned = request.query_params.get("v") == __version__
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    return Response(f.read_bytes(), media_type=allowed[ext], headers={"Cache-Control": cache})
