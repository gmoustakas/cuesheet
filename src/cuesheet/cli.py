"""cuesheet CLI.

  cuesheet list                   # find cassettes under cwd
  cuesheet inspect <path>         # render a cassette in the terminal
  cuesheet stats                  # aggregate cost / interaction counts
  cuesheet scrub <path>           # re-apply scrubbers in place
  cuesheet web                    # serve the local dashboard
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cuesheet._version import __version__
from cuesheet.cassette import (
    load_cassette,
    save_cassette,
)

console = Console(width=max(120, Console().size.width))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="cuesheet")
def main() -> None:
    """cuesheet - replay LLM API calls in tests."""


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
    """Aggregate stats (interactions, tokens, cost estimate) across cassettes."""
    from cuesheet.pricing import aggregate

    files = list(_find_cassettes(root))
    if not files:
        console.print(f"[dim]No cassettes under {root}.[/dim]")
        return

    total_interactions = 0
    by_provider: dict[str, int] = {}
    streaming_count = 0
    total_bytes = 0
    all_interactions = []

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
        all_interactions.extend(cas.interactions)

    usage = aggregate(all_interactions)

    table = Table(show_header=False, box=None)
    table.add_column(style="dim", width=22)
    table.add_column()
    table.add_row("Cassette files", f"{len(files)}")
    table.add_row("Interactions", f"{total_interactions:,}")
    table.add_row("Streaming responses", f"{streaming_count:,}")
    table.add_row("Disk size", f"{total_bytes / 1024:.1f}KB")
    table.add_row("Input tokens", f"{usage['input_tokens']:,}")
    table.add_row("Output tokens", f"{usage['output_tokens']:,}")
    table.add_row("Total tokens", f"{usage['total_tokens']:,}")
    table.add_row("Estimated cost", f"[yellow]${usage['cost_usd']:.4f}[/yellow]" if usage['cost_usd'] else "[dim]$0.0000[/dim]")
    console.print(table)

    if by_provider:
        console.print("\n[bold]By provider:[/bold]")
        for prov, count in sorted(by_provider.items(), key=lambda kv: kv[1], reverse=True):
            console.print(f"  [cyan]{prov}[/cyan]  [dim]{count}[/dim]")

    if usage["by_model"]:
        console.print("\n[bold]By model:[/bold]")
        model_table = Table(box=None, header_style="dim")
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Calls", justify="right")
        model_table.add_column("Input", justify="right", style="dim")
        model_table.add_column("Output", justify="right", style="dim")
        model_table.add_column("Cost (est.)", justify="right")
        sorted_models = sorted(usage["by_model"].items(), key=lambda kv: kv[1]["cost"], reverse=True)
        for model, b in sorted_models:
            cost_cell = f"[yellow]${b['cost']:.4f}[/yellow]" if b["priced"] else "[dim]n/a[/dim]"
            model_table.add_row(
                model, f"{b['count']:,}",
                f"{b['input']:,}", f"{b['output']:,}", cost_cell,
            )
        console.print(model_table)

    if usage["unpriced_models"]:
        console.print(
            f"\n[dim]Note: no built-in price for "
            f"{', '.join(usage['unpriced_models'])}. "
            f"Costs above are partial.[/dim]"
        )


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
            "[red]cuesheet[web] is not installed.[/red]\n"
            "  [dim]pip install 'cuesheet[web]'[/dim]"
        )
        raise click.exceptions.Exit(1) from e

    root = root.resolve()
    url = f"http://{host}:{port}"
    console.print(f"[bold]cuesheet web[/bold]  [dim]{url}[/dim]  [yellow]watching[/yellow] {root}")

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

    # We pass the root via env var so the factory in cuesheet.web.app can read it.
    import os
    os.environ["CUESHEET_WEB_ROOT"] = str(root)

    uvicorn.run(
        "cuesheet.web.app:_factory",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


@main.command("diff")
@click.argument("a", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("b", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--limit", "-n", default=8, show_default=True,
              help="Maximum diff lines per interaction.")
def cmd_diff(a: Path, b: Path, limit: int) -> None:
    """Semantic diff between two cassettes.

    Pairs interactions across A and B by (method, url, model, messages), then
    reports added, removed, or response-changed interactions. Useful when
    refactoring a prompt and you want to see exactly what changed in the
    recorded output before committing.
    """
    import difflib

    from cuesheet.matchers import default_matcher

    cas_a = load_cassette(a)
    cas_b = load_cassette(b)
    match = default_matcher("method", "url", "model", "messages")

    used_b: set[int] = set()
    changed: list[tuple[int, int]] = []  # (idx_a, idx_b)
    only_in_a: list[int] = []

    for ia, ix_a in enumerate(cas_a.interactions):
        for ib, ix_b in enumerate(cas_b.interactions):
            if ib in used_b:
                continue
            if match(ix_a.request, ix_b.request):
                used_b.add(ib)
                changed.append((ia, ib))
                break
        else:
            only_in_a.append(ia)

    only_in_b = [i for i in range(len(cas_b.interactions)) if i not in used_b]

    console.print(f"[bold]{a}[/bold]  ↔  [bold]{b}[/bold]\n")
    summary = Table(show_header=False, box=None)
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("Matched pairs", f"{len(changed)}")
    summary.add_row("Only in A", f"[red]{len(only_in_a)}[/red]")
    summary.add_row("Only in B", f"[green]{len(only_in_b)}[/green]")
    console.print(summary)

    response_changes = 0
    for ia, ib in changed:
        body_a = cas_a.interactions[ia].response.body
        body_b = cas_b.interactions[ib].response.body
        if body_a == body_b:
            continue
        response_changes += 1
        console.print(
            f"\n[yellow]~ pair #{ia + 1} ↔ #{ib + 1}[/yellow]  "
            f"[dim]({_short_url(cas_a.interactions[ia].request.url)})[/dim]"
        )
        text_a = _pretty_json(body_a)
        text_b = _pretty_json(body_b)
        diff = list(difflib.unified_diff(
            text_a.splitlines(), text_b.splitlines(),
            fromfile="A.response", tofile="B.response",
            lineterm="", n=2,
        ))
        for line in diff[:limit + 3]:
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"  [green]{escape(line)}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"  [red]{escape(line)}[/red]")
            elif line.startswith("@@"):
                console.print(f"  [cyan]{escape(line)}[/cyan]")
            else:
                console.print(f"  [dim]{escape(line)}[/dim]")
        if len(diff) > limit + 3:
            console.print(f"  [dim]... ({len(diff) - limit - 3} more lines, raise --limit to see them)[/dim]")

    for ia in only_in_a:
        req = cas_a.interactions[ia].request
        console.print(
            f"\n[red]- removed[/red]  #{ia + 1}  "
            f"[dim]{req.method} {_short_url(req.url)}[/dim]"
        )
    for ib in only_in_b:
        req = cas_b.interactions[ib].request
        console.print(
            f"\n[green]+ added[/green]    #{ib + 1}  "
            f"[dim]{req.method} {_short_url(req.url)}[/dim]"
        )

    if not response_changes and not only_in_a and not only_in_b:
        console.print("\n[dim]No differences.[/dim]")


def _pretty_json(value: object) -> str:
    import json
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


@main.command("init")
@click.option("--target", default=".", type=click.Path(file_okay=False, path_type=Path),
              show_default=True, help="Directory to scaffold into.")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite files that already exist.")
def cmd_init(target: Path, force: bool) -> None:
    """Scaffold a tests/cassettes/ tree and a starter conftest into the current
    project. Idempotent: existing files are left alone unless --force is set."""
    target = target.resolve()
    cassettes_dir = target / "tests" / "cassettes"
    conftest_path = target / "tests" / "conftest.py"
    example_path = target / "tests" / "test_cuesheet_example.py"

    created: list[str] = []
    skipped: list[str] = []

    cassettes_dir.mkdir(parents=True, exist_ok=True)
    keep = cassettes_dir / ".gitkeep"
    if not keep.exists():
        keep.touch()
        created.append(str(keep.relative_to(target)))

    if conftest_path.exists() and not force:
        skipped.append(str(conftest_path.relative_to(target)))
    else:
        conftest_path.parent.mkdir(parents=True, exist_ok=True)
        conftest_path.write_text(_CONFTEST_SNIPPET, encoding="utf-8")
        created.append(str(conftest_path.relative_to(target)))

    if example_path.exists() and not force:
        skipped.append(str(example_path.relative_to(target)))
    else:
        example_path.parent.mkdir(parents=True, exist_ok=True)
        example_path.write_text(_EXAMPLE_SNIPPET, encoding="utf-8")
        created.append(str(example_path.relative_to(target)))

    for path in created:
        console.print(f"[green]+ created[/green]  {path}")
    for path in skipped:
        console.print(f"[dim]· skipped (exists)[/dim]  {path}")

    console.print(
        "\nNext step: write a test that calls your LLM SDK, "
        "wrap it with [cyan]@cuesheet.cassette(...)[/cyan], and run pytest. "
        "The first run records; every run after replays."
    )


_CONFTEST_SNIPPET = '''"""pytest configuration.

The `cuesheet_cassette` fixture auto-finds tests/cassettes/<test_name>.yaml
and binds a session for the duration of the test. It is registered for you
by cuesheet's pytest plugin; you can use it without importing anything.
"""
'''


_EXAMPLE_SNIPPET = '''"""Example cuesheet test.

First run: hits the real provider, saves the response to
tests/cassettes/test_cuesheet_example.yaml.
Every run after: replays from the YAML, no network calls.
"""
import cuesheet


@cuesheet.cassette("tests/cassettes/test_cuesheet_example.yaml")
def test_example():
    # Replace this with your real LLM call. Example with Anthropic:
    #
    #   from anthropic import Anthropic
    #   client = Anthropic()
    #   response = client.messages.create(
    #       model="claude-sonnet-4-5",
    #       max_tokens=100,
    #       messages=[{"role": "user", "content": "Say hello."}],
    #   )
    #   assert response.content[0].text
    assert True
'''


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
