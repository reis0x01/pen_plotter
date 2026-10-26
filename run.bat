@echo off
REM Voice Pen Plotter launcher for Windows. First run creates .venv; requirements.txt is
REM installed again whenever it changes.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python.exe -c "import hashlib,pathlib,sys; s=pathlib.Path('.venv/.requirements'); sys.exit(0 if s.exists() and s.read_text()==hashlib.sha1(pathlib.Path('requirements.txt').read_bytes()).hexdigest() else 1)"
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt && .venv\Scripts\python.exe -c "import hashlib,pathlib; pathlib.Path('.venv/.requirements').write_text(hashlib.sha1(pathlib.Path('requirements.txt').read_bytes()).hexdigest())"
)
.venv\Scripts\python.exe -m app %*
