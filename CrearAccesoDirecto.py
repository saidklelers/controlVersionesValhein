"""Crea el acceso directo de ValheimSync en el Escritorio y el menu Inicio.

Ejecutalo una vez despues de instalar (o si actualizas Python y deja de abrir).

    python CrearAccesoDirecto.py

El acceso apunta a pythonw.exe (no a python.exe) para que no salga la ventana
negra de consola detras de la app.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "ValheimSync.py"
ICON = HERE / "assets" / "valheimsync.ico"
NAME = "ValheimSync"
DESCRIPTION = "Comparte tus mundos de Valheim con tus amigos"


def _is_real_exe(path: Path) -> bool:
    """Descarta los alias de la Microsoft Store.

    El pythonw.exe de WindowsApps es un stub de 0 bytes: funciona si lo llamas
    desde una consola (que resuelve el alias), pero un acceso directo lo invoca
    en directo y no arranca nada, sin dar ni un error.
    """
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _launcher_pythons() -> list[Path]:
    """Los python.exe que conoce el lanzador oficial (py -0p)."""
    try:
        result = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                                timeout=30, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[Path] = []
    for line in result.stdout.splitlines():
        # Formato:  -V:3.12 *        C:\...\Python312\python.exe
        idx = line.lower().find(":\\")
        if idx > 0:
            found.append(Path(line[idx - 1:].strip()))
    return found


def _candidates() -> list[Path]:
    """Todos los pythonw.exe utilizables del equipo, del mejor al peor."""
    seen: set[Path] = set()
    out: list[Path] = []

    def add(exe: Path) -> None:
        if _is_real_exe(exe) and exe not in seen:
            seen.add(exe)
            out.append(exe)

    for python in _launcher_pythons():
        add(python.with_name("pythonw.exe"))
    # base_prefix da el pythonw de verdad aunque Python venga de la Store
    # (sys.executable seria el alias vacio de WindowsApps).
    add(Path(sys.base_prefix) / "pythonw.exe")
    add(Path(sys.executable).with_name("pythonw.exe"))

    # Las rutas de la Store llevan la version incrustada (…Python.3.10_3.10.3056.0…)
    # y se rompen en cuanto la Store actualice: dejarlas para el final.
    out.sort(key=lambda p: "WindowsApps" in str(p))
    return out


def find_launcher() -> Path | None:
    """El primer pythonw que de verdad pueda abrir la app.

    No basta con que exista y arranque: tiene que ser un Python que tenga
    customtkinter. Si nos equivocamos, la app moriria con un
    ModuleNotFoundError invisible, porque sin consola no se ve ningun error.

    Se prueba lanzando el propio pythonw con el import de verdad: es lo unico
    que demuestra que ese binario abrira la app. (pyw.exe, el lanzador, da
    falsos positivos: responde bien a -c pero luego no consigue arrancar el
    pythonw de la Store.)
    """
    for exe in _candidates():
        try:
            probe = subprocess.run(
                [str(exe), "-c", "import customtkinter"],
                capture_output=True, timeout=90, creationflags=0x08000000,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return exe
        print(f"  descartado: {exe}\n              (ese Python no tiene customtkinter)")
    return None


def make_shortcuts(exe: Path) -> list[str]:
    """Crea los .lnk via WScript.Shell. Devuelve las rutas creadas."""
    arguments = f'"{SCRIPT}"'
    # SpecialFolders resuelve bien el Escritorio aunque este en OneDrive.
    ps = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$targets = @($shell.SpecialFolders('Desktop'), $shell.SpecialFolders('Programs'))
foreach ($dir in $targets) {{
    if (-not $dir) {{ continue }}
    $path = Join-Path $dir '{NAME}.lnk'
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = '{exe}'
    $lnk.Arguments = '{arguments}'
    $lnk.WorkingDirectory = '{HERE}'
    $lnk.IconLocation = '{ICON}'
    $lnk.Description = '{DESCRIPTION}'
    $lnk.Save()
    Write-Output $path
}}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(ps)
        tmp = fh.name
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", tmp],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> int:
    print("Creando accesos directos de ValheimSync...\n")

    if not SCRIPT.is_file():
        print(f"ERROR: no encuentro {SCRIPT}")
        return 1
    if not ICON.is_file():
        print(f"AVISO: falta el icono ({ICON}), se usara el generico de Python.\n")

    print("Buscando un Python que pueda abrir la app...")
    exe = find_launcher()
    if not exe:
        print(
            "\nERROR: ningun Python del equipo puede abrir ValheimSync.\n\n"
            "Faltan las dependencias. Instalalas con:\n"
            "    python -m pip install -r requirements.txt\n\n"
            "y vuelve a ejecutar esto."
        )
        return 1
    print(f"  usando: {exe}")

    try:
        created = make_shortcuts(exe)
    except Exception as e:  # noqa: BLE001
        print(f"\nERROR al crear el acceso directo:\n{e}")
        return 1

    if not created:
        print("\nERROR: no se pudo crear ningun acceso directo.")
        return 1

    print("\nListo. Accesos creados:")
    for path in created:
        print(f"  {path}")
    print("\nYa puedes abrir ValheimSync desde el Escritorio,\n"
          "o escribiendo 'ValheimSync' en el menu Inicio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
