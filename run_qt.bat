@echo off
rem ZAPiXDeskViewerQt — launcher for Windows.
rem
rem Uses the project's venv if it exists; otherwise, creates one and installs
rem the dependencies (Pillow + PySide6) automatically.
rem
rem The Qt viewer (ZAPiXDeskViewerQt.py) requires PySide6, which lives in the
rem venv. Do not use the system python directly.

setlocal
set "DIR=%~dp0"
set "VENV_PY=%DIR%venv\Scripts\python.exe"

if exist "%VENV_PY%" (
    "%VENV_PY%" "%DIR%ZAPiXDeskViewerQt.py" %*
    goto :eof
)

echo venv not found — creating it and installing dependencies...
python -m venv "%DIR%venv"
"%DIR%venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%DIR%venv\Scripts\python.exe" -m pip install --quiet -r "%DIR%requirements.txt"
"%VENV_PY%" "%DIR%ZAPiXDeskViewerQt.py" %*
