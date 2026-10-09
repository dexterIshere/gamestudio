"""The graphic style and the game type: their cards, their board of influences, their thread.

What these tests protect: the game type is written part by part -- gameplay,
setting, lore --, a card each, created only once written; an influence is
named with what is kept and left, and a blend crosses influences that exist;
a dropped image is filed with the board's card and shown with its influence;
an image proposal is free, its amount is stated, and only the user's consent
pays it -- once --; once its job is done, its images join their influence.
The thread drives Claude Code headless -- a fake one here --: the answer
arrives as it is written, what the agent says before acting goes into its
steps, the conversation is resumed under its own id, a turn is one at a time
and can be stopped, and a new discussion keeps the previous one.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.service import build, direction_chat, documents, folders, influences, using
from gamestudio.service.context import space
from gamestudio.service.errors import NotFound, PaymentRequired, ServiceError
from gamestudio.terminal import harnesses

PROJECT_GODOT = 'config_version=5\n\n[application]\nconfig/name="Trial"\n'


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    (studio_dir / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"gamestudio": {"command": ".venv/bin/gamestudio", "args": ["mcp"]}}}),
        encoding="utf-8")
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = tmp_path / "my-game"
    (root / "client").mkdir(parents=True)
    (root / "client" / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root
        direction_chat.stop_all()


def _png(color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 9), color).save(buffer, "PNG")
    return buffer.getvalue()


def test_an_influence_is_named_with_its_images(game: Path) -> None:
    tiles = influences.set_influence("game", "style", name="Dorfromantik", kind="game",
                                     keep="thick tiles", avoid="villages")
    assert (tiles["id"], tiles["keep"], tiles["avoid"]) == ("dorfromantik", "thick tiles",
                                                            "villages")
    card = documents.read_document("game", "style-influences", "design/direction")
    assert card["title"] == "Graphic style influences", "the board's card, from its template"

    with pytest.raises(ServiceError, match="unknown kind"):
        influences.set_influence("game", "style", name="X", kind="painting")
    with pytest.raises(NotFound):
        influences.set_influence("game", "style", name="Mix", kind="blend", of=["nothing"])
    blend = influences.set_influence("game", "style", name="Toy planet", kind="blend",
                                     of=["dorfromantik"])
    assert blend["of"] == ["dorfromantik"]

    board = influences.add_reference("game", "style", "dorfromantik", _png(), "tile.png")
    tiles = next(entry for entry in board["influences"] if entry["id"] == "dorfromantik")
    [image] = tiles["images"]
    assert image["key"] == "ref:tile.png" and Path(image["path"]).is_file()
    assert Path(image["path"]).parent.name == "style-influences.references"
    assert influences.summary("game")["style"] == {"influences": 2, "written": 0, "parts": 1}
    assert influences.summary("game")["game"] == {"influences": 0, "written": 0, "parts": 3}

    renamed = influences.set_influence("game", "style", influence="dorfromantik",
                                       name="Dorfromantik, the tiles", keep="")
    assert (renamed["id"], renamed["name"], renamed["keep"], renamed["avoid"]) == \
        ("dorfromantik", "Dorfromantik, the tiles", "", "villages"), "renamed, the id stays"
    influences.add_reference("game", "style", "dorfromantik", _png("blue"), "sea.png")
    influences.set_influence("game", "style", influence="toy-planet", name="Toy planet",
                             images=["ref:sea.png"])
    board = influences.remove_image("game", "style", "dorfromantik", "ref:sea.png")
    assert Path(image["path"]).parent.joinpath("sea.png").is_file(), \
        "an image another influence shows stays"
    board = influences.remove_image("game", "style", "toy-planet", "ref:sea.png")
    assert not Path(image["path"]).parent.joinpath("sea.png").exists(), \
        "a dropped image nothing shows any more leaves"
    with pytest.raises(NotFound):
        influences.remove_image("game", "style", "toy-planet", "ref:sea.png")

    board = influences.remove_influence("game", "style", "dorfromantik")
    assert [entry["id"] for entry in board["influences"]] == ["toy-planet"]
    assert board["influences"][0]["of"] == [], "a blend forgets what left the board"
    assert Path(image["path"]).is_file(), "the image stays with the card"
    with pytest.raises(NotFound):
        influences.board("game", "sound")


def test_the_game_type_is_written_part_by_part(game: Path) -> None:
    parts = {part["id"]: part for part in influences.board("game", "game")["parts"]}
    assert list(parts) == ["gameplay", "setting", "lore"]
    assert not any(part["written"] or part["path"] for part in parts.values()), \
        "nothing is created before it is written"
    assert parts["lore"]["template"].startswith("# Lore\n")
    documents.write_document("game", "lore", parts["lore"]["template"], folder="design/direction")
    assert not influences.board("game", "game")["parts"][2]["written"], "a bare template is empty"
    documents.write_document("game", "lore", "# Lore\n\nA colony ship, lost.\n",
                             folder="design/direction")
    lore = influences.board("game", "game")["parts"][2]
    assert lore["written"] and "colony ship" in lore["text"]
    assert influences.summary("game")["game"]["written"] == 1


def test_a_proposal_is_paid_by_the_user_only(game: Path) -> None:
    influences.set_influence("game", "game", name="Star Wars", kind="film")
    proposal = influences.propose("game", "game", influence="star-wars",
                                  prompt="a desert planet under two suns", why="the scale")
    assert proposal["status"] == "proposed" and proposal["cost_usd"] == pytest.approx(0.0052)
    with pytest.raises(ServiceError, match="Kontext"):
        influences.propose("game", "game", influence="star-wars", prompt="x",
                           model="runware:106@1")
    with pytest.raises(NotFound):
        influences.propose("game", "game", influence="nothing", prompt="x")

    with pytest.raises(PaymentRequired, match=r"\$0\.003 for 2 image"):
        influences.pay("game", "game", proposal["id"], count=2)
    st = space("game")
    assert st.queue.claim() is None, "nothing leaves without consent"

    paid = influences.pay("game", "game", proposal["id"], count=2, confirm=True)
    assert (paid["status"], paid["count"]) == ("queued", 2)
    with pytest.raises(ServiceError, match="already queued"):
        influences.pay("game", "game", proposal["id"], confirm=True)
    job = st.queue.claim()
    assert job is not None and job.id == paid["job"]
    assert job.payload["card"] == "design/direction/game-influences"
    assert job.payload["style"] is None
    board = influences.board("game", "game")
    assert board["proposals"][0]["status"] == "queued"

    asset = st.db.save_asset(st.store.put_bytes(_png("navy"), ".png", kind="image", meta={
        "role": "generation", "project": "game",
        "card": "design/direction/game-influences",
        "prompt": "a desert planet under two suns", "model": "runware:100@1"}))
    st.queue.complete(job, {"assets": [asset.id]})
    board = influences.board("game", "game")
    assert board["proposals"][0]["status"] == "done"
    assert [image["asset_id"] for image in board["influences"][0]["images"]] == [asset.id]
    [shown] = board["gallery"]
    assert (shown["asset_id"], shown["influence"]) == (asset.id, "star-wars"), \
        "every generated image is in the aspect's gallery"
    whole = influences.propose("game", "game", prompt="the whole game, in one image")
    assert whole["influence"] == "", "images of the aspect as a whole"

    other = influences.propose("game", "game", influence="star-wars", prompt="a cantina")
    assert influences.dismiss("game", "game", other["id"])["status"] == "dismissed"
    with pytest.raises(ServiceError, match="already dismissed"):
        influences.pay("game", "game", other["id"], confirm=True)


# A fake Claude Code: it notes its arguments and the message it reads, says a
# word before acting, calls a tool, then answers -- or waits, if asked to.
FAKE_CLAUDE = """#!{python}
import json, sys, time
args = sys.argv[1:]
message = sys.stdin.read()
with open({log!r}, "a") as log:
    log.write(json.dumps({{"args": args, "message": message}}) + "\\n")
