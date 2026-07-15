# ValheimSync

Control de versiones para mundos de Valheim. Sube tu mundo a un repositorio de git,
tus amigos se lo bajan con la misma herramienta, juegan, y suben los cambios.
Cada uno juega en su PC, sin servidor dedicado ni depender de que tú tengas Steam abierto.

## La idea en 30 segundos

Un mundo de Valheim son dos archivos: `MiMundo.db` (el mundo) y `MiMundo.fwl` (la semilla).
Están en tu PC y Steam solo los comparte mientras tú tengas el juego abierto.
ValheimSync los mete en un repo de git, así que cualquiera puede bajarlos y seguir la partida.

**El problema:** el `.db` es binario. Git no sabe fusionar dos versiones. Si tú y tu amigo
jugáis el mismo mundo a la vez, **una de las dos partidas se pierde entera**. No hay arreglo,
no hay merge, no hay nada. Es la naturaleza del formato.

**La solución:** turnos. Solo una persona tiene el turno de un mundo a la vez. Quien lo tiene
juega; los demás esperan. Al terminar, sube y libera. La herramienta lo gestiona sola y no te
deja subir si el turno es de otro.

## Requisitos

- **git** — https://git-scm.com
- **Python 3.10+** — https://python.org (o usa el `.exe`, ver abajo)
- Una cuenta de GitHub, y que te hayan invitado al repo de mundos

## Los dos repositorios

No los mezcles, son cosas distintas:

| Repo | Qué lleva |
|---|---|
| https://github.com/saidklelers/controlVersionesValhein | **La app**. Se clona una vez y ya |
| https://github.com/saidklelers/mundos | **Las partidas**. Lo gestiona la app sola, no lo toques a mano |

## Instalación (cada jugador, en su PC)

```bash
git clone https://github.com/saidklelers/controlVersionesValhein.git
cd controlVersionesValhein
pip install -r requirements.txt
```

Y lo abres con doble clic en `ValheimSync.bat`.

