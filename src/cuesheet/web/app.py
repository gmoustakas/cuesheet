"""FastAPI app for `cuesheet web`.

Local-first browser for cassette files: list, inspect, watch live.

Routes:
  GET  /                       index (cassette table)
  GET  /cassette?path=...      detail view for one cassette
  GET  /partials/index_table   HTMX partial: table fragment (used by live refresh)
  GET  /partials/cassette      HTMX partial: cassette detail fragment
  GET  /events                 SSE: file-change events
  GET  /api/cassettes          JSON list
  GET  /api/cassettes/_detail  JSON for one cassette
  GET  /api/stats              aggregate stats
  GET  /healthz                health check + watcher state
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from cuesheet._version import __version__
from cuesheet.cassette import CassetteFile, Interaction, load_cassette
from cuesheet.web.watcher import LiveWatcher

logger = logging.getLogger("cuesheet.web")

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def _factory() -> FastAPI:
    """Uvicorn factory entry-point.

    Reads CUESHEET_WEB_ROOT (set by `cuesheet web`) and falls back to cwd. The CLI
    needs this indirection because uvicorn's `factory=True` mode calls the
    factory with no arguments.
    """
    raw = os.environ.get("CUESHEET_WEB_ROOT")
    root = Path(raw) if raw else Path.cwd()
    return build_app(root)


def build_app(root: Path | None = None) -> FastAPI:
    root_path = (root or Path.cwd()).resolve()
    watcher = LiveWatcher(root_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await watcher.start()
        try:
            yield
        finally:
            await watcher.stop()

    app = FastAPI(
        title="cuesheet",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["short_path"] = lambda v: str(v)
    templates.env.filters["bytes_human"] = _filter_bytes
    templates.env.filters["json_pretty"] = lambda v: json.dumps(
        v, indent=2, ensure_ascii=False, default=str
    )
    templates.env.filters["since"] = _filter_since
    templates.env.filters["short_url"] = lambda v: (v or "").split("?", 1)[0]
    templates.env.filters["truncate_chars"] = lambda v, n=80: (
        (str(v)[:n] + "...") if v and len(str(v)) > n else (str(v) if v else "")
    )

    # ── pages ─────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, search: str | None = None) -> Any:
        cassettes = _scan_cassettes(root_path, search)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "cassettes": cassettes,
                "search": search or "",
                "total": len(cassettes),
                "root": str(root_path),
                "version": __version__,
                "live_enabled": True,
            },
        )

    @app.get("/cassette", response_class=HTMLResponse)
    async def cassette_detail(request: Request, path: str) -> Any:
        cas_path = _resolve_in_root(root_path, path)
        if cas_path is None or not cas_path.exists():
            raise HTTPException(status_code=404, detail="cassette not found")
        cassette = load_cassette(cas_path)
        rel = str(cas_path.relative_to(root_path)) if cas_path.is_relative_to(root_path) else str(cas_path)
        return templates.TemplateResponse(
            request,
            "cassette.html",
            {
                **_cassette_context(cas_path, rel, cassette, root_path),
                "version": __version__,
                "live_enabled": True,
            },
        )

    # ── HTMX partials (used by the live reload mechanism) ─────────────

    @app.get("/partials/index_table", response_class=HTMLResponse)
    async def partial_index_table(request: Request, search: str | None = None) -> Any:
        cassettes = _scan_cassettes(root_path, search)
        return templates.TemplateResponse(
            request,
            "_index_table.html",
            {
                "cassettes": cassettes,
                "total": len(cassettes),
                "search": search or "",
            },
        )

    @app.get("/partials/cassette", response_class=HTMLResponse)
    async def partial_cassette(request: Request, path: str) -> Any:
        cas_path = _resolve_in_root(root_path, path)
        if cas_path is None or not cas_path.exists():
            return HTMLResponse(
                '<div class="banner-warn">cassette removed since you opened it</div>',
                status_code=200,
            )
        cassette = load_cassette(cas_path)
        rel = str(cas_path.relative_to(root_path)) if cas_path.is_relative_to(root_path) else str(cas_path)
        return templates.TemplateResponse(
            request,
            "_cassette_body.html",
            _cassette_context(cas_path, rel, cassette, root_path),
        )

    # ── SSE: live file-change stream ─────────────────────────────────

    @app.get("/events")
    async def events(request: Request) -> EventSourceResponse:
        q = watcher.subscribe()

        async def event_source():
            try:
                yield {"event": "connected", "data": json.dumps({"watching": str(root_path)})}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                    except TimeoutError:
                        # Periodic ping keeps proxies / browsers from dropping the conn
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": event.get("type", "update"),
                        "data": json.dumps(event),
                    }
            finally:
                watcher.unsubscribe(q)

        return EventSourceResponse(event_source())

    # ── JSON API ──────────────────────────────────────────────────────

    @app.get("/api/cassettes")
    async def api_list(search: str | None = None) -> dict[str, Any]:
        cassettes = _scan_cassettes(root_path, search)
        return {
            "root": str(root_path),
            "total": len(cassettes),
            "cassettes": [
                {
                    "path": c["rel"],
                    "interactions": c["interactions"],
                    "providers": c["providers"],
                    "size_bytes": c["size"],
                    "modified_at": c["modified"].isoformat(),
                }
                for c in cassettes
            ],
        }

    @app.get("/api/cassettes/_detail")
    async def api_detail(path: str) -> dict[str, Any]:
        cas_path = _resolve_in_root(root_path, path)
        if cas_path is None or not cas_path.exists():
            raise HTTPException(status_code=404, detail="cassette not found")
        cassette = load_cassette(cas_path)
        return {
            "path": str(cas_path.relative_to(root_path) if cas_path.is_relative_to(root_path) else cas_path),
            "interactions": [_interaction_to_api(i) for i in cassette.interactions],
        }

    @app.get("/api/stats")
    async def api_stats() -> dict[str, Any]:
        cassettes = _scan_cassettes(root_path, None)
        total_interactions = sum(c["interactions"] for c in cassettes)
        total_bytes = sum(c["size"] for c in cassettes)
        by_provider: dict[str, int] = {}
        for c in cassettes:
            for prov in c["providers"]:
                by_provider[prov] = by_provider.get(prov, 0) + 1
        return {
            "cassettes": len(cassettes),
            "interactions": total_interactions,
            "size_bytes": total_bytes,
            "by_provider": by_provider,
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "root": str(root_path),
            "watcher": {
                "running": watcher._task is not None,
                "subscribers": watcher.subscriber_count,
                "events_broadcast": watcher.events_broadcast,
            },
        }

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots() -> str:
        return "User-agent: *\nDisallow: /\n"

    return app


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────


def _cassette_context(cas_path: Path, rel: str, cassette: CassetteFile, root: Path) -> dict[str, Any]:
    providers = sorted({i.request.provider for i in cassette.interactions})
    models = sorted({_extract_model(i) for i in cassette.interactions if _extract_model(i)})
    return {
        "rel_path": rel,
        "abs_path": str(cas_path),
        "cassette": cassette,
        "providers": providers,
        "models": models,
        "size_bytes": cas_path.stat().st_size if cas_path.exists() else 0,
        "modified_at": datetime.fromtimestamp(cas_path.stat().st_mtime)
            if cas_path.exists()
            else datetime.now(),
        "root": str(root),
    }


def _scan_cassettes(root: Path, search: str | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.yaml")):
        if not _looks_like_cassette_path(path):
            continue
        try:
            cas = load_cassette(path)
        except Exception:
            continue
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        providers = sorted({i.request.provider for i in cas.interactions})
        if search:
            haystack = (rel + " " + " ".join(providers)).lower()
            if search.lower() not in haystack:
                continue
        with contextlib.suppress(OSError):
            stat = path.stat()
            results.append({
                "rel": rel,
                "abs": str(path),
                "interactions": len(cas.interactions),
                "providers": providers,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime),
            })
    results.sort(key=lambda c: c["modified"], reverse=True)
    return results


def _looks_like_cassette_path(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if "cassette" in parts or "cassettes" in parts:
        return True
    return path.stem.startswith("test_")


def _resolve_in_root(root: Path, path: str) -> Path | None:
    if path.startswith(("/", "\\")) or ".." in Path(path).parts:
        return None
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _extract_model(interaction: Interaction) -> str:
    body = interaction.request.body
    if isinstance(body, dict):
        return str(body.get("model") or "")
    return ""


def _interaction_to_api(i: Interaction) -> dict[str, Any]:
    return {
        "id": i.id,
        "recorded_at": i.recorded_at.isoformat(),
        "duration_ms": i.duration_ms,
        "request": i.request.model_dump(mode="json"),
        "response": i.response.model_dump(mode="json"),
    }


def _filter_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.2f} MB"


def _filter_since(value: datetime | str) -> str:
    from datetime import timezone
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    now = datetime.now() if dt.tzinfo is None else datetime.now(timezone.utc)
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86_400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86_400}d ago"
