"""Configuracion persistente de ValheimSync (%APPDATA%/ValheimSync)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


# Viene rellenado para que los amigos solo tengan que poner su nombre y darle a
# Guardar. Se puede cambiar desde la ventana de Configuración.
DEFAULT_REPO_URL = "https://github.com/saidklelers/mundos.git"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "ValheimSync"


CONFIG_FILE = app_dir() / "config.json"
REPO_DIR = app_dir() / "repo"
BACKUP_DIR = app_dir() / "backups"


@dataclass
class Config:
    player_name: str = ""
    repo_url: str = ""
    save_folder: str = ""

    @property
    def is_ready(self) -> bool:
        return bool(self.player_name and self.repo_url and self.save_folder)


def load() -> Config:
    if not CONFIG_FILE.is_file():
        return Config()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config()
    known = {f for f in Config.__dataclass_fields__}
    return Config(**{k: v for k, v in data.items() if k in known})


def save(config: Config) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8"
    )
