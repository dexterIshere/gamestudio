"""The studio's command-line interface."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import settings as load_settings
from .domain.recipe import TEMPLATE, load_recipe
from .pipeline.base import Context

app = typer.Typer(help="Game asset generation studio (2D and 3D) for Godot.",
                  no_args_is_help=True)
style_app = typer.Typer(help="Art direction and style LoRA.", no_args_is_help=True)
library_app = typer.Typer(help="Readable mirror of the productions (.gamestudio/library).",
                          no_args_is_help=True)
video_app = typer.Typer(
    help="Reference video: frames, manifest and contact sheet.",
    no_args_is_help=True)
context_app = typer.Typer(
    help="Agent context: hand-written notes and the regenerated briefing.",
    no_args_is_help=True)
workspace_app = typer.Typer(
    help="Workspace: projects as cards, and their readable report.",
    no_args_is_help=True)
mesh_app = typer.Typer(
    help="3D meshes: the production routes, and importing an external mesh.",
    no_args_is_help=True)
doc_app = typer.Typer(
    help="Project documents: hand-written texts (.gamestudio/documents/).",
    no_args_is_help=True)
fx_app = typer.Typer(
    help="Visual effects: specs written in the effects language, rendered locally.",
    no_args_is_help=True)
prompts_app = typer.Typer(
    help="Workbench prompts: posed reference, element sheets, angles.",
    no_args_is_help=True)
inbox_app = typer.Typer(
    help="Inbox: what is dropped for an agent (screenshots, dragged files).",
    no_args_is_help=True)
folder_app = typer.Typer(
    help="Project folders: a project that lives in a folder on the machine.",
    no_args_is_help=True)
world_app = typer.Typer(
    help="World: the sections the user declares (characters, buildings...).",
    no_args_is_help=True)
skills_app = typer.Typer(
    help="Skills: the procedures, their regenerated index and their mirror.",
    no_args_is_help=True)
app.add_typer(style_app, name="style")
app.add_typer(library_app, name="library")
app.add_typer(video_app, name="video")
app.add_typer(context_app, name="context")
app.add_typer(workspace_app, name="workspace")
app.add_typer(mesh_app, name="mesh")
app.add_typer(doc_app, name="doc")
app.add_typer(world_app, name="world")
app.add_typer(fx_app, name="fx")
app.add_typer(skills_app, name="skills")
app.add_typer(inbox_app, name="inbox")
app.add_typer(folder_app, name="folder")
app.add_typer(prompts_app, name="prompts")

console = Console()


def _show_version(value: bool) -> None:
    """`--version` answers and stops: no command runs after it."""
    if value:
        console.print(f"gamestudio {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_show_version,
                                 is_eager=True, help="Studio version."),
) -> None:
    """Game asset generation studio."""


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@app.command()
def init(
    project: str = typer.Argument(..., help="Project name."),
    path: Path = typer.Option(Path("."), help="Game folder (the current folder by default)."),
) -> None:
    """Make a folder a project: `.gamestudio/` and a starter recipe."""
    from .service import folders

    opened = folders.open_folder(str(path.expanduser().resolve()), project)
    recipe = Path(opened["root"]) / ".gamestudio" / "recipe.yaml"
    if opened["created"]:
        recipe.write_text(TEMPLATE.format(project=opened["project"]), encoding="utf-8")
        console.print(f"[green]recipe created:[/green] {recipe}")
    else:
        console.print(f"[yellow]{recipe} already exists: project reopened[/yellow]")
    console.print("Next steps:")
    console.print("  1. gamestudio doctor")
    console.print(f"  2. gamestudio build {recipe}")


@app.command()
def doctor(
    check: bool = typer.Option(False, "--check",
                               help="Exit with an error if the studio cannot work."),
    as_json: bool = typer.Option(False, "--json", help="Raw output, for a script."),
) -> None:
    """Diagnose the environment: what works, what is missing, and what to do."""
    import json as jsonlib

    from .service import doctor as service

    report = service.check()
    if as_json:
        print(jsonlib.dumps(report, indent=2, ensure_ascii=False))
    else:
        colors = {"ok": "green", "warning": "yellow", "failed": "red"}
        table = Table(title=f"gamestudio {report['version']} — "
                            f"{report['counts']['ok']} ok, "
                            f"{report['counts']['warnings']} warning(s), "
                            f"{report['counts']['failures']} failure(s)")
        for column in ("Component", "State", "Detail"):
            table.add_column(column)
        for entry in report["checks"]:
            tone = colors.get(entry["status"], "white")
            table.add_row(entry["label"], f"[{tone}]{entry['status']}[/{tone}]",
                          entry["detail"])
        console.print(table)
        # The fix is all one reads when something is missing: it comes after
        # the table, in order of priority.
        if report["next"]:
            console.print("\n[bold]To do[/bold]")
            for line in report["next"]:
                console.print(f"  · {line}")
        if report["ok"]:
            console.print("\n[green]The studio can work here.[/green]")

    if check and not report["ok"]:
        console.print(f"\n[red]{service.refuse(report['counts']['failures'])}[/red]")
        raise typer.Exit(1)


# ------------------------------------------------------------ paid production

# These commands go through `service/produce`, like the API and the MCP server:
# the same refusal without consent, the same job in the database, the same
# cost accounted.

CONFIRM_HELP = "Consent to spend: without it, the command states the amount and stops."
WAIT_HELP = ("Run and follow the job here; --no-wait leaves it queued, for a worker "
             "started elsewhere (the studio, `gamestudio worker`).")


def _paid(function, *args, **kwargs) -> dict:
    """A paid service operation, translated for a terminal.

    Without `--confirm`, the service refuses and states the amount: the
    command reports it and exits with an error, queuing nothing.
    """
    from .service import PaymentRequired, ServiceError

    try:
        return function(*args, **kwargs)
    except PaymentRequired as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        console.print("Run again with [bold]--confirm[/bold] to pay and start.")
        raise typer.Exit(1) from exc
    except ServiceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _follow(queued: dict, kind: str, wait: bool) -> list[dict]:
    """Follow queued jobs until they end; return their detail.

    A worker runs in this process while waiting, restricted to `kind`: without
    it, the command would wait for an open studio. A worker already started
    elsewhere may take the job first -- the queue only gives it to one. The
    local worker finishes the job it holds before returning: an interrupted
    paid job fails, and is never restarted.
    """
    import threading
    import time

    from .jobs.worker import run_worker
    from .service import jobs

    ids = list(queued["queued"])
    for job_id in ids:
        console.print(f"  queued: [cyan]{job_id}[/cyan] ({queued['project']})")
    if not wait:
        console.print("A worker runs them (the open studio, or `gamestudio worker`); "
                      "their state shows in the studio or through the MCP tool `job_detail`.")
        return []

    stop = threading.Event()
    thread = threading.Thread(target=run_worker, daemon=True, kwargs={
        "poll_interval": 1.0, "kinds": [kind], "stop": stop, "name": "cli"})
    thread.start()
    try:
        with console.status("running…"):
            while True:
                details = [jobs.detail(job_id) for job_id in ids]
                if all(d["state"] not in ("pending", "running") for d in details):
                    return details
                time.sleep(1.0)
    except KeyboardInterrupt:
        console.print("[yellow]follow-up interrupted: the running job finishes, the "
                      "others stay queued[/yellow]")
        raise typer.Exit(130) from None
    finally:
        stop.set()
        thread.join()


def _report(detail: dict) -> None:
    """What a job produced: its state, cost, files and errors."""
    tone = {"done": "green", "needs_review": "yellow"}.get(detail["state"], "red")
    report = detail["report"]
    console.print(f"[{tone}]{detail['state']}[/{tone}] {detail['step']}  "
                  f"{detail['cost_usd']:.4f} $")
    for file in report["files"]:
        console.print(f"  {file['path']}")
    for error in [detail["error"], *report["errors"]]:
        if error:
            console.print(f"  [red]x[/red] {error}")
    if report["note"]:
        console.print(f"  [yellow]![/yellow] {report['note']}")


@style_app.command("explore")
def style_explore(
    recipe_path: Path = typer.Argument(..., help="Project recipe."),
    subject: str = typer.Option(..., "--subject", "-s", help="Subject to explore."),
    count: int = typer.Option(8, help="Number of variants (1 to 16)."),
    confirm: bool = typer.Option(False, "--confirm", help=CONFIRM_HELP),
    wait: bool = typer.Option(True, "--wait/--no-wait", help=WAIT_HELP),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate variants to choose an art direction. PAID."""
    from .service import produce

    _setup_logging(verbose)
    queued = _paid(produce.explore_style, str(recipe_path), subject, count, confirm)
    details = _follow(queued, "explore_style", wait)
    for detail in details:
        _report(detail)
    if details:
        console.print("Keep the best ones, then: gamestudio style train "
                      f"{recipe_path} --images <id> --images <id> ... --confirm")


