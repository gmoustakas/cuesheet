"""encore CLI.

  encore list                   # find cassettes under cwd
  encore inspect <path>         # render a cassette in the terminal
  encore stats                  # aggregate cost / interaction counts
  encore scrub <path>           # re-apply scrubbers in place
  encore web                    # serve the local dashboard
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from encore._version import __version__
from encore.cassette import (
    load_cassette,
    save_cassette,
)

console = Console(width=max(120, Console().size.width))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="encore")
def main() -> None:
    """encore - replay LLM API calls in tests."""


@main.command("list")
@click.option("--root", default=".", type=click.Path(exists=True, file_okay=False, path_type=Path),
              show_default=True)
def cmd_list(root: Path) -> None:
    """List all cassette YAML files under a root directory."""
    files = list(_find_cassettes(root))
    if not files:
        console.print(f"[dim]No cassettes found under {root}.[/dim]")
        return

    table = Table(title="Cassettes", header_style="bold magenta")
    table.add_column("Path", style="cyan")
    table.add_column("Interactions", justify="right")
    table.add_column("Providers")
    table.add_column("Size", justify="right", style="dim")

    for f in files:
        try:
            cas = load_cassette(f)
            providers = sorted({i.request.provider for i in cas.interactions}) or ["-"]
            size_kb = f.stat().st_size / 1024
            table.add_row(
                str(f.relative_to(root)),
                str(len(cas.interactions)),
                ", ".join(providers),
                f"{size_kb:.1f}KB",
            )
        except Exception as exc:
            table.add_row(str(f.relative_to(root)), "?", f"[red]error: {exc}[/red]", "-")
    console.print(table)


@main.command("inspect")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--limit", "-n", default=20, show_default=True)
def cmd_inspect(path: Path, limit: int) -> None:
    """Pretty-print a cassette file."""
    cas = load_cassette(path)
    if not cas.interactions:
        console.print(f"[dim]{path} has no interactions.[/dim]")
        return
    console.print(f"[bold]{path}[/bold]  [dim]({len(cas.interactions)} interactions)[/dim]\n")

    for idx, interaction in enumerate(cas.interactions[:limit]):
        req = interaction.request
        resp = interaction.response
        provider = req.provider
        model = (req.body or {}).get("model", "-") if isinstance(req.body, dict) else "-"
        console.print(
            f"[yellow]#{idx + 1:02d}[/yellow]  "
            f"[cyan]{req.method}[/cyan] {escape(_short_url(req.url))}  "
            f"[dim]→[/dim] {resp.status_code}  "
            f"[dim]({provider}, {escape(str(model))})[/dim]"
        )
        if isinstance(req.body, dict) and "messages" in req.body:
            first = req.body["messages"][0] if req.body["messages"] else {}
            if isinstance(first, dict):
                preview = str(first.get("content", ""))[:80].replace("\n", " ")
                console.print(f"   [dim]first message:[/dim] {escape(preview)}")


@main.command("stats")
@click.option("--root", default=".", type=click.Path(exists=True, file_okay=False, path_type=Path),
              show_default=True)
def cmd_stats(root: Path) -> None:
    """Aggregate stats across all cassettes under a root."""
    files = list(_find_cassettes(root))
    if not files:
        console.print(f"[dim]No cassettes under {root}.[/dim]")
        return

    total_interactions = 0
    by_provider: dict[str, int] = {}
    streaming_count = 0
    total_bytes = 0

    for f in files:
        try:
            cas = load_cassette(f)
        except Exception:
            continue
        total_interactions += len(cas.interactions)
        total_bytes += f.stat().st_size
        for i in cas.interactions:
            by_provider[i.request.provider] = by_provider.get(i.request.provider, 0) + 1
            if i.response.is_streaming:
                streaming_count += 1

    table = Table(show_header=False, box=None)
    table.add_column(style="dim", width=22)
    table.add_column()
    table.add_row("Cassette files", f"{len(files)}")
    table.add_row("Interactions", f"{total_interactions:,}")
    table.add_row("Streaming responses", f"{streaming_count:,}")
    table.add_row("Disk size", f"{total_bytes / 1024:.1f}KB")
    console.print(table)

    if by_provider:
        console.print("\n[bold]By provider:[/bold]")
        for prov, count in sorted(by_provider.items(), key=lambda kv: kv[1], reverse=True):
            console.print(f"  [cyan]{prov}[/cyan]  [dim]{count}[/dim]")


@main.command("scrub")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def cmd_scrub(path: Path) -> None:
    """Re-apply scrubbers to one cassette or a directory of cassettes."""
    targets = [path] if path.is_file() else list(_find_cassettes(path))
    if not targets:
        console.print(f"[dim]No cassettes found at {path}.[/dim]")
        return
    for f in targets:
        try:
            cas = load_cassette(f)
            save_cassette(f, cas, scrub=True)
            console.print(f"[green]✓[/green] {f}")
        except Exception as exc:
            console.print(f"[red]✗[/red] {f}: {exc}")


@main.command("web")
@click.option("--root", default=".", type=click.Path(exists=True, file_okay=False, path_type=Path),
              show_default=True, help="Directory to scan for cassettes.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8095, show_default=True, type=int)
@click.option("--no-open", is_flag=True, default=False, help="Don't auto-open a browser.")
@click.option("--reload", is_flag=True, default=False, help="Reload on code changes (dev only).")
def cmd_web(root: Path, host: str, port: int, no_open: bool, reload: bool) -> None:
    """Launch the local web dashboard (requires the [web] extra)."""
    try:
        import uvicorn
    except ImportError as e:
        console.print(
            "[red]encore[web] is not installed.[/red]\n"
            "  [dim]pip install 'encore[web]'[/dim]"
        )
        raise click.exceptions.Exit(1) from e

    root = root.resolve()
    url = f"http://{host}:{port}"
    console.print(f"[bold]encore web[/bold]  [dim]{url}[/dim]  [yellow]watching[/yellow] {root}")

    if not no_open:
        import contextlib
        import threading
        import webbrowser

        def open_browser() -> None:
            import time
            time.sleep(0.6)
            with contextlib.suppress(Exception):
                webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()

    # We pass the root via env var so the factory in encore.web.app can read it.
    import os
    os.environ["ENCORE_WEB_ROOT"] = str(root)

    uvicorn.run(
        "encore.web.app:_factory",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


# ──────────────────────────────────────────────────────────────────────


def _find_cassettes(root: Path) -> Iterable[Path]:
    return [
        p for p in root.rglob("*.yaml")
        if "cassette" in p.parts or "cassettes" in p.parts or p.stem.startswith("test_")
    ]


def _short_url(url: str) -> str:
    # Trim auth params + truncate for readability
    return url.split("?", 1)[0]


if __name__ == "__main__":
    main()
