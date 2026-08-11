"""
House docstring style, for the one rule ruff cannot express.

Ruff's D213 requires the summary on the second line of a *multi-line* docstring, and
D200 (disabled here) would otherwise collapse a three-line docstring back to one line.
Neither rule requires the expanded form, so ``\"\"\"One liner.\"\"\"`` passes silently.
This walks the package and insists on the expanded form everywhere.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "guard_client"
Definition = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _definitions(path: pathlib.Path):
    """Yield (qualified name, node) for every documentable definition in a file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    yield path.name, tree

    stack = [(path.name, node) for node in tree.body]
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}::{node.name}"
            yield name, node
            stack.extend((name, child) for child in node.body)


def _source_files() -> list[pathlib.Path]:
    """Find all Python source files in the package directory excluding init."""
    return sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")


def test_source_files_were_found():
    """Guard the guard: an empty glob would make every check below vacuous."""
    files = _source_files()

    assert len(files) >= 15, (
        f"expected the full package, found {[p.name for p in files]}"
    )


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_every_definition_is_documented(path):
    """Dunders included, matching ruff's D105 — several carry real behaviour."""
    missing = [
        name for name, node in _definitions(path) if ast.get_docstring(node) is None
    ]

    assert not missing, f"undocumented: {missing}"


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_summaries_start_on_the_line_below_the_quotes(path):
    """The house style, matching the backend's 1205 docstrings and zero one-liners."""
    offenders = []
    for name, node in _definitions(path):
        raw = ast.get_docstring(node, clean=False)
        # an expanded docstring starts with the newline after the opening quotes;
        # """Summary.""" and """Summary\n...""" both start with the text itself.
        if raw is not None and not raw.startswith("\n"):
            offenders.append(name)

    assert not offenders, (
        f"these use the single-line form; put the summary on the line below the "
        f"opening quotes: {offenders}"
    )


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_summaries_end_with_a_period(path):
    """D415 covers this, but a named offender is easier to act on than a rule code."""
    offenders = []
    for name, node in _definitions(path):
        doc = ast.get_docstring(node)
        if doc is None:
            continue
        summary = doc.strip().splitlines()[0].strip()
        if summary and not summary.endswith((".", "?", "!", ":")):
            offenders.append(f"{name}: {summary!r}")

    assert not offenders, f"summaries must end with a period: {offenders}"
