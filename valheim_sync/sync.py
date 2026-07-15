"""Logica de sincronizacion: estado de cada mundo, turnos y subida/bajada.

MODELO MENTAL
-------------
Un .db de Valheim es binario y git NO sabe fusionarlo. Si dos personas juegan
el mismo mundo a la vez, una de las dos PIERDE su partida entera; no hay
"merge" posible. Por eso esto no es un git normal, es un sistema de TURNOS:

  1. Tomas el turno   -> nadie mas puede subir ese mundo
  2. Bajas el mundo   -> lo juegas
  3. Subes y liberas  -> le toca a otro

El turno se guarda en state.json DENTRO del repo. Reclamarlo = commit + push.
Como el push de git es atomico en el servidor, si dos personas lo intentan a la
vez el segundo recibe un rechazo y se entera de que llego tarde. El servidor de
git es el arbitro; no hace falta nada mas.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from . import config, worlds
from .gitrepo import PushRejected, Repo
from .worlds import World


class Status(Enum):
    IN_SYNC = "al día"
    LOCAL_ONLY = "solo local"
    REMOTE_ONLY = "solo en el repo"
    LOCAL_NEWER = "tienes cambios sin subir"
    REMOTE_NEWER = "hay versión nueva para bajar"
    DIVERGED = "conflicto"


class SyncError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pretty_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return iso or "?"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Hashear los mundos locales en cada refresco serian ~80 MB de lectura cada vez.
# Cacheamos por (ruta, mtime, tamanyo): si Valheim toca el .db, cambian los dos
# ultimos y el hash se recalcula solo.
_sha_cache: dict[tuple[str, int, int], str] = {}


def sha256_cached(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _sha_cache:
        if len(_sha_cache) > 64:
            _sha_cache.clear()
        _sha_cache[key] = sha256_of(path)
    return _sha_cache[key]


def valheim_is_running() -> bool:
    """Sobrescribir un .db con Valheim abierto = perder la partida al cerrar."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq valheim.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,
        )
        return "valheim.exe" in result.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------- estado


