"""La ventana de ValheimSync (CustomTkinter).

Toda operacion de git corre en un hilo aparte: un push de 40 MB tarda y la
ventana no puede congelarse. El hilo nunca toca widgets directamente, siempre
vuelve al hilo de Tk con self.after(...).
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import config, paths, worlds
from .gitrepo import git_available
from .sync import Status, SyncError, SyncManager, WorldInfo, valheim_is_running

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG = "#1a1d21"
CARD = "#242830"
TEXT_DIM = "#8b93a1"

# Con git normal cada version de un mundo pesa lo que pesa el .db. A partir de
# aqui avisamos: GitHub empieza a quejarse cerca del giga.
REPO_WARN_MB = 700

STATUS_STYLE: dict[Status, tuple[str, str]] = {
    Status.IN_SYNC:     ("#2e7d4f", "Al día"),
    Status.LOCAL_NEWER: ("#b8791a", "Sin subir"),
    Status.REMOTE_NEWER: ("#2563a8", "Versión nueva"),
    Status.LOCAL_ONLY:  ("#4a5160", "Solo tuyo"),
    Status.REMOTE_ONLY: ("#5b4b8a", "En el repo"),
    Status.DIVERGED:    ("#a83232", "CONFLICTO"),
}

HELP = {
    Status.IN_SYNC: "Tu copia y la del repo son idénticas.",
    Status.LOCAL_NEWER: "Jugaste y no has subido. Sube antes de que otro tome el turno.",
    Status.REMOTE_NEWER: "Alguien subió después de tu última sincronización. Baja antes de jugar.",
    Status.LOCAL_ONLY: "Este mundo solo existe en tu PC. Súbelo para compartirlo.",
    Status.REMOTE_ONLY: "Disponible en el repo. Bájalo para jugarlo.",
    Status.DIVERGED: "Tú jugaste Y alguien más subió. Una de las dos partidas se va a perder: "
                     "decidid cuál vale antes de tocar nada.",
}


MANUAL_OPTION = "Escribir otra ruta a mano…"

# Los dos sitios donde Valheim guarda los mundos, segun tenga Steam Cloud o no.
FOLDER_PATHS = (
    "Con Steam Cloud activado:\n"
    "C:\\Program Files (x86)\\Steam\\userdata\\<tu-numero>\\892970\\remote\\worlds\n"
    "\n"
    "Sin Steam Cloud:\n"
    "C:\\Users\\<tu-usuario>\\AppData\\LocalLow\\IronGate\\Valheim\\worlds_local"
)


class Field(ctk.CTkFrame):
    """Etiqueta + descripcion + hueco para el control."""

    def __init__(self, master, title: str, description: str):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=title, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ctk.CTkLabel(
            self, text=description, font=("Segoe UI", 11), text_color=TEXT_DIM,
            wraplength=580, justify="left",
        ).pack(anchor="w", pady=(1, 0))


class SettingsDialog(ctk.CTkToplevel):
    """Primer arranque / cambiar configuracion."""

    def __init__(self, master, cfg: config.Config):
        super().__init__(master)
        self.title("Configuración")
        self.geometry("660x650")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.result: config.Config | None = None

        self.locations = paths.find_save_locations()
        self.path_var = ctk.StringVar(value=cfg.save_folder)

        ctk.CTkLabel(self, text="Configuración", font=("Segoe UI", 22, "bold")).pack(
            pady=(20, 2), padx=26, anchor="w"
        )
        ctk.CTkLabel(
            self, text="Cada jugador rellena esto en su propio PC.",
            font=("Segoe UI", 12), text_color=TEXT_DIM,
        ).pack(padx=26, anchor="w")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=(16, 0))

        # --- nombre
        Field(body, "Tu nombre de jugador",
              "Ponlo distinto al de tus amigos: es lo que verán junto al turno "
              "y en cada subida.").pack(fill="x")
        self.name_entry = ctk.CTkEntry(body, height=34, placeholder_text="said")
        self.name_entry.pack(fill="x", pady=(5, 16))
        self.name_entry.insert(0, cfg.player_name)

        # --- repo
        Field(body, "Repositorio de mundos",
              "La dirección de git donde se guardan las partidas. TODOS los del grupo "
              "tienen que poner exactamente la misma, o no compartiréis nada.").pack(fill="x")
        self.repo_entry = ctk.CTkEntry(body, height=34, placeholder_text=config.DEFAULT_REPO_URL)
        self.repo_entry.pack(fill="x", pady=(5, 16))
        self.repo_entry.insert(0, cfg.repo_url or config.DEFAULT_REPO_URL)

        # --- carpeta
        Field(body, "Carpeta de mundos de Valheim",
              "Elige una de las detectadas, o escribe la ruta a mano si tu Valheim "
              "está en otro sitio (otro disco, Game Pass, servidor…).").pack(fill="x")

        options = [f"{loc.label}  —  {loc.path}" for loc in self.locations] + [MANUAL_OPTION]
        self.folder_menu = ctk.CTkOptionMenu(body, values=options, height=34,
                                             command=self._on_menu)
        self.folder_menu.pack(fill="x", pady=(5, 6))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.path_entry = ctk.CTkEntry(row, height=34, textvariable=self.path_var,
                                       placeholder_text="C:\\...\\Valheim\\worlds_local")
        self.path_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Examinar…", width=104, height=34, fg_color="#3a3f4b",
                      hover_color="#464c5a", command=self._browse).pack(side="left", padx=(7, 0))

        self.folder_status = ctk.CTkLabel(body, text="", font=("Segoe UI", 11),
                                          wraplength=580, justify="left", anchor="w")
        self.folder_status.pack(fill="x", pady=(6, 0))

        ctk.CTkLabel(
            body, text="Dónde suele estar, si tienes que buscarla a mano:",
            font=("Segoe UI", 11), text_color=TEXT_DIM, wraplength=580,
            justify="left", anchor="w",
        ).pack(fill="x", pady=(10, 2))
        ctk.CTkLabel(body, text=FOLDER_PATHS, font=("Consolas", 10), text_color="#6b7280",
                     wraplength=580, justify="left", anchor="w").pack(fill="x")

        # Preselecciona la carpeta guardada, o la mejor detectada si es la 1a vez.
        start = next(
            (o for o, loc in zip(options, self.locations) if str(loc.path) == cfg.save_folder),
            options[0],
        )
        self.folder_menu.set(start)
        if not cfg.save_folder and self.locations:
            self.path_var.set(str(self.locations[0].path))

        self.path_var.trace_add("write", lambda *_: self._check_folder())
        self._check_folder()

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=26, pady=18)
        ctk.CTkButton(bar, text="Cancelar", width=110, height=36, fg_color="#3a3f4b",
                      hover_color="#464c5a", command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bar, text="Guardar", width=130, height=36,
                      command=self._save).pack(side="right")

        self.transient(master)
        self.after(120, self.grab_set)  # sin retardo, Tk a veces falla en Windows

    # ------------------------------------------------------------- carpeta

    def _on_menu(self, choice: str) -> None:
        if choice == MANUAL_OPTION:
            self.path_entry.focus()
            return
        idx = self.folder_menu.cget("values").index(choice)
        self.path_var.set(str(self.locations[idx].path))

    def _browse(self) -> None:
        start = self.path_var.get().strip()
        chosen = filedialog.askdirectory(
            parent=self, title="Elige la carpeta de mundos de Valheim",
            initialdir=start if Path(start).is_dir() else None,
        )
        if chosen:
            self.path_var.set(str(Path(chosen)))
            self.folder_menu.set(MANUAL_OPTION)

    def _check_folder(self) -> tuple[bool, bool]:
        """Valida en vivo. Devuelve (es_usable, tiene_mundos)."""
        raw = self.path_var.get().strip()
        if not raw:
            self.folder_status.configure(text="Elige una carpeta o escribe su ruta.",
                                         text_color="#d97070")
            return False, False

        folder = Path(raw)
        if not folder.is_dir():
            self.folder_status.configure(text="✗ Esa carpeta no existe en este PC.",
                                         text_color="#d97070")
            return False, False

        found = worlds.list_worlds(folder)
        if not found:
            self.folder_status.configure(
                text="⚠ Aquí no hay ningún mundo (.fwl). Vale si aún no has jugado y solo "
                     "vas a bajar el de tus amigos; si no, revisa la ruta.",
                text_color="#e0a94e",
            )
            return True, False

        names = ", ".join(w.name for w in found[:4])
        more = f" y {len(found) - 4} más" if len(found) > 4 else ""
        self.folder_status.configure(
            text=f"✓ {len(found)} mundo{'s' if len(found) != 1 else ''}: {names}{more}",
            text_color="#4ec27f",
        )
        return True, True

    # -------------------------------------------------------------- guardar

    def _save(self) -> None:
        name = self.name_entry.get().strip()
        url = self.repo_entry.get().strip()
        folder = self.path_var.get().strip()

        if not name:
            messagebox.showwarning("Falta algo", "Escribe tu nombre de jugador.", parent=self)
            return
        if not url:
            messagebox.showwarning("Falta algo", "Escribe la dirección del repositorio.", parent=self)
            return
        usable, _ = self._check_folder()
        if not usable:
            messagebox.showwarning(
                "Carpeta incorrecta",
                "Esa carpeta no existe.\n\nUsa Examinar... para elegirla, "
                "o revisa las rutas de ejemplo de abajo.",
                parent=self,
            )
            return

        self.result = config.Config(player_name=name, repo_url=url, save_folder=folder)
        config.save(self.result)
        self.destroy()


class WorldCard(ctk.CTkFrame):
    """Una tarjeta por mundo, con sus acciones."""

    def __init__(self, master, info: WorldInfo, app: "App"):
        super().__init__(master, fg_color=CARD, corner_radius=10)
        self.info = info
        self.app = app

        color, badge = STATUS_STYLE[info.status]

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(13, 0))

        ctk.CTkLabel(top, text=info.name, font=("Segoe UI", 16, "bold")).pack(side="left")
        ctk.CTkLabel(
            top, text=f"  {badge}  ", font=("Segoe UI", 11, "bold"),
            fg_color=color, corner_radius=6, height=22,
        ).pack(side="left", padx=10)

        size = info.local_mb or info.remote_mb
        ctk.CTkLabel(top, text=f"{size:.1f} MB", font=("Segoe UI", 11),
                     text_color=TEXT_DIM).pack(side="right")

        ctk.CTkLabel(
            self, text=HELP[info.status], font=("Segoe UI", 11),
            text_color="#e0a0a0" if info.status is Status.DIVERGED else TEXT_DIM,
            wraplength=620, justify="left",
        ).pack(anchor="w", padx=16, pady=(5, 0))

        # Linea del turno: lo mas importante de la tarjeta.
        if info.locked_by_me:
            turn, turn_color = "Tienes el turno — puedes jugar", "#4ec27f"
        elif info.locked_by_other:
            turn, turn_color = f"Turno de {info.state.lock_holder} — no juegues este mundo", "#e0a94e"
        else:
            turn, turn_color = "Turno libre", TEXT_DIM
        ctk.CTkLabel(self, text=turn, font=("Segoe UI", 12, "bold"),
                     text_color=turn_color).pack(anchor="w", padx=16, pady=(7, 0))

        ctk.CTkLabel(self, text=f"Última subida: {info.last_upload_text}",
                     font=("Segoe UI", 11), text_color=TEXT_DIM).pack(anchor="w", padx=16)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(11, 13))
        self._buttons(bar)

    def _btn(self, bar, text, cmd, *, primary=False, danger=False, enabled=True):
        colors = ("#1f6aa5", "#2d7fc0")
        if danger:
            colors = ("#8f2f2f", "#a83a3a")
        elif not primary:
            colors = ("#3a3f4b", "#464c5a")
        b = ctk.CTkButton(bar, text=text, height=32, width=112, command=cmd,
                          fg_color=colors[0], hover_color=colors[1],
                          state="normal" if enabled else "disabled")
        b.pack(side="left", padx=(0, 7))
        return b

    def _buttons(self, bar) -> None:
        i = self.info
        blocked = i.locked_by_other

        if i.status is Status.REMOTE_ONLY:
            self._btn(bar, "Bajar mundo", lambda: self.app.do_download(i.name), primary=True)
        elif i.status is Status.LOCAL_ONLY:
            self._btn(bar, "Subir al repo", lambda: self.app.do_upload(i.name), primary=True)
        elif i.status is Status.DIVERGED:
            self._btn(bar, "Bajar (pierdo lo mío)", lambda: self.app.do_download(i.name), danger=True)
            self._btn(bar, "Subir (piso lo suyo)", lambda: self.app.do_upload(i.name),
                      danger=True, enabled=not blocked)
        else:
            self._btn(bar, "Bajar", lambda: self.app.do_download(i.name),
                      primary=i.status is Status.REMOTE_NEWER)
            self._btn(bar, "Subir", lambda: self.app.do_upload(i.name),
                      primary=i.status is Status.LOCAL_NEWER, enabled=not blocked)

        if i.locked_by_me:
            self._btn(bar, "Liberar turno", lambda: self.app.do_release(i.name))
        elif blocked:
            self._btn(bar, "Forzar turno", lambda: self.app.do_release(i.name, force=True), danger=True)
        elif i.remote_exists:
            self._btn(bar, "Tomar turno", lambda: self.app.do_claim(i.name))


class UploadDialog(ctk.CTkToplevel):
    """Pide una nota para el commit."""

    def __init__(self, master, world: str):
        super().__init__(master)
        self.title("Subir mundo")
        self.geometry("470x250")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.note: str | None = None
        self.keep_turn = False

        ctk.CTkLabel(self, text=f"Subir {world}", font=("Segoe UI", 18, "bold")).pack(
            pady=(20, 2), padx=22, anchor="w")
        ctk.CTkLabel(self, text="¿Qué hicisteis en esta partida?", font=("Segoe UI", 12),
                     text_color=TEXT_DIM).pack(padx=22, anchor="w")

        self.entry = ctk.CTkEntry(self, height=36, placeholder_text="matamos a Bonemass")
        self.entry.pack(fill="x", padx=22, pady=(10, 8))
        self.entry.bind("<Return>", lambda _e: self._ok())

        self.keep = ctk.CTkCheckBox(self, text="Quedarme el turno (sigo jugando luego)",
                                    font=("Segoe UI", 12))
        self.keep.pack(padx=22, anchor="w", pady=(2, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=18, side="bottom")
        ctk.CTkButton(bar, text="Cancelar", width=100, height=36, fg_color="#3a3f4b",
                      hover_color="#464c5a", command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bar, text="Subir", width=120, height=36, command=self._ok).pack(side="right")

        self.transient(master)
        self.after(120, self.grab_set)
        self.after(160, self.entry.focus)

    def _ok(self) -> None:
        self.note = self.entry.get().strip()
        self.keep_turn = bool(self.keep.get())
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ValheimSync — mundos compartidos")
        self.geometry("780x760")
        self.minsize(700, 520)
        self.configure(fg_color=BG)

        self.cfg = config.load()
        self.manager: SyncManager | None = None
        self.busy = False

        self._build()
        self.after(250, self.boot)

    # ------------------------------------------------------------- estructura

    def _build(self) -> None:
        head = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=74)
        head.pack(fill="x")
        head.pack_propagate(False)

        left = ctk.CTkFrame(head, fg_color="transparent")
        left.pack(side="left", padx=20)
        ctk.CTkLabel(left, text="ValheimSync", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        self.sub = ctk.CTkLabel(left, text="", font=("Segoe UI", 11), text_color=TEXT_DIM)
        self.sub.pack(anchor="w")

        ctk.CTkButton(head, text="Configuración", width=118, height=32, fg_color="#3a3f4b",
                      hover_color="#464c5a", command=self.open_settings).pack(side="right", padx=(6, 18))
        self.refresh_btn = ctk.CTkButton(head, text="Actualizar", width=110, height=32,
                                         command=self.do_refresh)
        self.refresh_btn.pack(side="right")

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=16, pady=(14, 6))

        foot = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=42)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        self.status = ctk.CTkLabel(foot, text="Arrancando...", font=("Segoe UI", 11),
                                   text_color=TEXT_DIM, anchor="w")
        self.status.pack(side="left", padx=18, fill="x", expand=True)
        self.size_label = ctk.CTkLabel(foot, text="", font=("Segoe UI", 11),
                                       text_color=TEXT_DIM, anchor="e")
        self.size_label.pack(side="right", padx=18)

    def say(self, text: str) -> None:
        self.status.configure(text=text)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_btn.configure(state="disabled" if busy else "normal")
        self.configure(cursor="watch" if busy else "")

    # ------------------------------------------------------------- arranque

    def boot(self) -> None:
        if not git_available():
            self.show_message(
                "Falta git",
                "No encuentro git en este equipo.\n\n"
                "Instálalo desde https://git-scm.com y vuelve a abrir la herramienta.",
                error=True,
            )
            self.say("git no esta instalado.")
            return
        if not self.cfg.is_ready:
            self.say("Sin configurar.")
            self.open_settings(first_run=True)
            return
        self.connect()

    def open_settings(self, first_run: bool = False) -> None:
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result:
            self.cfg = dlg.result
            self.connect()
        elif first_run:
            self.say("Configura la herramienta para empezar.")

    def connect(self) -> None:
        self.manager = SyncManager(self.cfg)
        self.sub.configure(text=f"{self.cfg.player_name} · {self.cfg.repo_url}")
        self.run_task("Conectando con el repositorio...", self.manager.connect)

    # --------------------------------------------------------------- tareas

    def run_task(self, busy_text: str, fn, *args, **kwargs) -> None:
        """Ejecuta fn en un hilo y refresca la lista al terminar."""
        if self.busy:
            return
        self.set_busy(True)
        self.say(busy_text)

        def worker():
            try:
                self.after(0, self._done, fn(*args, **kwargs), None)
            except SyncError as e:
                self.after(0, self._done, None, e)
            except Exception as e:  # noqa: BLE001 — cualquier fallo debe verse
                traceback.print_exc()
                self.after(0, self._done, None, e)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, message, error) -> None:
        self.set_busy(False)
        if error is not None:
            self.say("La operación no se completó.")
            self.show_message("No se pudo", str(error), error=True)
        elif message:
            self.say(str(message).splitlines()[0])
            if "\n" in str(message):
                self.show_message("Listo", str(message))
        self.render()

    def show_message(self, title: str, text: str, error: bool = False) -> None:
        (messagebox.showerror if error else messagebox.showinfo)(title, text, parent=self)

    # --------------------------------------------------------------- acciones

    def do_refresh(self) -> None:
        if self.manager:
            self.run_task("Buscando cambios...", self.manager.refresh)

    def do_claim(self, name: str) -> None:
        self.run_task(f"Tomando el turno de {name}...", self.manager.claim, name)

    def do_release(self, name: str, force: bool = False) -> None:
        if force:
            info = self._info(name)
            holder = info.state.lock_holder if info else "otro jugador"
            if not messagebox.askyesno(
                "Forzar turno",
                f"El turno lo tiene {holder}.\n\n"
                "Forzarlo solo tiene sentido si sabes que ya no está jugando "
                "(se le olvidó liberarlo, se le colgó el juego...).\n\n"
                f"Si {holder} está jugando ahora mismo, su partida se perderá.\n\n"
                "¿Forzar de todas formas?",
                parent=self, icon="warning",
            ):
                return
        self.run_task(f"Liberando {name}...", self.manager.release, name, force=force)

    def do_download(self, name: str) -> None:
        info = self._info(name)
        if info and info.status in (Status.LOCAL_NEWER, Status.DIVERGED):
            if not messagebox.askyesno(
                "Vas a perder tu progreso",
                f"Tienes cambios en {name} sin subir.\n\n"
                "Si bajas ahora, tu partida se reemplaza por la del repo.\n"
                "(Se guardará una copia de seguridad por si acaso.)\n\n"
                "¿Bajar igualmente?",
                parent=self, icon="warning",
            ):
                return
        self.run_task(f"Bajando {name}...", self.manager.download, name)

    def do_upload(self, name: str) -> None:
        info = self._info(name)
        if info and info.status is Status.DIVERGED:
            if not messagebox.askyesno(
                "Vas a pisar la partida de otro",
                f"Alguien subió {name} después de tu última sincronización, "
                "y tú también tienes cambios.\n\n"
                "Si subes, el progreso que subió esa persona se pierde.\n\n"
                "¿Seguro que tu versión es la buena?",
                parent=self, icon="warning",
            ):
                return
        dlg = UploadDialog(self, name)
        self.wait_window(dlg)
        if dlg.note is None:
            return
        self.run_task(f"Subiendo {name}...", self.manager.upload, name,
                      note=dlg.note, release_turn=not dlg.keep_turn)

    def _info(self, name: str) -> WorldInfo | None:
        return next((i for i in self._infos if i.name == name), None)

    def _show_repo_size(self) -> None:
        try:
            mb = self.manager.repo.repo_size_mb()
        except Exception:  # noqa: BLE001 — es solo informativo
            return
        if mb <= 0:
            return
        heavy = mb > REPO_WARN_MB
        self.size_label.configure(
            text=f"Repo: {mb:,.0f} MB" + ("  ⚠ conviene limpiar historial" if heavy else ""),
            text_color="#e0a94e" if heavy else TEXT_DIM,
        )

    # ---------------------------------------------------------------- pintado

    _infos: list[WorldInfo] = []

    def render(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        if not self.manager:
            return

        try:
            self._infos = self.manager.world_infos()
        except Exception as e:  # noqa: BLE001
            self.say(f"No pude leer los mundos: {e}")
            return

        if valheim_is_running():
            warn = ctk.CTkFrame(self.list, fg_color="#5c4415", corner_radius=8)
            warn.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(
                warn, text="Valheim está abierto. Ciérralo antes de subir o bajar mundos:\n"
                           "al salir del juego se sobrescribe el .db y perderías lo sincronizado.",
                font=("Segoe UI", 12), justify="left",
            ).pack(padx=14, pady=10, anchor="w")

        if not self._infos:
            ctk.CTkLabel(
                self.list,
                text="No hay mundos ni en tu PC ni en el repositorio.\n\n"
                     "Crea un mundo en Valheim y pulsa Actualizar.",
                font=("Segoe UI", 13), text_color=TEXT_DIM, justify="center",
            ).pack(pady=60)
            return

        self._show_repo_size()

        order = {s: n for n, s in enumerate([
            Status.DIVERGED, Status.REMOTE_NEWER, Status.LOCAL_NEWER,
            Status.REMOTE_ONLY, Status.IN_SYNC, Status.LOCAL_ONLY,
        ])}
        for info in sorted(self._infos, key=lambda i: (order[i.status], i.name.lower())):
            WorldCard(self.list, info, self).pack(fill="x", pady=(0, 10))


def main() -> None:
    App().mainloop()
