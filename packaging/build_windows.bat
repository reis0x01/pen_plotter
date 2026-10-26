@echo off
REM Builds the Windows installer dist\VoicePenPlotter-Setup-1.0.exe.
REM
REM Needed on the BUILD computer only (the people who install the result need nothing):
REM   - Python 3.12 or newer, 64-bit, from python.org ("Add python.exe to PATH" ticked)
REM   - Inno Setup 6 (https://jrsoftware.org/isdl.php); this script tries winget if it's missing
REM   - an internet connection, to download the libraries once
REM
REM Double-click this file, or run it from any folder. Takes about 5-10 minutes.
setlocal
cd /d "%~dp0\.."

echo === 1/4  Build environment
if not exist build\venv\Scripts\python.exe (
    py -3 -m venv build\venv || python -m venv build\venv || goto :fail
)
build\venv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
build\venv\Scripts\python.exe -m pip install -r requirements.txt -r packaging\requirements-build.txt || goto :fail

echo === 2/4  Checking the app (tests)
set QT_QPA_PLATFORM=offscreen
build\venv\Scripts\python.exe -m pytest -q || echo WARNING: some tests failed - building anyway, check the messages above.
set QT_QPA_PLATFORM=

echo === 3/4  Packing Python, the libraries and the app into dist\VoicePenPlotter
build\venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\voice_pen_plotter.spec || goto :fail

echo === 4/4  Making the installer
set ISCC=
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%P
if not defined ISCC (
    echo Inno Setup not found, trying to install it with winget...
    winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
    for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%P
)
if not defined ISCC (
    echo.
    echo Inno Setup 6 is not installed. Install it from https://jrsoftware.org/isdl.php and run this again.
    echo The program itself is ready in dist\VoicePenPlotter\ ^(VoicePenPlotter.exe^).
    goto :fail
)
%ISCC% packaging\installer.iss || goto :fail

echo.
echo Done: dist\VoicePenPlotter-Setup-1.0.exe
echo Give that one file to users: they run it, click Next, and start the app.
pause
exit /b 0

:fail
echo.
echo BUILD FAILED (see the messages above).
pause
exit /b 1
