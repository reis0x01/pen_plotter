# Development

Running from source, testing, building the installers, and how the code is organised.

## Running from source

You need **Python 3.12 or newer** and git. The first run downloads about 300 MB of packages.

### Linux (Ubuntu / Debian)

```bash
sudo apt install python3-venv libxcb-cursor0 libportaudio2   # Qt window support, microphone
sudo usermod -aG dialout $USER                               # USB serial port (log out and in)
./run.sh
```

`run.sh` creates `.venv`, installs `requirements.txt`, and starts the app. It reinstalls when
`requirements.txt` changes and rebuilds `.venv` when the system Python version changes. Manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH= .venv/bin/python -m app
```

| Without | Symptom |
|---|---|
| `libxcb-cursor0` | *Could not load the Qt platform plugin "xcb"* |
| `libportaudio2` | Everything works except microphone recording |
| `dialout` group | *Permission denied* on `/dev/ttyUSB0` |

> **ROS users:** a sourced ROS 2 environment puts `/opt/ros/...` on `PYTHONPATH`. `run.sh` clears it
> for the app, and `pytest.ini` disables the ROS pytest plugins.

### Windows

1. Install Python 3.12+ from [python.org](https://www.python.org/downloads/) with **"Add
   python.exe to PATH"** ticked, and [Git for Windows](https://git-scm.com/download/win).
2. Clone the repository and double-click **`run.bat`**.

### macOS

`./run.sh` works as on Linux. The Arduino appears as `/dev/tty.usbmodem…` or `/dev/tty.usbserial…`.

### Dependencies

Pinned in `requirements.txt`:

| Package | Purpose |
|---|---|
| PySide6 | User interface (Qt 6) |
| pyserial | Serial connection to the controller |
| sounddevice, numpy | Microphone recording and level meter |
| SpeechRecognition | Google speech recognition |
| pymupdf | Vector drawings from PDF files |
| opencv-python-headless | Picture processing (headless build, so its Qt can't clash with PySide6) |
| pytest | Tests |

Building the installers additionally uses PyInstaller (`packaging/requirements-build.txt`) and, on
Windows, Inno Setup 6.

## Tests

```bash
.venv/bin/python -m pytest          # Windows: .venv\Scripts\python -m pytest
```

201 tests, no hardware, microphone or network needed. They cover layout and fonts (including
Turkish), G-code generation and pen discipline, every validator rule, the machine envelope and
origin maths, the time estimator, GRBL 0.9 and 1.1 protocol parsing, the streamer against the
simulated GRBL (buffer limits, errors, alarms, pause, resume, stop, interlock, in-stream guard,
connection loss), PDF import, picture tracing and drawing order, the drawing area, voice commands,
the profile, speech error handling, and headless runs of the whole GUI including complete
simulated plots.

## Building the installers

The installers contain Python and every library (PyInstaller, one folder), so users install
nothing else. On first start the app copies `profiles/my_plotter.json` into place.

| Target | Command | Output |
|---|---|---|
| Windows installer | `packaging\build_windows.bat` (on Windows; installs Inno Setup with winget if missing) | `dist\VoicePenPlotter-Setup-1.0.exe` |
| Linux archive | `./packaging/build_linux.sh` | `dist/VoicePenPlotter-1.0-linux-x86_64.tar.gz` |

PyInstaller can't cross-compile, so the Windows installer must be built on Windows — locally or on
GitHub Actions:

- **Every push / pull request** runs the tests on Windows and Linux.
- **Actions → Build → Run workflow** also builds both packages and offers them as artifacts.
- **Pushing a version tag** builds both and attaches them to a GitHub Release:

  ```bash
  git tag v1.0
  git push origin v1.0
  ```

Every build self-checks the packed program with `VoicePenPlotter --self-test` (libraries, fonts,
profile, picture tracing, planning). The packaging recipe (`packaging/voice_pen_plotter.spec`)
leaves out what the app never uses — offline speech models, unused Qt modules and plugins, Qt
translations and, on Linux, the GTK stack — and verifies the remaining libraries' dependencies.

To release a new version, update the version in `app/theme.py`, `packaging/installer.iss`,
`packaging/version_info.txt` and the archive names, then tag.

## Project structure

```
app/                       desktop interface (PySide6); only calls plotter/
  main_window.py           window, menus, wiring of panels to the core
  preview.py               the sheet preview and drawing-area editing
  image_dialog.py          the picture window (live tracing, colour removal, replay)
  theme.py                 dark theme, artwork, owner and version
  panels/                  connection, machine, plot, text, picture & drawing, profile, settings, log