session = args[args.index("--session-id") + 1] if "--session-id" in args else \\
    args[args.index("--resume") + 1]
def say(event):
    print(json.dumps(event), flush=True)
def text(words):
    say({{"type": "stream_event", "event": {{"type": "content_block_start",
         "content_block": {{"type": "text", "text": ""}}}}}})
    for word in words:
        say({{"type": "stream_event", "event": {{"type": "content_block_delta",
             "delta": {{"type": "text_delta", "text": word}}}}}})
say({{"type": "system", "subtype": "init", "session_id": session}})
say({{"type": "stream_event", "event": {{"type": "message_start"}}}})
text(["Let me ", "look."])
say({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Read",
     "input": {{"file_path": {readme!r}}}}}]}}}})
if "slow" in message:
    time.sleep(30)
say({{"type": "stream_event", "event": {{"type": "message_start"}}}})
text(["The game ", "is a **toy**."])
say({{"type": "result", "subtype": "success", "is_error": False}})
"""


def _wait(project: str, aspect: str) -> dict:
    for _ in range(200):
        held = direction_chat.thread(project, aspect)
        if not held["running"]:
            return held
        time.sleep(0.05)
    raise AssertionError("the agent never finished")


def test_the_thread_drives_the_agent(game: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "claude.log"
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable, log=str(log),
                                       readme=str(game / "README.md")), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(harnesses, "search_path",
                        lambda env=None: f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    influences.set_influence("game", "style", name="Dorfromantik", kind="game",
                             keep="thick tiles")
    direction_chat.send("game", "style", "What do you see?", lang="fr")
    held = _wait("game", "style")
    user, answer = held["messages"]
    assert user["text"] == "What do you see?"
    assert (answer["state"], answer["text"]) == ("done", "The game is a **toy**.")
    assert [step.get("note") or step["tool"] for step in answer["steps"]] == \
        ["Let me look.", "Read"], "what the agent says before acting is a step"
    assert answer["steps"][1]["detail"] == "README.md", "paths relative to the game"
    first = json.loads(log.read_text().splitlines()[0])
    assert first["message"] == "What do you see?", "the message goes through stdin"
    args = first["args"]
    assert args[args.index("--session-id") + 1] == held["session"]
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep"
    assert "mcp__gamestudio__influence_propose" in args
    assert "mcp__gamestudio__influence_set" not in args, "the board is the user's"
    assert args[args.index("--model") + 1] == "sonnet", "the lightest model by default"
    assert not any("card_generate" in arg or "generate_image" in arg for arg in args), \
        "the agent cannot pay"
    system = args[args.index("--append-system-prompt") + 1]
    assert "Answer in French" in system and "`graphic-style`" in system
    assert "**Dorfromantik** (game, id `dorfromantik`) -- keep: thick tiles" in system, \
        "the influences are in mind before anything is written or proposed"
    assert "The graphic style, written by you, comes from the influences' images" in system

    direction_chat.send("game", "style", "And slow down")
    with pytest.raises(ServiceError, match="still answering"):
        direction_chat.send("game", "style", "again")
    for _ in range(200):
        if len(log.read_text().splitlines()) == 2:
            break
        time.sleep(0.05)
    resumed = json.loads(log.read_text().splitlines()[1])["args"]
    assert resumed[resumed.index("--resume") + 1] == held["session"], "the same conversation"
    direction_chat.stop("game", "style")
    assert _wait("game", "style")["messages"][-1]["state"] == "stopped"

    assert direction_chat.reset("game", "style")["messages"] == []
    kept = list((documents.directory("game") / "lookdev").glob("style.chat.*.json"))
    assert len(kept) == 1, "the previous discussion is kept"


# A fake Claude Code asked for one prompt: it notes what it is shown, and answers.
FAKE_DRAFT = """#!{python}
import json, sys
message = sys.stdin.read()
with open({log!r}, "w") as log:
    json.dump({{"args": sys.argv[1:], "message": message}}, log)