@style_app.command("train")
def style_train(
    recipe_path: Path = typer.Argument(...),
    images: list[str] = typer.Option(..., "--images", "-i",
                                     help="Ids of approved assets (10 at least)."),
    steps: int = typer.Option(1000, help="Training steps."),
    confirm: bool = typer.Option(False, "--confirm", help=CONFIRM_HELP),
    wait: bool = typer.Option(True, "--wait/--no-wait", help=WAIT_HELP),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Train the style LoRA and freeze the art direction. PAID."""
    from .service import produce

    _setup_logging(verbose)
    queued = _paid(produce.train_style, str(recipe_path), images, steps, confirm)
    for detail in _follow(queued, "train_style", wait):
        _report(detail)
        result = detail["result"] or {}
        if result.get("air"):
            console.print(f"[green]LoRA trained[/green]: {result['air']}")
            console.print(f"trigger_word: {result.get('trigger_word')}")
            console.print("\nAdd to the recipe, style section:")
            console.print(f"  lora_air: {result['air']}")
            console.print(f"  trigger_word: {result.get('trigger_word')}")


@app.command()
def build(
    recipe_path: Path = typer.Argument(..., help="Project recipe."),
    character: list[str] = typer.Option(None, "--character", "-c",
                                        help="Limit to these characters."),
    godot_project: Path = typer.Option(None, help="Target Godot project."),
    force: bool = typer.Option(False, "--force", help="Ignore the step cache."),
    confirm: bool = typer.Option(False, "--confirm", help=CONFIRM_HELP),
    wait: bool = typer.Option(True, "--wait/--no-wait", help=WAIT_HELP),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Build the roster: A-pose reference, bare mesh, Godot export. PAID.

    The 3D rig and the animations are not part of it: they are handed to an
    agent (`gamestudio world brief`).
    """
    from .service import produce

    _setup_logging(verbose)
    target = str(godot_project.expanduser().resolve()) if godot_project else None
    queued = _paid(produce.build, str(recipe_path), characters=character or None,
                   force=force, godot_project=target, confirm=confirm)
    details = _follow(queued, "build_character", wait)
    for detail in details:
        _report(detail)
        for note in (detail["result"] or {}).get("notes") or []:
            console.print(f"  [yellow]![/yellow] {note}")
    if details:
        total = sum(detail["cost_usd"] for detail in details)
        console.print(f"\n[bold]total cost: {total:.4f} $[/bold]")


@app.command()
def status(
    recipe_path: Path = typer.Argument(...),
) -> None:
    """State of the characters already built."""
    recipe = load_recipe(recipe_path)
    with Context(project=recipe.project) as ctx:
        characters = ctx.db.list_characters(recipe.project)
        if not characters:
            console.print("[yellow]no character built[/yellow]")
            return
        table = Table(title=f"Project {recipe.project}")
        for column in ("Character", "State", "Mesh", "Bones", "Animations", "Exports"):
            table.add_column(column)
        for character in characters:
            rig = character.rig3d
            table.add_row(
                character.spec.id,
                character.state.value,
                "yes" if rig and rig.mesh_asset_id else "-",
                str(len(rig.bones)) if rig else "-",
                ", ".join(rig.animations) if rig and rig.animations else "-",
                ", ".join(character.exports) or "-",
            )
        console.print(table)


@app.command()
def mcp(
    workers: int = typer.Option(1, "--workers", "-w",
                                help="Background workers of the MCP server."),
    no_worker: bool = typer.Option(False, "--no-worker",
                                   help="Server only: a worker runs elsewhere."),
) -> None:
    """MCP server over stdio: the studio driven by an agent (Claude Code)."""
    from .mcp_server import run as run_mcp

    run_mcp(workers=0 if no_worker else workers)


@library_app.command("sync")
def library_sync(
    project: str = typer.Argument(..., help="Name of the project to sync."),
) -> None:
    """(Re)build .gamestudio/library from the database and the store."""
    from .service.context import space

    librarian = space(project).librarian
    manifest = librarian.sync_project(project)
    console.print(f"[green]{len(manifest['characters'])} character(s)[/green] "
                  f"-> {librarian.project_dir(project)}")


@library_app.command("ls")
def library_ls(
    project: str = typer.Argument(..., help="Project name."),
    folder: str = typer.Argument("", help="Folder to list (icons/my-sheet, 2d/hero)."),
    depth: int = typer.Option(1, help="1 = direct content, more to go deeper."),
    paths: bool = typer.Option(False, "--paths", "-p",
                               help="Print only absolute paths, one per line."),
) -> None:
    """List a library folder, with its paths on disk."""
    from .service.context import space

    found = space(project)
    librarian = found.librarian
    librarian.sync_project(project)
    index = librarian.index_project(project)
    base = librarian.project_dir(project)
    folder = folder.strip("/")

    prefix = f"{folder}/" if folder else ""
    depth = max(1, depth)
    entries = [f for f in index.folders
               if not folder or f.path == folder or f.path.startswith(prefix)]
    entries = [f for f in entries
               if f.path == folder or len(f.path[len(prefix):].split("/")) <= depth]

    if paths:  # raw output, for a script or an agent
        for entry in entries:
            for file in entry.files:
                print(base / entry.path / file.name)
        return

    children = index.subfolders(folder)
    if not entries and not children:
        console.print(f"[yellow]nothing in {base / folder}[/yellow] — run a "
                      "production, or import a sheet (the Library's “Import a "
                      "sheet” button, MCP tools `inspect_sheet` then `import_sheet`)")
        return
    console.print(f"[bold]{base / folder if folder else base}[/bold]")
    for path in children:
        count = sum(len(f.files) for f in index.folders
                    if f.path == path or f.path.startswith(f"{path}/"))
        console.print(f"  [cyan]{path.rsplit('/', 1)[-1]}/[/cyan]  {count} file(s)")
    for entry in entries:
        relative = f"{entry.path}/" if entry.path != folder else ""
        for file in entry.files:
            console.print(f"  {relative}{file.name}")
        for name in entry.documents:
            console.print(f"  [dim]{relative}{name}[/dim]")


@library_app.command("path")
def library_path(
    project: str = typer.Argument(..., help="Project name."),
    folder: str = typer.Argument("", help="Library folder."),
) -> None:
    """Print the absolute path of a folder -- to pass it to another tool."""
    from .store.folders import project_paths

    base = project_paths(load_settings(), project).library
    print(base / folder if folder else base)


@context_app.command("show")
def context_show(
    project: str = typer.Option("", help="Restrict the detail to one project."),
    write: bool = typer.Option(False, "--write", "-w",
                               help="Rewrite context/briefing.md before printing."),
) -> None:
    """Print the briefing: the studio's state as an agent reads it at startup."""
    from rich.markdown import Markdown

    from .service import briefing

    if write:
        console.print(f"[green]briefing written[/green] -> "
                      f"{briefing.write_briefing(project or None)}")
    console.print(Markdown(briefing.briefing(project or None)["markdown"]))


@context_app.command("write")
def context_write(
    project: str = typer.Option("", help="Restrict the detail to one project."),
) -> None:
    """Regenerate context/briefing.md without printing it -- for a script."""
    from .service import briefing

    print(briefing.write_briefing(project or None))


@context_app.command("codemap")
def context_codemap() -> None:
    """Regenerate context/codemap.md: one repository file per line, with its header."""
    from .service import codemap

    result = codemap.write()
    console.print(f"[green]code map written[/green] ({result['files']} files) "
                  f"-> {result['path']}")


@context_app.command("check")
def context_check() -> None:
    """Check that context/codemap.md is current. Exits with an error otherwise."""
    from .service import codemap

    state = codemap.check()
    if state["ok"]:
        console.print(f"[green]code map ok[/green] — {state['files']} files")
        return
    console.print(f"[red]{state['problem']}[/red]: {state['path']} "
                  "(`gamestudio context codemap`)")
    raise typer.Exit(code=1)


@context_app.command("session")
def context_session() -> None:
    """The opening context of an agent session -- what the SessionStart hook reads.

    Regenerates the briefing, refreshes the code map if it drifted, and prints
    the current state as plain text: a SessionStart hook's standard output
    enters the session's context as is.
    """
    from .service import briefing, codemap

    path = briefing.write_briefing()
    lines = [
        "Studio context, loaded automatically when the session opened.",
        "CLAUDE.md already imports the notes (context/identity.md, goals.md,",
        "preferences.md) and the code map (context/codemap.md): do not",
        "re-explore the repository to find out what it is about -- start from",
        "the map and open the relevant files directly.",
        "",
    ]
    if not codemap.check()["ok"]:
        result = codemap.write()
        lines += [f"The code map had drifted: it has just been regenerated "
                  f"({result['files']} files). Reread {result['path']} rather "
                  "than the imported version.", ""]
    print("\n".join(lines))
    print(Path(path).read_text(encoding="utf-8"))


@context_app.command("path")
def context_path() -> None:
    """Print the context files and their state, writing nothing."""
    from .service import briefing

    for entry in briefing.files():
        state = "present" if entry["exists"] else "missing"
        tag = "generated" if entry["generated"] else "by hand"
        console.print(f"[cyan]{state}[/cyan] {entry['name']:<16} [dim]{tag:<9}[/dim] "
                      f"{entry['path']}")


@workspace_app.command("ls")
def workspace_ls(
    paths: bool = typer.Option(False, "--paths", "-p",
                               help="Print only the reports, one path per line."),
) -> None:
    """Projects: category, state, cost, and their steps."""
    from .service import workspace

    data = workspace.index()
    if paths:
        for entry in data["projects"]:
            print(entry["report"])
        return
    if not data["projects"]:
        console.print("[yellow]no project[/yellow] — run a production, "
                      "or `gamestudio init <project>`")
        return
    table = Table(title=f"workspace — {data['counters']['projects']} project(s), "
                        f"{data['counters']['cost_usd']:.2f} $")
    for column in ("", "project", "category", "characters", "files",
                   "to review", "running", "cost"):
        table.add_column(column, justify="right" if column not in
                         ("", "project", "category") else "left")
    for entry in data["projects"]:
        counters = entry["counters"]
        table.add_row("*" if entry["pinned"] else "", entry["title"],
                      entry["category"], str(counters["characters"]),
                      str(counters["files"]), str(counters["to_review"]),
                      str(counters["jobs_active"]), f"{counters['cost_usd']:.2f} $")
    console.print(table)
    for entry in data["projects"]:
        if entry["steps"]:
            console.print(f"[bold]{entry['project']}[/bold] "
                          f"[dim]{len(entry['steps'])} step(s)[/dim]")
            for step in entry["steps"]:
                console.print(f"  [cyan]{step['kind']:<12}[/cyan] {step['title']}")


@workspace_app.command("build")
def workspace_build(
    project: str = typer.Argument("", help="One project, or all if omitted."),
) -> None:
    """(Re)write the projects' HTML reports."""
    from .service import workspace

    written = [workspace.write_report(project)] if project else workspace.write_all()
    for path in written:
        console.print(f"[green]report written[/green] -> {path}")


@workspace_app.command("report")
def workspace_report(
    project: str = typer.Argument(..., help="Project name."),
) -> None:
    """Regenerate a project's report and print its path."""
    from .service import workspace

    print(workspace.write_report(project))


@workspace_app.command("pin")
def workspace_pin(
    project: str = typer.Argument(..., help="Project name."),
    off: bool = typer.Option(False, "--off", help="Unpin instead of pinning."),
) -> None:
    """Pin a project (or unpin it): it moves to the top of the cards."""
    from .service import workspace

    meta = workspace.set_meta(project, pinned=not off)
    console.print(f"[green]{meta['title']}[/green] "
                  f"{'unpinned' if off else 'pinned'}")


@workspace_app.command("meta")
def workspace_meta(
    project: str = typer.Argument(..., help="Project name."),
    title: str = typer.Option("", help="Readable card title."),
    description: str = typer.Option("", help="One sentence about the project."),
    category: str = typer.Option("", help="project | workbench | research."),
    status: str = typer.Option("", help="active | dormant | archived."),
) -> None:
    """Correct a project's card. Only the given fields change."""
    from .service import workspace

    meta = workspace.set_meta(project, title=title or None,
                              description=description or None,
                              category=category or None, status=status or None)
    console.print(f"[green]{meta['title']}[/green] — {meta['category']}, "
                  f"{meta['status']}" + (" (pinned)" if meta["pinned"] else ""))


@workspace_app.command("logo")
def workspace_logo(
    project: str = typer.Argument(..., help="Project name."),
    image: Path = typer.Argument(None, help="Logo image (PNG, JPEG, GIF, WebP, SVG)."),
    clear: bool = typer.Option(False, "--clear", help="Remove the logo."),
) -> None:
    """Set (or remove) the logo shown at the top of the studio's menu."""
    from .service import workspace

    if clear:
        workspace.clear_logo(project)
        console.print(f"[yellow]logo removed[/yellow] — {project}")
        return
    if image is None:
        path = workspace.logo_path(project)
        console.print(str(path) if path else f"[dim]no logo for {project}[/dim]")
        return
    meta = workspace.set_logo_from_file(project, str(image))
    console.print(f"[green]logo set[/green] {meta['logo']['path']}")


@workspace_app.command("rm")
def workspace_rm(
    project: str = typer.Argument(..., help="Project name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Delete a project: recipe, documents, database, store, library, workspace."""
    from .service import workspace

    if not yes:
        typer.confirm(f"Permanently delete the project “{project}”?", abort=True)
    done = workspace.delete_project(project)
    console.print(f"[red]{project}[/red] deleted — {done['assets']} asset(s), "
                  f"{len(done['recipes'])} recipe(s)")


@prompts_app.command("ls")
def prompts_ls() -> None:
    """The workbench prompts, with what they are for."""
    from .service import prompts as service

    table = Table(title=f"{len(service.catalogue())} workbench prompt(s)")
    for column in ("prompt", "size", "pose", "what it is for"):
        table.add_column(column)
    for entry in service.catalogue():
        table.add_row(entry["id"], f"{entry['width']}x{entry['height']}",
                      entry["pose"] or "—", entry["what"][:60] + "…")
    console.print(table)


@prompts_app.command("show")
def prompts_show(
    prompt_id: str = typer.Argument(..., help="Prompt name (apose, multiview…)."),
    subject: str = typer.Option(..., "--subject", "-s",
                                help="What is asked for: “an old knight”."),
    style_prefix: str = typer.Option("", help="The project's style prefix."),
    style_negative: str = typer.Option("", help="The project's style negative."),
) -> None:
    """Compose a workbench prompt and print it, ready to copy."""
    from .service import prompts as service

    result = service.render(prompt_id, subject, style_prefix=style_prefix,
                            style_negative=style_negative)
    console.print(f"[bold]{result['label']}[/bold] — {result['width']}x"
                  f"{result['height']}, pose {result['pose'] or 'free'}, "
                  f"{'transparent background' if result['transparent'] else 'flat background'}")
    console.print(f"[dim]{result['what']}[/dim]")
    console.print(f"\n[cyan]positive[/cyan]\n{result['positive']}")
    console.print(f"\n[cyan]negative[/cyan]\n{result['negative']}")
    console.print(f"\n[dim]to pass to: {result['uses']}[/dim]")


@folder_app.command("ls")
def folder_ls() -> None:
    """Projects opened on a folder, most recent first."""
    from .service import folders

    rows = folders.list_folders()
    if not rows:
        console.print("[yellow]no folder opened[/yellow] — "
                      "`gamestudio folder open <path>`")
        return
    table = Table()
    for column in ("project", "folder", "opened"):
        table.add_column(column)
    for row in rows:
        where = row["root"] if row["exists"] else f"[red]{row['root']} (not found)[/red]"
        table.add_row(row["project"], where, (row["opened_at"] or "")[:16])
    console.print(table)


@folder_app.command("open")
def folder_open(
    path: Path = typer.Argument(..., help="The project folder."),
    name: str = typer.Option("", help="Project name, for a blank folder."),
) -> None:
    """Open a folder as a project: recipe, documents and library live in it."""
    from .service import folders

    result = folders.open_folder(str(path.expanduser().resolve()), name)
    verb = "created" if result["created"] else "opened"
    console.print(f"[green]{result['project']}[/green] {verb} -> {result['root']}")


@folder_app.command("close")
def folder_close(project: str = typer.Argument(..., help="Project name.")) -> None:
    """Remove a folder project from the studio. The folder is not touched."""
    from .service import folders

    result = folders.close_folder(project)
    console.print(f"{result['project']} closed — {result['root']} left intact")


@inbox_app.command("ls")
def inbox_ls(
    paths: bool = typer.Option(False, "--paths", "-p",
                               help="Print only the relative paths."),
) -> None:
    """What the inbox holds, newest first."""
    from .service import inbox

    data = inbox.summary()
    if paths:
        for entry in data["attachments"]:
            print(entry["relative"])
        return
    if not data["count"]:
        console.print(f"[yellow]empty inbox[/yellow] — drop a file with "
                      f"`gamestudio inbox add <path>` ({data['relative']})")
        return
    table = Table(title=f"{data['count']} file(s), {data['bytes'] // 1024} KB — "
                        f"{data['relative']}")
    for column in ("file", "kind", "size", "modified"):
        table.add_column(column, justify="right" if column == "size" else "left")
    for entry in data["attachments"]:
        size = (f"{entry['width']}x{entry['height']}" if entry["width"]
                else f"{entry['size_bytes'] // 1024} KB")
        table.add_row(entry["name"], entry["kind"], size, entry["modified_at"][:16])
    console.print(table)


@inbox_app.command("add")
def inbox_add(
    path: Path = typer.Argument(..., help="File to drop (copied, never moved)."),
) -> None:
    """Drop a file from disk into the inbox and print its relative path."""
    from .service import inbox

    result = inbox.add_file(str(path))
    suffix = " (already there)" if result["duplicate"] else ""
    console.print(f"[green]dropped[/green]{suffix} {result['relative']}")


@inbox_app.command("rm")
def inbox_rm(
    name: str = typer.Argument(..., help="File name in the inbox."),
) -> None:
    """Remove a file from the inbox. Irreversible."""
    from .service import inbox

    result = inbox.delete_attachment(name)
    console.print(f"[red]removed[/red] {result['path']}")


@skills_app.command("ls")
def skills_ls(
    paths: bool = typer.Option(False, "--paths", "-p",
                               help="Print only the folders, one per line."),
) -> None:
    """The studio's procedures, and what they are for."""
    from .service import skills

    found = skills.skills()
    if paths:
        for entry in found:
            print(entry["path"])
        return
    if not found:
        console.print("[yellow]no skill[/yellow] in .claude/skills/")
        return
    table = Table(title=f"{len(found)} skill(s) — {sum(e['bytes'] for e in found) // 1024} KB")
    for column in ("skill", "files", "state", "what it is for"):
        table.add_column(column, justify="right" if column == "files" else "left")
    for entry in found:
        state = "[green]ok[/green]" if not entry["problems"] else "[red]to fix[/red]"
        if entry["vendored"]:
            state += " [dim](third-party)[/dim]"
        description = entry["description"]
        table.add_row(entry["name"], str(entry["files"]), state,
                      description[:70] + ("…" if len(description) > 70 else ""))
    console.print(table)


@skills_app.command("new")
def skills_new(
    name: str = typer.Argument(..., help="Procedure title (the folder derives from it)."),
    description: str = typer.Option(..., "--description", "-d",
                                    help="When to follow it, in one sentence."),
    body: str = typer.Option("", "--body", "-b",
                             help="The text; the skeleton is written if empty."),
) -> None:
    """Add a procedure, then regenerate the index and repair the mirror."""
    from .service import ServiceError, skills

    try:
        entry = skills.create(name, description, body)
    except ServiceError as exc:
        # A taken name or a too-short description is a usage error: it is
        # reported, not traced.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]skill created[/green] {entry['name']} -> {entry['skill_file']}")
    console.print(f"  index : {entry['index']}")
    console.print(f"  mirror: {entry['mirror']}")


@skills_app.command("check")
def skills_check() -> None:
    """Check the skills, the index and the mirror. Exits with an error on drift."""
    from .service import skills

    state = skills.check()
    if state["ok"]:
        console.print(f"[green]skills ok[/green] — {state['skills']} procedures "
                      f"({state['vendored']} third-party), index current, "
                      f"mirror {state['mirror']}")
        return
    for problem in state["problems"]:
        console.print(f"[red]{problem['skill']}[/red]: {problem['problem']}")
    for missing in state["missing"]:
        console.print(f"[red]{missing}[/red]")
    raise typer.Exit(code=1)


@skills_app.command("index")
def skills_index() -> None:
    """Regenerate the skills index (.claude/skills/README.md)."""
    from .service import skills

    console.print(f"[green]index written[/green] -> {skills.write_index()['path']}")


@skills_app.command("sync")
def skills_sync() -> None:
    """Create or repair the `.agents/skills` mirror (symbolic links)."""
    from .service import skills

    result = skills.sync()
    console.print(f"[green]mirror current[/green] — {len(result['links'])} link(s) "
                  f"in {result['dir']}")
    for name in result["created"]:
        console.print(f"  [cyan]created[/cyan] {name}")
    for name in result["removed"]:
        console.print(f"  [red]removed[/red] {name}")


@skills_app.command("show")
def skills_show(
    name: str = typer.Argument(..., help="Skill name."),
    raw: bool = typer.Option(False, "--raw", help="Print the raw markdown."),
) -> None:
    """Print a skill's procedure."""
    from rich.markdown import Markdown

    from .service import skills

    entry = skills.read(name)
    console.print(f"[dim]{entry['skill_file']}[/dim]")
    console.print(Markdown(entry["text"]) if not raw else entry["text"])


@app.command()
def connections() -> None:
    """The MCP connections: where they are declared, and whether they start here."""
    from .service import connections as service

    data = service.connections()
    table = Table(title=f"MCP connections — {data['counts']['project']} in the project, "
                        f"{data['counts']['global']} global")
    for column in ("source", "server", "transport", "state", "command"):
        table.add_column(column)
    for source in data["sources"]:
        if not source["servers"]:
            table.add_row(source["label"], "[dim]—[/dim]", "", "[dim]none[/dim]",
                          f"[dim]{source['path']}[/dim]")
            continue
        for server in source["servers"]:
            state = "[green]starts[/green]" if server["available"] else "[red]not found[/red]"
            companion = server.get("companion")
            if server["available"] and companion and not companion["ready"]:
                # The server starts, but its tools will fail: say so here.
                state = "[yellow]waiting[/yellow]"
            command = f"{server['command']} {' '.join(server['args'])}".strip() \
                or server["url"]
            table.add_row(source["label"], server["name"], server["transport"],
                          state, command)
    console.print(table)
    for source in data["sources"]:
        if source.get("warning"):
            console.print(f"[yellow]{source['label']}[/yellow] — {source['warning']}")
    # A companion declared for several CLIs is still a single server to report.
    companions = {server["name"]: server["companion"]
                  for source in data["sources"] for server in source["servers"]
                  if server.get("companion")}
    for name, companion in sorted(companions.items()):
        color = "green" if companion["ready"] else "yellow"
        console.print(f"[{color}]{name}[/{color}] — {companion['detail']}")
    for note in data["notes"]:
        console.print(f"[dim]· {note}[/dim]")


SHELF_HELP = "Shelf: notes, ideas, devlog, design/..., world/<section>."


@doc_app.command("ls")
def doc_ls(
    project: str = typer.Argument(..., help="Project name."),
    paths: bool = typer.Option(False, "--paths", "-p",
                               help="Print only the paths, one per line."),
    folder: str = typer.Option("", "--folder", "-f", help=SHELF_HELP),
) -> None:
    """A project's documents, most recently modified first."""
    from .service import documents

    found = documents.documents(project, folder)
    if paths:
        for entry in found:
            print(entry["path"])
        return
    if not found:
        console.print(f"[yellow]no document[/yellow] — create one with "
                      f"`gamestudio doc new {project} \"Title\"`")
        return
    table = Table(title=f"{project} — {len(found)} document(s)")
    for column in ("document", "title", "words", "modified"):
        table.add_column(column, justify="right" if column == "words" else "left")
    for entry in found:
        table.add_row(entry["name"], entry["title"], str(entry["words"]),
                      entry["modified_at"][:16].replace("T", " "))
    console.print(table)


@doc_app.command("show")
def doc_show(
    project: str = typer.Argument(..., help="Project name."),
    name: str = typer.Argument(..., help="Document id, without .md."),
    raw: bool = typer.Option(False, "--raw", help="Print the raw markdown."),
    folder: str = typer.Option("", "--folder", "-f", help=SHELF_HELP),
) -> None:
    """Print a document (rendered by default, raw with --raw)."""
    from rich.markdown import Markdown

    from .service import documents

    document = documents.read_document(project, name, folder)
    console.print(f"[dim]{document['path']}[/dim]")
    console.print(Markdown(document["text"]) if not raw else document["text"])


@doc_app.command("new")
def doc_new(
    project: str = typer.Argument(..., help="Project name."),
    title: str = typer.Argument(..., help="Document title."),
    template: str = typer.Option("blank", help="blank | character | world | direction | "
                                                "mood | note | idea | devlog | mechanic | "
                                                "interface | icon | prop | vfx | card | "
                                                "todo."),
    folder: str = typer.Option("", "--folder", "-f", help=SHELF_HELP),
) -> None:
    """Create a document from a template. Never overwrites an existing one."""
    from .service import documents

    document = documents.create_document(project, title, template, folder)
    console.print(f"[green]created[/green] {document['path']}")


@doc_app.command("write")
def doc_write(
    project: str = typer.Argument(..., help="Project name."),
    name: str = typer.Argument(..., help="Document id, without .md."),
    path: Path = typer.Argument(..., help="File to copy in (- for standard input)."),
    folder: str = typer.Option("", "--folder", "-f", help=SHELF_HELP),
) -> None:
    """Replace a document's content with a file (or stdin)."""
    import sys

    from .service import documents

    text = sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8")
    document = documents.write_document(project, name, text, folder=folder)
    console.print(f"[green]written[/green] {document['path']} "
                  f"({document['size_bytes']} B)")


@doc_app.command("rm")
def doc_rm(
    project: str = typer.Argument(..., help="Project name."),
    name: str = typer.Argument(..., help="Document id, without .md."),
    folder: str = typer.Option("", "--folder", "-f", help=SHELF_HELP),
) -> None:
    """Delete a document. Irreversible."""
    from .service import documents

    result = documents.delete_document(project, name, folder)
    console.print(f"[red]deleted[/red] {result['path']}")


@doc_app.command("brief")
def doc_brief(
    project: str = typer.Argument(..., help="Project name."),
    shelf: str = typer.Argument(..., help="Section: interface, icons, props, mechanics, "
                                          "direction, vfx, or a world section."),
) -> None:
    """Write the brief that updates a section from what the game already holds."""
    from .service import handoff

    sent = handoff.shelf_brief(project, shelf)
    console.print(f"[green]brief[/green] {sent['path']}")
    console.print(f"[dim]{sent['prompt']}[/dim]")


@fx_app.command("brief")
def fx_brief(
    project: str = typer.Argument(..., help="Project name."),
    name: str = typer.Argument(..., help="VFX concept (design/vfx/ documents)."),
) -> None:
    """Write the brief an agent receives to make a concept in Godot."""
    from .service import handoff

    sent = handoff.brief(project, name)
    console.print(f"[green]brief[/green] {sent['path']}")
    console.print(f"[dim]{sent['prompt']}[/dim]")


@world_app.command("ls")
def world_ls(project: str = typer.Argument(..., help="Project name.")) -> None:
    """The world sections, with their number of cards."""
    from .service import world

    found = world.sections(project)
    if not found:
        console.print(f"[yellow]no section[/yellow] — create one with "
                      f"`gamestudio world new {project} \"Characters\"`")
        return
    for entry in found:
        console.print(f"{entry['id']:<20} {entry['label']:<24} "
                      f"[dim]{entry['icon']:<11}[/dim] {entry['documents']} card(s)")
        for axis in entry.get("axes") or []:
            values = ", ".join(value["label"] for value in axis["values"]) or "—"
            console.print(f"  [dim]{axis['label']:<16} {values}[/dim]")


@world_app.command("new")
def world_new(
    project: str = typer.Argument(..., help="Project name."),
    label: str = typer.Argument(..., help="Section label."),
    icon: str = typer.Option("", help="character | building | place | map | item | "
                                      "creature | faction | book | weapon | vehicle | "
                                      "plant | star."),
) -> None:
    """Declare a world section."""
    from .service import world

    entry = world.create_section(project, label, icon or None)
    console.print(f"[green]created[/green] {entry['label']} → documents {entry['folder']}/")


@world_app.command("rm")
def world_rm(
    project: str = typer.Argument(..., help="Project name."),
    section: str = typer.Argument(..., help="Section id."),
    force: bool = typer.Option(False, "--force",
                               help="Also remove its cards and their workbenches."),
) -> None:
    """Remove a section (refused if it holds cards, unless --force)."""
    from .service import ServiceError, world

    try:
        entry = world.delete_section(project, section, force=force)
    except ServiceError as exc:
        # A refused removal is a usage error: it is reported, not traced.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    for name in entry["cards"]:
        console.print(f"[red]card removed[/red] {name}")
    console.print(f"[red]removed[/red] {entry['label']}")


@world_app.command("axis")
def world_axis(
    project: str = typer.Argument(..., help="Project name."),
    section: str = typer.Argument("", help="Section id; empty: the project's axes."),
    declare: list[str] = typer.Option(
        [], "--set",
        help="“Axis=Value1,Value2”, or the id of a project axis to reuse "
             "(“race”); repeat for several axes."),
) -> None:
    """The filing axes: the project's, or a section's and their declaration.

    An axis belongs to the project: reusing it in a section does not copy it,
    and correcting it corrects it everywhere. Renaming a value detaches no
    card; removing one detaches the cards that carried it.
    """
    from .service import ServiceError, world

    if not section:
        found = world.axes(project)
        if not found:
            console.print("[yellow]no axis[/yellow] in the project")
        for axis in found:
            values = ", ".join(value["label"] for value in axis["values"]) or "—"
            users = ", ".join(entry["label"] for entry in axis["sections"]) or "no section"
            console.print(f"{axis['id']:<16} {axis['label']:<20} [dim]{values}[/dim]  "
                          f"[cyan]{users}[/cyan]")
        return
    try:
        entry = world.section(project, section)
        if declare:
            axes: list[dict[str, object] | str] = []
            for spec in declare:
                label, equals, values = spec.partition("=")
                if not equals:
                    axes.append(label.strip())
                    continue
                axes.append({"label": label.strip(),
                             "values": [{"label": value.strip()}
                                        for value in values.split(",") if value.strip()]})
            entry = world.set_axes(project, section, axes)
    except ServiceError as exc:
        # A badly declared axis is a usage error: it is reported, not traced.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if declare and entry.get("detached"):
        where = ", ".join(f"{name} ({count})" for name, count in entry["detached_in"].items())
        console.print(f"[yellow]{entry['detached']} card(s) detached[/yellow]: {where}")
    if not entry["axes"]:
        console.print(f"[yellow]no axis[/yellow] — declare one with "
                      f"`gamestudio world axis {project} {section} "
                      "--set \"Race=Human,Elf\"`")
        return
    for axis in entry["axes"]:
        values = ", ".join(value["label"] for value in axis["values"]) or "—"
        console.print(f"{axis['id']:<16} {axis['label']:<20} [dim]{values}[/dim]")


@world_app.command("file")
def world_file(
    project: str = typer.Argument(..., help="Project name."),
    section: str = typer.Argument(..., help="Section id."),
    name: str = typer.Argument(..., help="Card name."),
    assign: list[str] = typer.Option(
        ..., "--set", help="“axis=value”; repeat for several axes."),
) -> None:
    """File a card in its section's axes."""
    from .service import ServiceError, entities

    values: dict[str, str] = {}
    for spec in assign:
        axis, _, value = spec.partition("=")
        values[axis.strip()] = value.strip()
    try:
        bench = entities.set_axes(project, section, name, values)
    except ServiceError as exc:
        # A value outside the grid is a usage error: it is reported, not traced.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]filed[/green] {bench['title']}: "
                  f"{bench['axes'] or 'no axis'}")


@world_app.command("brief")
def world_brief(
    project: str = typer.Argument(..., help="Project name."),
    section: str = typer.Argument(..., help="Section id."),
    name: str = typer.Argument(..., help="Card name."),
) -> None:
    """Write the brief an agent receives to rig and animate a card's entity."""
    from .service import handoff

    sent = handoff.animation_brief(project, section, name)
    console.print(f"[green]brief[/green] {sent['path']}")
    console.print(f"[dim]{sent['prompt']}[/dim]")


@mesh_app.command("providers")
def mesh_providers() -> None:
    """The 3D routes: the local, free one, then the paid ones."""
    from .service import meshes

    for provider in meshes.mesh_providers():
        price = "[red]paid[/red]" if provider["paid"] else "[green]free[/green]"
        state = ""
        if "ready" in provider:
            state = (" [green]ready[/green]" if provider["ready"]
                     else " [yellow]key missing[/yellow]")
        console.print(f"[bold]{provider['label']}[/bold] ({provider['id']}) — "
                      f"{price}{state}")
        console.print(f"  [dim]{provider['detail']}[/dim]")
        for model in provider["models"]:
            cost = f"{model['cost_usd']:.2f} $"
            if model.get("cost_detailed_usd"):
                cost += f" (detailed {model['cost_detailed_usd']:.2f} $)"
            if model.get("faces"):
                cost += f" · {model['faces'][0]}-{model['faces'][1]} faces"
            console.print(f"  [cyan]{model['id']}[/cyan] — {model['label']} "
                          f"[dim]({cost})[/dim]")


@mesh_app.command("balance")
def mesh_balance() -> None:
    """The Tripo credit balance -- read-only, free.

    Tells what is left before starting a generation on the direct route: an
    empty account refuses the task after freezing it.
    """
    from .service import meshes
    from .tripo import TripoError

    try:
        balance = meshes.tripo_balance()
    except TripoError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]balance[/green] {balance['credits']:.2f} credits "
                  f"({balance['usd']:.2f} $) · [dim]{balance['frozen']:.2f} frozen[/dim]")


