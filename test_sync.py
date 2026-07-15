"""Prueba end-to-end: dos jugadores compartiendo un mundo por un repo git.

Monta un repo bare local que hace de GitHub, y simula el ciclo completo entre
dos jugadores: subir, bajar, turnos, conflicto y backups. Usa un mundo real de
tu PC (solo lo LEE, nunca lo toca) para comprobar que git no corrompe el .db.

    python test_sync.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from valheim_sync import config, paths, sync, worlds
from valheim_sync.sync import Status, SyncError, SyncManager

SCRATCH = Path(__file__).parent / ".test-tmp"

ok = lambda m: print(f"  [OK] {m}")


def fail(m):
    print(f"  [FALLO] {m}")
    sys.exit(1)


def main() -> None:
    location = paths.default_save_location()
    if not location:
        fail("No encuentro ninguna carpeta de Valheim con mundos en este PC.")
    sample = next((w for w in worlds.list_worlds(location.path) if w.has_data), None)
    if not sample:
        fail(f"No hay ningun mundo con .db en {location.path}")

    if SCRATCH.exists():
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(SCRATCH)], capture_output=True)
    SCRATCH.mkdir(parents=True)

    origin = SCRATCH / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True)

    W = sample.name

    def player(name):
        home = SCRATCH / name
        (home / "valheim").mkdir(parents=True)
        cfg = config.Config(player_name=name, repo_url=str(origin),
                            save_folder=str(home / "valheim"))
        return SyncManager(cfg, work_dir=home / "work"), home

    A, homeA = player("said")
    B, homeB = player("juan")

    for f in sample.files:
        shutil.copy2(f, homeA / "valheim" / f.name)
    sha0 = sync.sha256_of(homeA / "valheim" / f"{W}.db")
    mb = (homeA / "valheim" / f"{W}.db").stat().st_size / 1024 / 1024
    print(f"Mundo de prueba: {W}  ({mb:.2f} MB)\n")

    status_of = lambda m: {i.name: i for i in m.world_infos()}[W].status

    print("1. said conecta y ve su mundo sin subir")
    A.connect()
    status_of(A) is Status.LOCAL_ONLY or fail(f"esperaba LOCAL_ONLY, hay {status_of(A)}")
    ok("solo local")

    print("2. said toma el turno y sube")
    A.claim(W)
    A.upload(W, "primera subida")
    status_of(A) is Status.IN_SYNC or fail(f"esperaba IN_SYNC, hay {status_of(A)}")
    ok("subido y al dia, turno liberado solo")

    print("3. juan conecta y baja")
    B.connect()
    status_of(B) is Status.REMOTE_ONLY or fail(f"esperaba REMOTE_ONLY, hay {status_of(B)}")
    B.claim(W)
    B.download(W)
    sha_b = sync.sha256_of(homeB / "valheim" / f"{W}.db")
    sha_b == sha0 or fail(f"el .db se corrompio: {sha_b[:16]} != {sha0[:16]}")
    ok(f"integridad byte a byte intacta ({mb:.1f} MB, sha={sha_b[:12]})")

    print("4. said no puede pisar el turno de juan")
    for action, args in ((A.claim, (W,)), (A.upload, (W, "pisando"))):
        try:
            action(*args)
            fail(f"{action.__name__} deberia haber sido bloqueado")
        except SyncError:
            pass
    ok("claim y upload bloqueados mientras juan tiene el turno")

    print("5. juan juega y sube")
    with open(homeB / "valheim" / f"{W}.db", "ab") as fh:
        fh.write(b"\x00\xff" * 5000)
    status_of(B) is Status.LOCAL_NEWER or fail(f"esperaba LOCAL_NEWER, hay {status_of(B)}")
    sha_b2 = sync.sha256_of(homeB / "valheim" / f"{W}.db")
    B.upload(W, "mate a Bonemass")
    ok("juan subio su partida")

    print("6. said ve la version nueva y la baja")
    A.refresh()
    status_of(A) is Status.REMOTE_NEWER or fail(f"esperaba REMOTE_NEWER, hay {status_of(A)}")
    A.download(W)
    sync.sha256_of(homeA / "valheim" / f"{W}.db") == sha_b2 or fail("said no recibio lo de juan")
    status_of(A) is Status.IN_SYNC or fail(f"esperaba IN_SYNC, hay {status_of(A)}")
    ok("said tiene la partida de juan, los dos al dia")

    print("7. se hizo backup antes de sobrescribir")
    saved = list((homeA / "work" / "backups" / W).glob(f"*/{W}.db"))
    saved or fail("no se respaldo la version previa de said")
    sync.sha256_of(saved[0]) == sha0 or fail("el backup no es la version previa")
    ok(f"copia de seguridad en backups/{W}/{saved[0].parent.name}/")

    print("8. conflicto: los dos juegan a la vez")
    for home in (homeA, homeB):
        with open(home / "valheim" / f"{W}.db", "ab") as fh:
            fh.write(os.urandom(1000))
    B.claim(W)
    B.upload(W, "juan subio primero")
    A.refresh()
    status_of(A) is Status.DIVERGED or fail(f"esperaba DIVERGED, hay {status_of(A)}")
    ok("conflicto detectado (said jugo Y juan subio)")

    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(SCRATCH)], capture_output=True)
    print(f"\n{'=' * 52}\nTODO OK — el ciclo completo funciona\n{'=' * 52}")


if __name__ == "__main__":
    main()