answer = {{"seen": "Thick hex tiles, varnished.", "prompt": "a toy planet of thick hex tiles"}}
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                  "result": "Here it is: " + json.dumps(answer)}}))
"""


def test_a_prompt_is_drafted_from_the_influences_images(game: Path, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "draft.json"
    fake = bin_dir / "claude"
    fake.write_text(FAKE_DRAFT.format(python=sys.executable, log=str(log)), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(harnesses, "search_path",
                        lambda env=None: f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    influences.set_influence("game", "style", name="Dorfromantik", kind="game",
                             keep="thick tiles")
    with pytest.raises(ServiceError, match="no influence with images"):
        direction_chat.draft("game", "style")
    board = influences.add_reference("game", "style", "dorfromantik", _png(), "tile.png")
    [image] = board["influences"][0]["images"]
    with pytest.raises(NotFound):
        direction_chat.draft("game", "style", sources=["nothing"])

    proposal = direction_chat.draft("game", "style", request="a whole planet", count=2,
                                    lang="fr")
    assert (proposal["influence"], proposal["prompt"], proposal["why"], proposal["count"]) == \
        ("dorfromantik", "a toy planet of thick hex tiles", "Thick hex tiles, varnished.", 2)
    assert proposal["status"] == "proposed", "drafted, never paid"
    shown = json.loads(log.read_text())
    assert image["path"] in shown["message"] and "a whole planet" in shown["message"], \
        "the model is shown the images, and what the user wants"
    args = shown["args"]
    assert args[args.index("--tools") + 1] == "Read" and "--strict-mcp-config" in args
    assert str(Path(image["path"]).parent) in args, "it may read the images' folder"