@mesh_app.command("import")
def mesh_import(
    path: Path = typer.Argument(..., help="Mesh to import (.glb, .gltf, .fbx, .obj)."),
    project: str = typer.Option("imports", help="Receiving project."),
    name: str = typer.Option("", help="Entity the mesh belongs to."),
    subject: str = typer.Option("", help="Description, if the entity is created."),
    replace: bool = typer.Option(False, "--replace",
                                 help="Replace the mesh of an entity that already has one."),
    detach: bool = typer.Option(False, "--detach",
                                help="Import without attaching to a character."),
) -> None:
    """Bring in a mesh from elsewhere -- free and local.

    The way in for 3D produced on the machine (img2threejs), for any mesh
    bought elsewhere, and for the GLB of the rigging and animating agent
    (`--replace`): the file becomes a studio asset, filed under
    `3d/<entity>/`, its bones and animations surveyed, renderable to 2D
    sprites.
    """
    from .service import meshes

    result = meshes.import_mesh(
        str(path), project=project, name=name, subject=subject,
        attach=not detach, replace=replace)
    console.print(f"[green]mesh imported[/green] -> {result['library'] or result['path']}")
    console.print(f"  asset [cyan]{result['asset_id']}[/cyan], "
                  f"entity [cyan]{result['entity']}[/cyan], "
                  f"state {result['state'] or '—'}")
    if result["rig"]:
        console.print(f"  {result['rig']}")
    if result["godot"]:
        console.print(f"  Godot: {result['godot']}")
    console.print(f"  [dim]{result['next']}[/dim]")