**Antes de nada, said tiene que invitarte** al repo de mundos
(`Settings > Collaborators > Add people` en https://github.com/saidklelers/mundos).
Sin eso no vas a poder bajar ni subir partidas.

## Primera vez que lo abres

Sale la pantalla de **Configuración** con tres campos:

- **Tu nombre de jugador** — pon el tuyo, distinto al de los demás. Es lo que verán tus amigos
  junto al turno y en cada subida.
- **Repositorio de mundos** — ya viene puesto (`.../saidklelers/mundos.git`). No lo cambies
  salvo que said os diga otra cosa. Todos tenéis que tener exactamente el mismo.
- **Carpeta de mundos de Valheim** — la herramienta busca sola y te lista las que encuentre.
  Si tu Valheim está en otro sitio (otro disco, Game Pass, una instalación rara), dale a
  **Examinar…** o escribe la ruta a mano.

Debajo de la carpeta te sale en verde cuántos mundos ha encontrado ahí. **Si no ves ese verde,
la ruta está mal** — salvo que aún no hayas jugado nunca y solo vayas a bajar el mundo del grupo,
que entonces es normal que esté vacía.

La primera vez que conectes, git te pedirá entrar en GitHub. Sale una ventana del navegador,
aceptas y listo.

### ¿Dónde está mi carpeta de Valheim?

En uno de estos dos sitios, según tengas Steam Cloud activado o no:

```
Con Steam Cloud:
C:\Program Files (x86)\Steam\userdata\<tu-numero>\892970\remote\worlds

Sin Steam Cloud:
C:\Users\<tu-usuario>\AppData\LocalLow\IronGate\Valheim\worlds_local
```

Es la carpeta que tiene los archivos `.db` y `.fwl`. Si tienes varias cuentas de Steam en el
mismo PC verás varias opciones: elige la de la cuenta con la que juegas.

## El día a día

```
   ┌──────────────────────────────────────────────┐
   │  1. Abres ValheimSync                        │
   │  2. "Tomar turno"    ← ahora el mundo es tuyo│
   │  3. "Bajar"          ← traes la última partida
   │  4. Juegas (cierras Valheim al acabar)       │
   │  5. "Subir"          ← subes y liberas turno │
   └──────────────────────────────────────────────┘
```

**La regla de oro:** cierra Valheim antes de subir o bajar. El juego reescribe el `.db` al
salir, así que si lo tienes abierto vas a pisar lo que acabas de sincronizar. La herramienta
te avisa y te bloquea, pero mejor que lo tengas en la cabeza.

### Los estados que verás

| Estado | Qué significa | Qué hacer |
|---|---|---|
| **Al día** | Tu copia y la del repo son iguales | Nada |
| **Versión nueva** | Alguien subió después de tu última sincronización | Bajar antes de jugar |
| **Sin subir** | Jugaste y no has subido | Subir |
| **Solo tuyo** | El mundo solo existe en tu PC | Subir para compartirlo |
| **En el repo** | Está en el repo pero no lo tienes | Bajar |
| **CONFLICTO** | Tú jugaste **y** otro subió | Leer abajo ⚠ |

### Si sale CONFLICTO

Significa que alguien se saltó el turno. Las dos partidas existen y **solo una puede sobrevivir**.
Hablad entre vosotros, decidid cuál tiene más progreso, y:

- El que se queda con la suya: **Subir (piso lo suyo)**
- El otro: **Bajar (pierdo lo mío)**

Antes de sobrescribir nada, la herramienta guarda una copia de tu versión en
`%APPDATA%\ValheimSync\backups\`, así que si os equivocáis se puede recuperar a mano.

## Dónde queda todo

| Qué | Dónde |
|---|---|
| Configuración | `%APPDATA%\ValheimSync\config.json` |
| Copia local del repo | `%APPDATA%\ValheimSync\repo\` |
| Backups de seguridad | `%APPDATA%\ValheimSync\backups\<mundo>\<fecha>\` |
| Tus mundos de Valheim | Los detecta solo (Steam Cloud o LocalLow) |

La herramienta **nunca borra** un mundo tuyo sin dejar copia antes.

## Cosas que conviene saber

**Los personajes no se sincronizan.** En Valheim el personaje (`.fch`) es tuyo y va contigo a
cualquier mundo. Solo se comparte el mundo. Si sincronizarais personajes os pisaríais el
inventario unos a otros.

**Las copias automáticas de Valheim no se suben.** Los `_backup_auto-*` que genera el juego
se ignoran; multiplicarían por cinco el tamaño del repo sin aportar nada.

**El repo crece.** Cada subida guarda el `.db` entero (git no comprime bien un binario que
cambia por todas partes). Un mundo de 6 MB × 100 subidas ≈ 600 MB. La herramienta te muestra
el tamaño abajo a la derecha y te avisa al pasar de 700 MB. Cuando llegue ese día, lo más
simple es empezar un repo nuevo con el estado actual:

```bash
gh repo create mundos2 --private
```

Y que todos cambien la URL en **Configuración > Repositorio de mundos**. El repo viejo queda
de archivo histórico por si hay que rescatar algo.

**Alguien se dejó el turno tomado.** Pasa: se les cuelga el juego, se van a dormir. Cualquiera
puede usar **Forzar turno**. Solo hazlo si estás seguro de que esa persona ya no está jugando —
si lo está, su partida se pierde.

## Hacer un .exe para los amigos

Para que no tengan que instalar Python:

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name ValheimSync ValheimSync.py
```

El ejecutable sale en `dist\ValheimSync.exe`. Aun así necesitan **git** instalado.

## Estructura del repo de mundos

Por si quieres mirarlo a mano en GitHub:

```
worlds/
  RUNATERRA/
    RUNATERRA.db      ← el mundo
    RUNATERRA.fwl     ← la semilla
    state.json        ← quién tiene el turno y quién subió lo último
.gitattributes        ← marca los .db como binarios (sin esto Windows los corrompe)
```

`state.json` es el árbitro. Tomar el turno es escribirlo y hacer push. Como el push de git es
atómico en el servidor, si dos personas lo intentan a la vez, la segunda recibe un rechazo y
se entera de que llegó tarde. No hace falta ningún servidor extra: GitHub hace de árbitro.