plotter/                   core logic, pure Python, no GUI code
  config.py                machine profile (JSON) and page ↔ machine coordinates
  pipeline.py              text / picture / PDF / G-code → validated, time-estimated jobs
  calibration.py           100 mm calibration pattern
  text/                    Hershey single-stroke fonts and text layout
  gcode/                   G-code generator, safety validator, time estimator
  grbl/                    protocol parsing, serial + simulated GRBL, streaming controller
  stt/                     microphone, Google speech recognition, voice commands
  drawing/                 PDF import, picture tracing (OpenCV), stroke joining and ordering
tests/                     pytest suite
profiles/my_plotter.json   this plotter's confirmed profile (installed on first start)
packaging/                 PyInstaller spec, Inno Setup script, build scripts, icon
docs/                      user guide, development, project guide, hardware analysis, images
.github/workflows/         continuous integration and release builds
run.sh / run.bat           run from source
```

All machine logic lives in `plotter/`, so it can be tested without a display.

## Design decisions

- **Page frame vs. machine frame.** Layout and preview work in the page as seen from the front.
  The profile's axis orientation maps it to GRBL X/Y, so a wrong direction is fixed in the
  profile, never in firmware, and text can't come out mirrored.
- **Refuse, don't clamp.** Clamping distorts drawings and hides mistakes; bad jobs are refused
  with the line number.
- **Paper origin via `G10 L20 P1`**, stored by GRBL (G54) and in the profile; the exact origin is
  read back from GRBL's reported work offset.
- **Pen moves are Z-only.** Any combined XY + Z move with the pen down is refused.
- **Stop = feed hold, then soft reset.** Position is treated as lost and re-homing is required.
- **A job error holds the machine** rather than resetting it, so you can see where it stopped.
- **Job completion** is declared after all lines are acknowledged and GRBL reports Idle twice in
  a row, avoiding a race with GRBL's auto cycle start.
- **Jogging** uses `$J=` on GRBL 1.1 (cancellable) and small `G91 G1` moves on 0.9.
- **Voice control commands must be the whole utterance**, so dictated words are never acted on.
- **Pictures** are processed in `plotter/drawing/image_import.py` (no Qt dependency):
  - k-means colour palette in Lab space; removing a colour also removes its blends with the paper.
  - Hysteresis ink threshold, so thin anti-aliased lines stay whole and grid paper stays out.
  - Zhang-Suen thinning and a skeleton tracer that ends every line on its junction; short bridges
    between nearby junctions are merged so lines continue straight through crossings.
  - Canny thresholds from the picture's own gradient strength, with a noise floor.
  - Length-adaptive smoothing and Douglas-Peucker simplification.
  - Drawing order: outlines, then shading light to dark; joining with direction continuity, then
    nearest neighbour + 2-opt per layer (`plotter/drawing/ordering.py`).
  - The picture window processes on a worker thread and shows only the newest result.
- **PDF drawings** are read as vectors (PyMuPDF); Bézier curves are flattened to ~0.35 mm chords
  and page backgrounds are skipped.
- **Status polling** runs 5× per second and starts only after GRBL's banner, so it never talks to
  the Arduino bootloader.