@video_app.command("frames")
def video_frames(
    path: Path = typer.Argument(..., help="Reference video (local file)."),
    name: str = typer.Option("", help="Batch name (default: the file name)."),
    fps: float = typer.Option(4.0, help="Requested rate, in frames per second."),
    start: float = typer.Option(0.0, help="Start of the range, in seconds."),
    end: float | None = typer.Option(None, help="End of the range, in seconds."),
    max_frames: int = typer.Option(120, "--max", "-m",
                                   help="Frame cap for the batch."),
    width: int = typer.Option(480, help="Frame width, in pixels."),
    project: str = typer.Option("imports", help="Receiving project in the library."),
) -> None:
    """Extract frames from a video: manifest and contact sheet included."""
    from .service import video as video_service

    result = video_service.extract_frames(
        str(path), name=name, project=project, fps=fps, start=start, end=end,
        max_frames=max_frames, width=width)

    source = result["video"]
    console.print(f"[bold]{source['path']}[/bold] — "
                  f"{source['duration']:.2f} s, {source['fps']:.2f} fps, "
                  f"{source['width']}x{source['height']}, {source['codec']}")
    console.print(f"[green]{result['count']} frames[/green] "
                  f"({result['start']:.2f} s → {result['end']:.2f} s at "
                  f"{result['fps']:.2f} fps) → {result['frames_folder']}")
    console.print(f"manifest: {result['manifest']}")
    if result["contact_sheet"]:
        console.print(f"sheet   : {result['contact_sheet']}")
    for warning in result["warnings"]:
        console.print(f"[yellow]![/yellow] {warning}")


