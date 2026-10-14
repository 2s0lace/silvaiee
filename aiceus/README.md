# aiceus

A local, read-only web view on top of `pk`, for a human Art Director. It answers only
from `pk ... --json` commands, listens on `127.0.0.1` only, and writes nothing but
`.pk/aiceus/tickets.md` (renders go through `pk map render` into `.pk/render/`).

## Start

From the game repo root:

```
python aiceus/server.py                 # http://127.0.0.1:8765
python aiceus/server.py --port 9000     # another port
```

Then open http://127.0.0.1:8765 in a browser. `aiceus/index.html` is the frontend; if it
is not installed yet, `/` serves a one-line placeholder.

## API

`GET /api/maps`, `GET /api/map/NAME`, `GET /api/render/NAME.png?scale=1..4`,
`GET /api/tickets`, `POST /api/ticket`. Errors are `{"ok": false, "error": "CODE message"}`
with HTTP 400/404/500.

Tickets are appended, one line each, as `MAP x,y-WxH: note` (or `MAP @x,y: note` for a
single cell). The first `/api/maps` call takes a few seconds (it runs `pk map ls --all`).
