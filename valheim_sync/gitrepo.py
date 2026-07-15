"""Capa fina sobre el comando git.

El repo vive en %APPDATA%/ValheimSync/repo y actua de intermediario entre la
carpeta de Valheim y GitHub. Nunca convertimos ficheros a git en la carpeta de
Valheim directamente: copiamos.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Sin esto Git en Windows haria conversion CRLF sobre los .db y los corromperia.
GITATTRIBUTES = "*.db binary\n*.fwl binary\n*.db -text\n*.fwl -text\n"

CREATE_NO_WINDOW = 0x08000000


class GitError(RuntimeError):
    """Un comando git fallo."""


class PushRejected(GitError):
    """El remoto ya tenia commits nuestros: alguien se nos adelanto."""


@dataclass
class GitResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out + "\n" + self.err).strip()


def git_available() -> bool:
    try:
        return _run_raw(["git", "--version"], cwd=None).ok
    except (OSError, subprocess.SubprocessError):
        return False


def _run_raw(args: list[str], cwd: Path | None, timeout: int = 300) -> GitResult:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    return GitResult(code=proc.returncode, out=proc.stdout or "", err=proc.stderr or "")


class Repo:
    def __init__(self, path: Path):
        self.path = path

    # ---------------------------------------------------------------- estado

    @property
    def exists(self) -> bool:
        return (self.path / ".git").is_dir()

    def run(self, *args: str, check: bool = True, timeout: int = 300) -> GitResult:
        # -c protege contra configuraciones globales que rompan los binarios o
        # abran un pager interactivo que colgaria la ventana.
        base = [
            "git",
            "-c", "core.autocrlf=false",
            "-c", "core.safecrlf=false",
            "-c", "core.longpaths=true",
            "-c", "core.pager=cat",
        ]
        result = _run_raw(base + list(args), cwd=self.path if self.exists else None, timeout=timeout)
        if check and not result.ok:
            raise GitError(result.text or f"git {' '.join(args)} fallo (codigo {result.code})")
        return result

    @property
    def branch(self) -> str:
        result = self.run("rev-parse", "--abbrev-ref", "HEAD", check=False)
        name = result.out.strip()
        return name if result.ok and name and name != "HEAD" else "main"

    def has_commits(self) -> bool:
        return self.run("rev-parse", "--verify", "HEAD", check=False).ok

    # -------------------------------------------------------------- montaje

    def clone(self, url: str, timeout: int = 900) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        result = _run_raw(
            ["git", "-c", "core.autocrlf=false", "clone", url, str(self.path)],
            cwd=self.path.parent,
            timeout=timeout,
        )
        if not result.ok:
            raise GitError(result.text or "No se pudo clonar el repositorio.")
        self.ensure_scaffold()

    def init_new(self, url: str) -> None:
        """Repo local nuevo apuntando a un remoto vacio."""
        self.path.mkdir(parents=True, exist_ok=True)
        _run_raw(["git", "init", "-b", "main"], cwd=self.path)
        self.run("remote", "add", "origin", url, check=False)
        self.ensure_scaffold()

    def ensure_scaffold(self) -> None:
        """.gitattributes es obligatorio: sin el, git corrompe los .db en Windows."""
        attrs = self.path / ".gitattributes"
        current = attrs.read_text(encoding="utf-8") if attrs.is_file() else ""
        if current != GITATTRIBUTES:
            attrs.write_text(GITATTRIBUTES, encoding="utf-8")
        (self.path / "worlds").mkdir(parents=True, exist_ok=True)

    def remote_url(self) -> str:
        return self.run("remote", "get-url", "origin", check=False).out.strip()

    def set_remote(self, url: str) -> None:
        if self.run("remote", "get-url", "origin", check=False).ok:
            self.run("remote", "set-url", "origin", url)
        else:
            self.run("remote", "add", "origin", url)

    # ------------------------------------------------------------ sincronia

    def fetch(self) -> None:
        self.run("fetch", "origin", "--prune", timeout=600)

    def sync_down(self) -> None:
        """Alinea el repo local con el remoto, descartando cambios locales.

        Es seguro descartar: la carpeta del repo es un espejo desechable, y todo
        lo del jugador vive en la carpeta de Valheim (con backup aparte).
        """
        self.fetch()
        branch = self.branch
        if self.run("rev-parse", "--verify", f"origin/{branch}", check=False).ok:
            self.run("reset", "--hard", f"origin/{branch}")
            self.run("clean", "-fd", check=False)
        self.ensure_scaffold()

    def commit_all(self, message: str, author_name: str) -> bool:
        """Commitea todo lo pendiente. False si no habia nada que commitear."""
        self.run("add", "-A")
        if not self.run("diff", "--cached", "--quiet", check=False).ok:
            self.run(
                "-c", f"user.name={author_name or 'ValheimSync'}",
                "-c", "user.email=valheimsync@localhost",
                "commit", "-m", message,
            )
            return True
        return False

    def push(self, timeout: int = 900) -> None:
        branch = self.branch
        result = self.run("push", "-u", "origin", branch, check=False, timeout=timeout)
        if result.ok:
            return
        blob = result.text.lower()
        if "rejected" in blob or "non-fast-forward" in blob or "fetch first" in blob:
            raise PushRejected(result.text)
        raise GitError(result.text or "No se pudo subir al remoto.")

    def repo_size_mb(self) -> float:
        result = self.run("count-objects", "-v", check=False)
        for line in result.out.splitlines():
            if line.startswith("size-pack:"):
                return int(line.split(":")[1].strip()) / 1024
        return 0.0