@app.command()
def worker(
    poll: float = typer.Option(2.0, help="Wait interval when the queue is empty."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Process the job queue continuously."""
    _setup_logging(verbose)
    from .jobs.worker import run_worker

    run_worker(poll_interval=poll)


@app.command()
def serve(
    host: str = typer.Option(None, help="Listening address."),
    port: int = typer.Option(None, help="Listening port."),
    workers: int = typer.Option(1, "--workers", "-w",
                                help="Workers started with the server."),
    no_worker: bool = typer.Option(False, "--no-worker",
                                   help="Server only, for a separate worker."),
    auto_port: bool = typer.Option(False, "--auto-port",
                                   help="Take the next free port if busy."),
    reload: bool = typer.Option(False, "--reload", help="Reload on every edit."),
) -> None:
    """Start the web interface and the workers: the whole studio in one command."""
    import os

    import uvicorn

    from .net import find_free_port, is_port_free, port_occupant

    settings = load_settings()
    count = 0 if no_worker else max(workers, 0)
    # Through the environment rather than an argument: with --reload, uvicorn
    # reimports the module in a subprocess that would not see a value passed
    # in memory.
    os.environ["GAMESTUDIO_WORKERS"] = str(count)

    bind_host = host or settings.api_host
    bind_port = port or settings.api_port

    # Check before starting: uvicorn only fails after starting the lifespan,
    # and its message does not say who holds the port.
    if not is_port_free(bind_host, bind_port):
        occupant = port_occupant(bind_port)
        console.print(f"[red]Port {bind_port} is already in use"
                      f"{f' by {occupant}' if occupant else ''}.[/red]")
        if auto_port:
            replacement = find_free_port(bind_host, bind_port + 1)
            if replacement is None:
                console.print("[red]No free port found in the next range.[/red]")
                raise typer.Exit(1)
            console.print(f"[yellow]Switching to port {replacement}.[/yellow]")
            bind_port = replacement
        else:
            suggestion = find_free_port(bind_host, bind_port + 1)
            console.print(
                f"Start again on another port: [bold]gamestudio serve --port "
                f"{suggestion or bind_port + 1}[/bold]\n"
                "or let it choose: [bold]gamestudio serve --auto-port[/bold]\n"
                "or set GAMESTUDIO_API_PORT in .env once and for all."
            )
            raise typer.Exit(1)

    address = f"http://{bind_host}:{bind_port}"
    console.print(f"[bold]interface[/bold]  {address}")
    console.print(f"[bold]workers[/bold]    {count or 'none (--no-worker)'}")

    # Write the token before the port answers: the shell reads it from disk as
    # soon as the connection succeeds, and would find no file if the server
    # created it on its first request.
    from .api import auth

    auth.publish()
    if auth.token_path().exists():
        console.print(f"[bold]token[/bold]      {auth.token_path()}")

    if settings.project_root is None:
        console.print("[yellow]no project detected: start from a folder "
                      "holding a .env[/yellow]")
    elif not settings.runware_api_key:
        console.print("[yellow]RUNWARE_API_KEY missing: the generation steps "
                      "will fail[/yellow]")

    # No access log: it would write every URL, and `<img>` tags, SSE streams
    # and WebSockets carry the token in theirs (`?token=`).
    uvicorn.run("gamestudio.api.app:app", host=bind_host, port=bind_port, reload=reload,
                access_log=False)


# -------------------------------------------------------------------- effects


def _fx_call(function, *args, **kwargs):
    from .service import ServiceError

    try:
        return function(*args, **kwargs)
    except ServiceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@fx_app.command("ls")
def fx_ls(project: str = typer.Argument(..., help="Project name.")) -> None:
    """A project's effects and their latest render."""
    from .service import effects

    rows = _fx_call(effects.effects, project)
    if not rows:
        console.print("[dim]no effect[/dim]")
    for row in rows:
        state = "[red]invalid[/red]" if not row["valid"] else (
            f"render {row['build']['build']}" if row["build"] else "[dim]never rendered[/dim]")
        console.print(f"{row['name']:<24} {state}  [dim]{row['path']}[/dim]")


@fx_app.command("ref")
def fx_ref() -> None:
    """The reference of the effects language."""
    from rich.markdown import Markdown

    from .service import effects

    console.print(Markdown(effects.reference()))


@fx_app.command("show")
def fx_show(project: str = typer.Argument(..., help="Project name."),
            name: str = typer.Argument(..., help="Effect name.")) -> None:
    """Print an effect's spec."""
    from .service import effects

    effect = _fx_call(effects.read_effect, project, name)
    console.print(f"[dim]{effect['path']}[/dim]")
    if effect["error"]:
        console.print(f"[red]{effect['error']}[/red]")
    console.print(effect["spec"], markup=False, highlight=False)


@fx_app.command("write")
def fx_write(project: str = typer.Argument(..., help="Project name."),
             name: str = typer.Argument(..., help="Effect name."),
             path: Path = typer.Argument(None, help="YAML file (- for stdin; omitted: "
                                                     "neutral canvas, new effect).")) -> None:
    """Write an effect's spec. A broken spec is refused, the old one stays."""
    import sys

    from .service import effects

    if path is None:
        effect = _fx_call(effects.create_effect, project, name)
    else:
        text = sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8")
        effect = _fx_call(effects.write_effect_spec, project, name, text)
    console.print(f"[green]written[/green] {effect['path']}")


@fx_app.command("preview")
def fx_preview(project: str = typer.Argument(..., help="Project name."),
               name: str = typer.Argument(..., help="Effect name."),
               scale: float = typer.Option(0.5, help="Preview scale (0.1 to 1).")) -> None:
    """Render a preview (nothing is filed) and write its contact sheet."""
    from .service import effects

    result = _fx_call(effects.preview_effect, project, name, scale=scale)
    meta = result["meta"]
    console.print(f"{meta['frames']} frames {meta['frame_width']}x{meta['frame_height']} "
                  f"in {result['seconds']} s — contact: {result['contact_sheet']}")
    for note in result["notes"]:
        console.print(f"[yellow]{note}[/yellow]")


@fx_app.command("build")
def fx_build(project: str = typer.Argument(..., help="Project name."),
             name: str = typer.Argument(..., help="Effect name."),
             validate: bool = typer.Option(True, help="Have Godot load the scenes.")
             ) -> None:
    """Render at full size, file in the library, export to Godot."""
    from .service import effects

    result = _fx_call(effects.build_effect, project, name, validate=validate)
    godot = result["godot"]
    console.print(f"library: {result['library']}")
    console.print(f"godot  : {godot['scene']} ({godot['validation']})")
    if godot["output"]:
        console.print(godot["output"], markup=False)
    for note in result["notes"]:
        console.print(f"[yellow]{note}[/yellow]")
    if godot["validation"] == "failed":
        raise typer.Exit(1)


@app.command("render")
def render(
    project: str = typer.Argument(..., help="Project name."),
    scene: str = typer.Argument("", help="res://... or a path from the game root; "
                                         "empty = the main scene."),
    name: str = typer.Option("", help="Render name: renders/<name>.png."),
    folder: str = typer.Option("", help="Shelf of the card that will cite the image."),
    scale: float = typer.Option(2.0, help="Multiple of the game's resolution."),
    crop: bool = typer.Option(False, "--crop", help="Crop to the scene root."),
    locale: str = typer.Option("", help="Render language (fr, en...)."),
) -> None:
    """Render a game scene to PNG, through its engine, offscreen."""
    from .service import renders

    found = renders.render_scene(project, scene, name=name, folder=folder, scale=scale,
                                 crop=crop, locale=locale)
    console.print(f"[green]rendered[/green] {found['path']} "
                  f"({found['width']} x {found['height']}, {found['engine']})")
    if found.get("markdown"):
        console.print(found["markdown"])
    for note in found["notes"]:
        console.print(f"[yellow]{note}[/yellow]")


@app.command("validate-godot")
def validate_godot(
    godot_project: Path = typer.Argument(..., help="Godot project to check."),
    scene: str = typer.Option(None, help="A specific scene (res://...)."),
) -> None:
    """Check a scene, a mesh or an effect by running Godot headless."""
    import subprocess

    binary = shutil.which("godot") or shutil.which("godot4")
    if binary is None:
        console.print("[red]Godot not found in the PATH[/red]")
        raise typer.Exit(1)

    script = Path(__file__).parent.parent.parent / "scripts" / "validate_godot_scene.gd"
    if not script.exists():
        console.print(f"[red]validation script not found: {script}[/red]")
        raise typer.Exit(1)
    shutil.copyfile(script, godot_project / script.name)

    subprocess.run([binary, "--headless", "--path", str(godot_project), "--import"],
                   capture_output=True, text=True, timeout=600)

    scenes = [scene] if scene else [
        "res://" + p.relative_to(godot_project).as_posix()
        for p in sorted(godot_project.rglob("*.tscn"))
    ]
    failures = 0
    for target in scenes:
        result = subprocess.run(
            [binary, "--headless", "--path", str(godot_project),
             "--script", f"res://{script.name}", "--", target],
            capture_output=True, text=True, timeout=600,
        )
        output = (result.stdout + result.stderr)
        if "RESULT: OK" in output:
            console.print(f"[green]OK[/green] {target}")
        else:
            failures += 1
            console.print(f"[red]FAILED[/red] {target}")
            for line in output.splitlines():
                if "FAILED" in line or "animation " in line or "MeshInstance3D" in line:
                    console.print(f"    {line}")
    if failures:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
