"""The window translates what the server tells it: every message has its translation.

The server speaks English to all its interfaces -- API, MCP, CLI. The window can
be in French (`app/src/lib/i18n.ts`): what it shows from the server -- errors,
labels, diagnostics, refusal reasons -- is translated through
`app/src/locales/fr-server.json`, where each value of a composed message stands
as `{0}`, `{1}`... (`f"project {name} not found"` becomes
`"project {0} not found"`).

This test collects from the code the texts the window can receive, and fails if
one is missing: a message added to the service gets its translation, or the
French interface would show it in English.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "gamestudio"
CATALOGUE = Path(__file__).resolve().parents[1] / "app" / "src" / "locales" / "fr-server.json"

# What the window never receives: the scripts run inside Blender, the CLI and
# the MCP server (read by a terminal or an agent), and the effects and video
# services, which no page calls.
OUTSIDE_WINDOW = ("blender/", "vfx/", "video/")
OUTSIDE_WINDOW_FILES = {"cli.py", "mcp_server.py", "service/effects.py", "service/video.py"}

# The response keys whose value is a text shown as is.
KEYS = {"label", "detail", "fix", "hint", "reason", "note", "notes", "why", "summary",
        "warning", "description", "error", "problem"}

# The functions that build a shown line: the index of their text arguments.
BUILDERS = {"_entry": (1, 3), "_prop": (1, 3), "_prompt": (1,)}

# The choice lists of a screen property: `[value, label]`.
CHOICES = {"ALIGN_H", "ALIGN_V", "STRETCH", "EXPAND"}

# What the survey cannot see, for lack of a literal to read: state constants,
# an assembled summary, and the texts of the Rust shell
# (`app/src-tauri/src/update.rs`), which go through the same catalogue.
ALSO = {
    "warning", "failed", "root",
    "{0} element(s)", "{0} refused", "{0} element(s), {1} refused", "nothing to do",
    "Checking…", "Python dependencies", "Interface", "Application",
    "make not found: {0}", "the build failed ({0})",
}


def _template(node: ast.AST) -> str | None:
    """The text of a literal or an f-string, each value turned into `{n}`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts, rank = [], 0
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            else:
                parts.append(f"{{{rank}}}")
                rank += 1
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _template(node.left), _template(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _texts(node: ast.AST) -> list[str | None]:
    """The templates of a value: each branch of an `a if c else b`, each list element."""
    if isinstance(node, ast.IfExp):
        return _texts(node.body) + _texts(node.orelse)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [text for element in node.elts for text in _texts(element)]
    return [_template(node)]


def server_texts() -> dict[str, str]:
    """Every text the window can receive, with the place it comes from."""
    found: dict[str, str] = {}

    def note(texts: list[str | None], where: str) -> None:
        for text in texts:
            # A template made only of values has nothing to translate.
            if text and re.sub(r"\{\d+\}", "", text).strip() and any(c.isalpha() for c in text):
                found.setdefault(text, where)

    for file in sorted(SRC.rglob("*.py")):
        rel = file.relative_to(SRC).as_posix()
        if rel.startswith(OUTSIDE_WINDOW) or rel in OUTSIDE_WINDOW_FILES:
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                note([_template(node.exc.args[0])], where)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value in KEYS:
                        note(_texts(value), where)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {target.id for target in targets if isinstance(target, ast.Name)}
                if names & KEYS:
                    note(_texts(node.value), where)
                if names & CHOICES and isinstance(node.value, ast.List):
                    note([_template(pair.elts[1]) for pair in node.value.elts
                          if isinstance(pair, ast.List) and len(pair.elts) == 2], where)
            elif (isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
                  and isinstance(node.iter, (ast.Tuple, ast.List))):
                # `for key, label, … in (("a", "A", …), …)`: each row's label.
                ranks = [rank for rank, target in enumerate(node.target.elts)
                         if isinstance(target, ast.Name) and target.id in KEYS]
                for row in node.iter.elts:
                    if isinstance(row, (ast.Tuple, ast.List)):
                        note([_template(row.elts[rank]) for rank in ranks if rank < len(row.elts)],
                             where)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                for index in BUILDERS.get(node.func.id, ()):
                    if index < len(node.args):
                        note([_template(node.args[index])], where)
                for keyword in node.keywords:
                    if keyword.arg in {"fix", "description", "label"}:
                        note(_texts(keyword.value), where)
    for text in ALSO:
        found.setdefault(text, "test_translations.ALSO")
    return found


def _catalogue() -> dict[str, str]:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def test_every_server_text_has_its_translation() -> None:
    catalogue = _catalogue()
    missing = {text: where for text, where in server_texts().items() if text not in catalogue}
    assert not missing, (
        f"{len(missing)} server text(s) without a translation: add them to "
        f"app/src/locales/fr-server.json\n"
        + "\n".join(f"  {where}: {json.dumps(text, ensure_ascii=False)}"
                    for text, where in sorted(missing.items(), key=lambda item: item[1])))


def test_a_translation_keeps_the_values_of_its_template() -> None:
    """`{0}`, `{1}`... go from English to French: none is lost, none is invented."""
    wrong = [english for english, french in _catalogue().items()
             if sorted(re.findall(r"\{\d+\}", english)) != sorted(re.findall(r"\{\d+\}", french))]
    assert not wrong, f"values lost or invented: {wrong}"


def test_the_survey_sees_the_service_messages() -> None:
    """The survey does read the code: a known message, an f-string, a label."""
    found = server_texts()
    assert "empty document: nothing to save" in found
    assert "document not found: {0}" in found
    assert "Character bible" in found
