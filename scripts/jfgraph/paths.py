"""Where JobFinderOS reads and writes, and the guard that keeps a run inside its lane.

Two problems this module solves:

* Every script used to hardcode ``Path.home() / ".jobfinderos"`` for state. A simulation
  must never touch the real state dir, so ``state_dir()`` honors ``JOBFINDEROS_STATE_DIR``.
* A test harness once deleted the real state dir because a command ran outside its
  sandbox. ``Paths.assert_writable`` refuses any write outside the allowed roots: in sim
  mode that is the scratch dir and nothing else; in live mode it is the vault, logs,
  config, and state dirs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

ENV_STATE_DIR = "JOBFINDEROS_STATE_DIR"
DEFAULT_STATE_DIRNAME = ".jobfinderos"


class WriteOutsideAllowedRoots(RuntimeError):
    """Raised when code tries to write somewhere this run is not allowed to."""


def project_root() -> Path:
    """The repo root (two levels above this file: scripts/jfgraph/paths.py)."""
    return Path(__file__).resolve().parents[2]


def state_dir(create: bool = True) -> Path:
    """The per-user state dir: ``$JOBFINDEROS_STATE_DIR`` or ``~/.jobfinderos``."""
    raw = os.environ.get(ENV_STATE_DIR, "").strip()
    d = Path(raw).expanduser() if raw else Path.home() / DEFAULT_STATE_DIRNAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class Paths:
    """Resolved locations for one run. Build with ``Paths.live()`` or ``Paths.sim(scratch)``."""

    root: Path          # repo root; read-only source of skills, templates, scripts
    config_dir: Path
    vault_dir: Path
    logs_dir: Path
    state_dir: Path
    scratch: Path | None = None   # set in sim mode; the ONLY writable root then

    @classmethod
    def live(cls) -> "Paths":
        root = project_root()
        return cls(
            root=root,
            config_dir=root / "config",
            vault_dir=root / "vault",
            logs_dir=root / "logs",
            state_dir=state_dir(),
        )

    @classmethod
    def sim(cls, scratch: Path | str) -> "Paths":
        """Everything writable lives under ``scratch``; the repo is only read."""
        scratch = Path(scratch).expanduser().resolve()
        return cls(
            root=project_root(),
            config_dir=scratch / "config",
            vault_dir=scratch / "vault",
            logs_dir=scratch / "logs",
            state_dir=scratch / "state",
            scratch=scratch,
        )

    # ------------------------------------------------------------------ guard

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "llm-cache"

    def allowed_write_roots(self) -> tuple[Path, ...]:
        if self.scratch is not None:
            return (self.scratch,)
        return (self.vault_dir, self.logs_dir, self.config_dir, self.state_dir)

    def assert_writable(self, path: Path | str) -> Path:
        """Return the resolved path, or raise if it is outside the allowed write roots.

        Resolves the deepest existing ancestor so a not-yet-created file under an
        allowed root still passes, and a symlink pointing out of the sandbox fails.
        """
        p = Path(path).expanduser()
        probe = p if p.is_absolute() else Path.cwd() / p
        existing = probe
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        resolved = existing.resolve() / probe.relative_to(existing)
        for root in self.allowed_write_roots():
            if _is_under(resolved, root.resolve()):
                return resolved
        mode = "sim" if self.scratch is not None else "live"
        raise WriteOutsideAllowedRoots(
            f"{mode} run refused to write {resolved}; allowed roots: "
            + ", ".join(str(r) for r in self.allowed_write_roots())
        )

    def open_write(self, path: Path | str, mode: str = "w", **kwargs: Any) -> IO:
        """``open()`` for writing that goes through the guard and creates parent dirs."""
        if not any(flag in mode for flag in ("w", "a", "x", "+")):
            raise ValueError(f"open_write needs a writing mode, got {mode!r}")
        resolved = self.assert_writable(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if "b" not in mode:
            kwargs.setdefault("encoding", "utf-8")
        return open(resolved, mode, **kwargs)

    def unlink(self, path: Path | str) -> None:
        """Remove a file or symlink that lives inside an allowed root, without following it.

        ``assert_writable`` resolves through symlinks (a write through a link that points out
        of the sandbox must fail), so removing the link itself is validated on its parent.
        """
        p = Path(path).expanduser()
        if not p.is_symlink() and not p.exists():
            return
        self.assert_writable(p.parent)
        if p.is_symlink() or p.is_file():
            p.unlink()
        else:
            raise WriteOutsideAllowedRoots(f"unlink refuses a directory: {p}; remove its contents through the guard instead")

    def mkdir(self, path: Path | str) -> Path:
        resolved = self.assert_writable(path)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def ensure_layout(self) -> None:
        for d in (self.config_dir, self.vault_dir, self.logs_dir, self.state_dir):
            self.mkdir(d)
