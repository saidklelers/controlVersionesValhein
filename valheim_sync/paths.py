"""Localiza las carpetas donde Valheim guarda los mundos.

Valheim guarda en dos sitios segun si Steam Cloud esta activo:
  - Local:       %USERPROFILE%/AppData/LocalLow/IronGate/Valheim/worlds_local
  - Steam Cloud: <Steam>/userdata/<steamid>/892970/remote/worlds[_local]

Un usuario puede tener varias cuentas de Steam en el mismo PC, asi que
devolvemos todas las carpetas que existan y contengan mundos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALHEIM_APPID = "892970"


@dataclass(frozen=True)
class SaveLocation:
    """Una carpeta de Valheim que contiene mundos."""

    path: Path
    kind: str  # "steam_cloud" | "local"
    account: str = ""  # steamid, solo para steam_cloud

    @property
    def label(self) -> str:
        if self.kind == "steam_cloud":
            return f"Steam Cloud ({self.account}) — {self.path.name}"
        return f"Local — {self.path.name}"


def _steam_install_dirs() -> list[Path]:
    dirs: list[Path] = []

    try:
        import winreg

        for hive, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for value in ("SteamPath", "InstallPath"):
                        try:
                            dirs.append(Path(winreg.QueryValueEx(k, value)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass  # no estamos en Windows

    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Steam")

    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        try:
            resolved = d.resolve()
        except OSError:
            continue
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _has_worlds(folder: Path) -> bool:
    try:
        return any(folder.glob("*.fwl"))
    except OSError:
        return False


def find_save_locations(include_empty: bool = False) -> list[SaveLocation]:
    """Devuelve todas las carpetas de mundos de Valheim del equipo."""
    found: list[SaveLocation] = []

    for steam in _steam_install_dirs():
        userdata = steam / "userdata"
        if not userdata.is_dir():
            continue
        try:
            accounts = [d for d in userdata.iterdir() if d.is_dir()]
        except OSError:
            continue
        for account in accounts:
            remote = account / VALHEIM_APPID / "remote"
            for name in ("worlds_local", "worlds"):
                folder = remote / name
                if folder.is_dir() and (include_empty or _has_worlds(folder)):
                    found.append(
                        SaveLocation(path=folder, kind="steam_cloud", account=account.name)
                    )

    local_root = Path(os.path.expanduser("~")) / "AppData/LocalLow/IronGate/Valheim"
    for name in ("worlds_local", "worlds"):
        folder = local_root / name
        if folder.is_dir() and (include_empty or _has_worlds(folder)):
            found.append(SaveLocation(path=folder, kind="local"))

    return found


def default_save_location() -> SaveLocation | None:
    """La carpeta con mas mundos — normalmente la que el jugador usa de verdad."""
    locations = find_save_locations()
    if not locations:
        return None
    return max(locations, key=lambda loc: len(list(loc.path.glob("*.fwl"))))
