from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

import pytest
from scripts.reading_order_post_v2_qualification import bootstrap_v3

GIT = which("git")
assert GIT is not None


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        [GIT, "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Qualification Test",
        "-c",
        "user.email=qualification@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repository, "rev-parse", "HEAD")


def test_materialized_source_uses_explicit_commit_after_head_moves(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    source = repository / "package" / "producer.py"
    source.parent.mkdir()
    source.write_text("ORIGIN = 'authorized'\n", encoding="utf-8")
    execution_sha = _commit(repository, "authorized source")
    execution_tree = _git(repository, "rev-parse", f"{execution_sha}^{{tree}}")
    source_blob = _git(repository, "rev-parse", f"{execution_sha}:package/producer.py")

    source.write_text("ORIGIN = 'mutable-head'\n", encoding="utf-8")
    mutable_head = _commit(repository, "move head")
    assert mutable_head != execution_sha

    destination = tmp_path / "snapshot"
    bootstrap_v3.materialize_git_snapshot(
        git_root=repository,
        execution_sha=execution_sha,
        expected_tree_sha=execution_tree,
        bindings={"package/producer.py": source_blob},
        destination=destination,
    )

    assert (destination / "package" / "producer.py").read_text(encoding="utf-8") == (
        "ORIGIN = 'authorized'\n"
    )
    assert _git(repository, "rev-parse", "HEAD") == mutable_head


def test_materialized_source_ignores_git_replacement_refs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    source = repository / "source.py"
    source.write_text("VALUE = 'authorized'\n", encoding="utf-8")
    execution_sha = _commit(repository, "authorized source")
    execution_tree = _git(repository, "rev-parse", f"{execution_sha}^{{tree}}")
    source_blob = _git(repository, "rev-parse", f"{execution_sha}:source.py")
    replacement = repository / "replacement.py"
    replacement.write_text("VALUE = 'malicious'\n", encoding="utf-8")
    replacement_blob = _git(repository, "hash-object", "-w", str(replacement))
    _git(repository, "replace", source_blob, replacement_blob)

    destination = tmp_path / "snapshot"
    bootstrap_v3.materialize_git_snapshot(
        git_root=repository,
        execution_sha=execution_sha,
        expected_tree_sha=execution_tree,
        bindings={"source.py": source_blob},
        destination=destination,
    )

    assert (destination / "source.py").read_text(encoding="utf-8") == (
        "VALUE = 'authorized'\n"
    )


@pytest.mark.parametrize("mismatch", ["tree", "blob"])
def test_materialized_source_rejects_wrong_git_identity(
    mismatch: str, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    source = repository / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    execution_sha = _commit(repository, "source")
    execution_tree = _git(repository, "rev-parse", f"{execution_sha}^{{tree}}")
    source_blob = _git(repository, "rev-parse", f"{execution_sha}:source.py")

    with pytest.raises(ValueError, match=mismatch):
        bootstrap_v3.materialize_git_snapshot(
            git_root=repository,
            execution_sha=execution_sha,
            expected_tree_sha="0" * 40 if mismatch == "tree" else execution_tree,
            bindings={"source.py": "0" * 40 if mismatch == "blob" else source_blob},
            destination=tmp_path / "snapshot",
        )

    assert not (tmp_path / "snapshot").exists()


def test_parent_bootstrap_rejects_mutable_checkout_origin() -> None:
    repository = Path(__file__).resolve().parents[2]
    with pytest.raises(RuntimeError, match="extracted from execution_sha"):
        bootstrap_v3._run_parent(
            [
                "--git-root",
                str(repository),
                "--execution-sha",
                "0" * 40,
                "--expected-tree-sha",
                "0" * 40,
                "--expected-spec-sha256",
                "0" * 64,
            ]
        )


def test_parent_bootstrap_rejects_checkout_subdirectory_as_git_root() -> None:
    repository = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="top-level"):
        bootstrap_v3._run_parent(
            [
                "--git-root",
                str(repository / "backend"),
                "--execution-sha",
                "0" * 40,
                "--expected-tree-sha",
                "0" * 40,
                "--expected-spec-sha256",
                "0" * 64,
            ]
        )


def test_external_bootstrap_must_match_reviewed_blob(tmp_path: Path) -> None:
    bootstrap = tmp_path / "bootstrap_v3.py"
    bootstrap.write_text("MUTATED = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not match execution_sha"):
        bootstrap_v3._validate_external_bootstrap(bootstrap, "0" * 40)


def test_external_dependency_roots_cannot_come_from_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkout"
    site_packages = checkout / ".venv" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    monkeypatch.setattr(
        bootstrap_v3.sysconfig,
        "get_paths",
        lambda: {"purelib": str(site_packages), "platlib": str(site_packages)},
    )

    with pytest.raises(RuntimeError, match="mutable checkout"):
        bootstrap_v3._external_roots(checkout)


def test_worker_rejects_forwarded_execution_identity_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not match authenticated"):
        bootstrap_v3._run_worker(
            "worker-arm",
            [
                "--source-root",
                str(tmp_path),
                "--git-root",
                str(tmp_path),
                "--execution-sha",
                "1" * 40,
                "--expected-tree-sha",
                "2" * 40,
                "--expected-spec-sha256",
                "3" * 64,
                "--",
                "--execution-sha",
                "4" * 40,
            ],
        )


def test_worker_rejects_checkout_subdirectory_as_git_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    nested = repository / "nested"
    nested.mkdir(parents=True)
    _git(repository, "init")

    with pytest.raises(ValueError, match="top-level"):
        bootstrap_v3._run_worker(
            "worker-arm",
            [
                "--source-root",
                str(tmp_path),
                "--git-root",
                str(nested),
                "--execution-sha",
                "1" * 40,
                "--expected-tree-sha",
                "2" * 40,
                "--expected-spec-sha256",
                "3" * 64,
                "--",
                "--execution-sha",
                "1" * 40,
            ],
        )
