# ZAPiXDeskViewer

<p align="center">
  <img src="logo_orig.png" alt="ZAPiXDesk logo" width="360">
</p>

Viewer for ZAPiXDESK exports (PowerShell). Verifies the `.zip` MD5,
extracts the decoded databases and displays the conversations with media,
documents, audios, videos, contacts (vCard), reactions and links.

- **Splash screen** with the ZAPiXDesk logo on startup.
- **Window icon** = the logo (without the "ZAPiXDESK" text).
- Works on **Linux/macOS** and **Windows**.
- **Two viewers**: `ZAPiXDeskViewer.py` (tkinter, original) and
  `ZAPiXDeskViewerQt.py` (PySide6, recommended — no trailing artifacts on
  resize and more robust threads).

## Branding files

| File             | Use                                               |
|------------------|---------------------------------------------------|
| `splash.png`     | Splash screen (480×300, logo on a dark background) |
| `logo_icone.png` | Window icon (logo without the text) — Linux/macOS |
| `zapixdesk.ico`  | Window icon — Windows (multiple sizes)             |
| `logo_orig.png`  | Original logo (with text) — source of the assets   |

## How to run

The app needs **Pillow** (with `PIL.ImageTk`) for the splash, the icon and
the media previews. The system Pillow (Debian) does **not** include
`PIL.ImageTk`, so we use the project's *virtual environment* (venv).

> **Recommended:** use the Qt viewer (`ZAPiXDeskViewerQt.py`) — see
> [Qt viewer (recommended)](#qt-viewer-recommended) below. It has dedicated
> launcher scripts (`run_qt.sh` / `run_qt.bat`) that create the venv and
> install the dependencies automatically.

The tkinter viewer (`ZAPiXDeskViewer.py`) has no launcher script, so run it
manually inside the venv.

### Linux / macOS (tkinter viewer)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 ZAPiXDeskViewer.py
```

### Windows (tkinter viewer)

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python ZAPiXDeskViewer.py
```

> The Qt viewer's launcher scripts (`run_qt.sh` / `run_qt.bat`) do all of
> this automatically — just run them.

## Qt viewer (recommended)

The original viewer (tkinter) can freeze when switching conversations and
leave "trailing" artifacts (broken pixel-art) when resizing with large
conversations. The Qt viewer (`ZAPiXDeskViewerQt.py`) uses `QGraphicsView` +
`QGraphicsProxyWidget` for the chat and `QThread` for data loading — the X11
compositor handles the resizing, with no manual reflow.

To use the Qt viewer:

```bash
# Linux / macOS
./run_qt.sh

# Windows
run_qt.bat
```

Or manually (PySide6 is in the venv, not in the system Python):

```bash
# Linux / macOS
source venv/bin/activate
python ZAPiXDeskViewerQt.py

# Windows
venv\Scripts\activate
python ZAPiXDeskViewerQt.py
```

> **Important:** do not use the system `python3` directly — PySide6 is
> installed in the project's venv. The `run_qt.sh`/`run_qt.bat` scripts
> handle this automatically.

The original tkinter viewer keeps working with Pillow only (run it manually
inside the venv, as shown in [How to run](#how-to-run)).

## Build an executable (optional)

To package into a single binary (`.exe` on Windows / binary on Linux):

```bash
# inside the venv
pip install pyinstaller

# Windows (with icon):
pyinstaller --onefile --windowed --icon zapixdesk.ico ZAPiXDeskViewer.py

# Linux:
pyinstaller --onefile --windowed ZAPiXDeskViewer.py
```

Place `splash.png`, `logo_icone.png` and `zapixdesk.ico` in the same folder
as the generated executable (the `dist/` folder), or adjust `--add-data`
according to your OS.

## Dependencies

See `requirements.txt`:

- `pillow>=11.0` — images (splash, icon, media previews).
- `PySide6>=6.5` — Qt viewer (optional for the original tkinter viewer).
- `pyinstaller` (optional) — packaging into an executable.

> The tkinter viewer uses only the Python standard library + Pillow.
> The Qt viewer adds PySide6.