@dataclass
class WorldState:
    """Lo que el repo sabe de un mundo (contenido de state.json)."""

    name: str
    lock_holder: str = ""
    lock_since: str = ""
    last_by: str = ""
    last_at: str = ""
    last_sha: str = ""
    last_note: str = ""

    @property
    def is_locked(self) -> bool:
        return bool(self.lock_holder)

    def to_json(self) -> str:
        data = {
            "world": self.name,
            "lock": (
                {"holder": self.lock_holder, "since": self.lock_since}
                if self.is_locked else None
            ),
            "last_upload": (
                {
                    "by": self.last_by,
                    "at": self.last_at,
                    "sha256": self.last_sha,
                    "note": self.last_note,
                }
                if self.last_sha else None
            ),
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_file(cls, path: Path, name: str) -> "WorldState":
        if not path.is_file():
            return cls(name=name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(name=name)
        lock = data.get("lock") or {}
        last = data.get("last_upload") or {}
        return cls(
            name=data.get("world", name),
            lock_holder=lock.get("holder", "") or "",
            lock_since=lock.get("since", "") or "",
            last_by=last.get("by", "") or "",
            last_at=last.get("at", "") or "",
            last_sha=last.get("sha256", "") or "",
            last_note=last.get("note", "") or "",
        )


@dataclass
class WorldInfo:
    """Vista unificada de un mundo: lo local + lo remoto + el turno."""

    name: str
    status: Status
    state: WorldState
    local: World | None
    remote_exists: bool
    me: str = ""
    local_mb: float = 0.0
    remote_mb: float = 0.0

    @property
    def locked_by_me(self) -> bool:
        return self.state.is_locked and self.me == self.state.lock_holder

    @property
    def locked_by_other(self) -> bool:
        return self.state.is_locked and self.me != self.state.lock_holder

    @property
    def last_upload_text(self) -> str:
        if not self.state.last_sha:
            return "Nunca subido"
        note = f" — {self.state.last_note}" if self.state.last_note else ""
        return f"{self.state.last_by} · {_pretty_time(self.state.last_at)}{note}"


# ------------------------------------------------------------------ el manager


class SyncManager:
    def __init__(self, cfg: config.Config, work_dir: Path | None = None):
        # work_dir permite aislar el estado (util para tests y para correr dos
        # perfiles en el mismo equipo); por defecto es %APPDATA%/ValheimSync.
        base = work_dir or config.app_dir()
        self.cfg = cfg
        self.work_dir = base
        self.repo = Repo(base / "repo")
        self.backup_dir = base / "backups"
        self.ledger_path = base / "ledger.json"

    # ---- carpetas

    @property
    def save_folder(self) -> Path:
        return Path(self.cfg.save_folder)

    def repo_world_dir(self, name: str) -> Path:
        return self.repo.path / "worlds" / name

    def _state_path(self, name: str) -> Path:
        return self.repo_world_dir(name) / "state.json"

    # ---- ledger: que version tenia yo la ultima vez que sincronice

    def _ledger(self) -> dict:
        if not self.ledger_path.is_file():
            return {}
        try:
            return json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _remember(self, name: str, sha: str) -> None:
        data = self._ledger()
        data[name] = {"sha256": sha, "at": _now()}
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---- conexion

    def connect(self) -> str:
        """Clona el repo si hace falta y lo pone al dia. Devuelve un mensaje."""
        if not self.repo.exists:
            if self.repo.path.exists() and any(self.repo.path.iterdir()):
                raise SyncError(
                    f"{self.repo.path} existe pero no es un repo de git.\n"
                    "Borra esa carpeta y vuelve a conectar."
                )
            self.repo.clone(self.cfg.repo_url)
            return "Repositorio clonado."
        if self.repo.remote_url() != self.cfg.repo_url:
            self.repo.set_remote(self.cfg.repo_url)
        self.repo.sync_down()
        return "Repositorio al día."

    def refresh(self) -> None:
        self.repo.sync_down()

    # ---- lectura de estado

    def world_infos(self) -> list[WorldInfo]:
        me = self.cfg.player_name
        local_worlds = {w.name: w for w in worlds.list_worlds(self.save_folder)}

        remote_names: list[str] = []
        repo_worlds = self.repo.path / "worlds"
        if repo_worlds.is_dir():
            remote_names = [d.name for d in repo_worlds.iterdir() if d.is_dir()]

        ledger = self._ledger()
        infos: list[WorldInfo] = []

        for name in sorted(set(local_worlds) | set(remote_names), key=str.lower):
            local = local_worlds.get(name)
            state = WorldState.from_file(self._state_path(name), name)
            remote_db = self.repo_world_dir(name) / f"{name}.db"
            remote_exists = remote_db.is_file()

            local_sha = sha256_cached(local.db) if local and local.has_data else ""
            remote_sha = state.last_sha
            seen_sha = (ledger.get(name) or {}).get("sha256", "")

            if not local and remote_exists:
                status = Status.REMOTE_ONLY
            elif local and not remote_exists:
                status = Status.LOCAL_ONLY
            elif not local and not remote_exists:
                continue
            elif local_sha and local_sha == remote_sha:
                status = Status.IN_SYNC
            elif seen_sha and seen_sha == remote_sha:
                status = Status.LOCAL_NEWER
            elif seen_sha and seen_sha == local_sha:
                status = Status.REMOTE_NEWER
            else:
                status = Status.DIVERGED

            infos.append(WorldInfo(
                name=name,
                status=status,
                state=state,
                local=local,
                remote_exists=remote_exists,
                me=me,
                local_mb=local.size_mb if local else 0.0,
                remote_mb=remote_db.stat().st_size / (1024 * 1024) if remote_exists else 0.0,
            ))

        return infos

    # ---- operaciones

    def _write_state(self, state: WorldState) -> None:
        path = self._state_path(state.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.to_json(), encoding="utf-8")

    def _commit_push(self, message: str) -> None:
        """Commit + push. Si nos rechazan, es que alguien se adelanto."""
        self.repo.commit_all(message, self.cfg.player_name)
        try:
            self.repo.push()
        except PushRejected:
            self.repo.sync_down()
            raise SyncError(
                "Alguien subió cambios antes que tú, así que tu acción se canceló.\n"
                "La lista ya se actualizó: mira quién tiene el turno e inténtalo de nuevo."
            ) from None

    def claim(self, name: str) -> str:
        """Toma el turno de un mundo."""
        self.repo.sync_down()
        state = WorldState.from_file(self._state_path(name), name)
        me = self.cfg.player_name

        if state.is_locked and state.lock_holder != me:
            raise SyncError(
                f"El turno lo tiene {state.lock_holder} desde "
                f"{_pretty_time(state.lock_since)}.\n"
                "Espera a que lo libere o pídeselo."
            )
        if state.is_locked and state.lock_holder == me:
            return f"Ya tenías el turno de {name}."

        state.lock_holder = me
        state.lock_since = _now()
        self._write_state(state)
        self._commit_push(f"{me} toma el turno de {name}")
        return f"Turno de {name} tomado. Ya puedes jugar."

    def release(self, name: str, force: bool = False) -> str:
        """Libera el turno sin subir nada."""
        self.repo.sync_down()
        state = WorldState.from_file(self._state_path(name), name)
        me = self.cfg.player_name

        if not state.is_locked:
            return f"{name} ya estaba libre."
        if state.lock_holder != me and not force:
            raise SyncError(f"El turno es de {state.lock_holder}, no tuyo.")

        who = state.lock_holder
        state.lock_holder = ""
        state.lock_since = ""
        self._write_state(state)
        verb = f"{me} libera a la fuerza" if who != me else f"{me} libera"
        self._commit_push(f"{verb} el turno de {name}")
        return f"Turno de {name} liberado."

    def download(self, name: str) -> str:
        """Trae el mundo del repo a Valheim, respaldando lo local antes."""
        if valheim_is_running():
            raise SyncError(
                "Valheim está abierto. Cierra el juego antes de bajar un mundo,\n"
                "o al cerrarlo sobrescribirá lo que acabas de bajar."
            )
        self.repo.sync_down()
        src_dir = self.repo_world_dir(name)
        if not (src_dir / f"{name}.db").is_file():
            raise SyncError(f"{name} no esta en el repositorio.")

        backup_note = ""
        existing = worlds.find_world(self.save_folder, name)
        if existing and existing.files:
            dest = worlds.backup_world(existing, self.backup_dir)
            backup_note = f"\nTu version anterior quedo guardada en:\n{dest}"

        self.save_folder.mkdir(parents=True, exist_ok=True)
        copied = 0
        for ext in ("fwl", "db"):
            src = src_dir / f"{name}.{ext}"
            if src.is_file():
                shutil.copy2(src, self.save_folder / f"{name}.{ext}")
                copied += 1
        if not copied:
            raise SyncError(f"No habia archivos de {name} que copiar.")

        state = WorldState.from_file(self._state_path(name), name)
        self._remember(name, state.last_sha or sha256_of(self.save_folder / f"{name}.db"))
        return f"{name} bajado a tu Valheim.{backup_note}"

    def upload(self, name: str, note: str = "", release_turn: bool = True) -> str:
        """Sube el mundo local al repo y (por defecto) libera el turno."""
        if valheim_is_running():
            raise SyncError(
                "Valheim está abierto. Cierra el juego para que guarde el mundo\n"
                "del todo antes de subirlo, si no subirás una partida incompleta."
            )
        self.repo.sync_down()
        local = worlds.find_world(self.save_folder, name)
        if not local or not local.has_data:
            raise SyncError(f"No encuentro {name}.db en tu carpeta de Valheim.")

        state = WorldState.from_file(self._state_path(name), name)
        me = self.cfg.player_name
        if state.is_locked and state.lock_holder != me:
            raise SyncError(
                f"No puedes subir: el turno lo tiene {state.lock_holder}.\n"
                "Si subes ahora borrarías su partida."
            )

        worlds.copy_world_files(local, self.repo_world_dir(name))

        state.last_by = me
        state.last_at = _now()
        state.last_sha = sha256_of(local.db)
        state.last_note = note.strip()
        if release_turn:
            state.lock_holder = ""
            state.lock_since = ""
        self._write_state(state)

        subject = f"{name}: {note.strip()}" if note.strip() else f"{name} actualizado"
        self._commit_push(f"{subject} ({me})")
        self._remember(name, state.last_sha)

        tail = " Turno liberado." if release_turn else " Sigues teniendo el turno."
        return f"{name} subido ({local.size_mb:.1f} MB).{tail}"
