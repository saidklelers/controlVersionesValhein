"""Enumera los mundos de una carpeta de guardado de Valheim.

Un mundo son dos archivos:
  <Nombre>.fwl  -> metadata + semilla (pequenyo, define que el mundo existe)
  <Nombre>.db   -> el mundo en si (binario, de 3 a 100+ MB)

Valheim ademas genera copias automaticas que NO queremos sincronizar:
  <Nombre>_backup_auto-20251231122946.db
  <Nombre>_backup_20250925-164059.db
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Coincide con el sufijo que Valheim anyade a sus copias automaticas.
BACKUP_SUFFIX = re.compile(r"_backup_(auto-)?\d", re.IGNORECASE)


def is_backup(stem: str) -> bool:
    return bool(BACKUP_SUFFIX.search(stem))


@dataclass
class World:
    name: str
    folder: Path

    @property
    def fwl(self) -> Path:
        return self.folder / f"{self.name}.fwl"

    @property
    def db(self) -> Path:
        return self.folder / f"{self.name}.db"

    @property
    def files(self) -> list[Path]:
        """Los archivos que existen de verdad, listos para copiar."""
        return [p for p in (self.fwl, self.db) if p.is_file()]

    @property
    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.files)

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def modified(self) -> datetime | None:
        stamps = [p.stat().st_mtime for p in self.files]
        return datetime.fromtimestamp(max(stamps)) if stamps else None

    @property
    def has_data(self) -> bool:
        """Un .fwl sin .db es un mundo creado pero nunca jugado."""
        return self.db.is_file()


def list_worlds(folder: Path) -> list[World]:
    """Todos los mundos reales de la carpeta, sin copias automaticas."""
    if not folder.is_dir():
        return []

    names = {
        p.stem
        for p in folder.glob("*.fwl")
        if not is_backup(p.stem)
    }
    worlds = [World(name=n, folder=folder) for n in names]
    return sorted(worlds, key=lambda w: w.name.lower())


def find_world(folder: Path, name: str) -> World | None:
    world = World(name=name, folder=folder)
    return world if world.fwl.is_file() else None


def copy_world_files(world: World, dest_folder: Path) -> list[Path]:
    """Copia .fwl/.db a dest_folder conservando el nombre. Devuelve lo copiado."""
    dest_folder.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for src in world.files:
        dst = dest_folder / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def backup_world(world: World, backup_root: Path) -> Path | None:
    """Guarda una copia de seguridad local antes de sobrescribir. None si no habia nada."""
    if not world.files:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_root / world.name / stamp
    copy_world_files(world, dest)
    return dest
