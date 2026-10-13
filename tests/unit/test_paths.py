from __future__ import annotations

import os

import pytest

from agentbridge.domain.paths import ensure_within, normalize, normalize_many
from agentbridge.errors import InvalidInputError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/auth/login.py", "src/auth/login.py"),
        ("src/auth/", "src/auth/"),
        ("./src//auth/./login.py", "src/auth/login.py"),
        ("src\\auth\\login.py", "src/auth/login.py"),
        ("  docs/README.md  ", "docs/README.md"),
        (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
    ],
)
def test_normalize_accepts(raw, expected):
    assert str(normalize(raw)) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "/etc/passwd",
        "C:/Windows",
        "c:\\x",
        "../outside.py",
        "src/../../x",
        "src/*.py",
        "src/**/",
        "file?.txt",
        "src/[ab].py",
        ".git/config",
        ".git/",
        ".GIT/hooks/pre-commit",
        "./",
        "a\x00b",
        "x" * 600,
    ],
)
def test_normalize_rejects(raw):
    with pytest.raises(InvalidInputError):
        normalize(raw)


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("src/auth/", "src/auth/login.py", True),
        ("src/auth/login.py", "src/auth/", True),
        ("src/auth/login.py", "src/auth/login.py", True),
        ("src/", "src/auth/deep/x.py", True),
        ("src/auth/", "src/authz.py", False),
        ("src/auth/", "src/authz/", False),
        ("src/a.py", "src/b.py", False),
        ("docs/", "src/", False),
    ],
)
def test_overlaps(a, b, overlap):
    assert normalize(a).overlaps(normalize(b)) is overlap
    assert normalize(b).overlaps(normalize(a)) is overlap


def test_normalize_many_deduplicates_preserving_order():
    paths = normalize_many(["b.py", "./a.py", "b.py", "a.py"])
    assert [str(p) for p in paths] == ["b.py", "a.py"]


def test_ensure_within_accepts_nonexistent_file(tmp_path):
    assert ensure_within(tmp_path, normalize("new/file.py")) == tmp_path.resolve() / "new" / "file.py"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="sin soporte de enlaces simbólicos")
def test_ensure_within_rejects_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "docs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidInputError, match="sale del repositorio"):
        ensure_within(repo, normalize("docs/secret.txt"))


def test_ensure_within_allows_symlink_inside_repo(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    assert ensure_within(tmp_path, normalize("alias/x.py")) == tmp_path.resolve() / "real" / "x.py"
