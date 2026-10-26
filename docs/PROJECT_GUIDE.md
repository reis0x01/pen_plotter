# Voice Pen Plotter: Complete Project Guide

This guide explains the whole project: what it does, how it works inside, how to use it every
day, how your machine is set up, the safety design, every module in the code, how it is tested,
and the problems solved along the way. The [README](../README.md) is the overview, the
[User Guide](USER_GUIDE.md) explains every feature and [Development](DEVELOPMENT.md) covers
building; this file is the full explanation.

---

## Contents

1. [What the project is](#1-what-the-project-is)
2. [The machine](#2-the-machine)
3. [How it works, end to end](#3-how-it-works-end-to-end)
4. [Coordinates: the most important idea](#4-coordinates-the-most-important-idea)
5. [The safety system](#5-the-safety-system)
6. [The code, module by module](#6-the-code-module-by-module)
7. [The desktop app (GUI)](#7-the-desktop-app-gui)
8. [Using it every day](#8-using-it-every-day)
9. [Settings reference (machine profile)](#9-settings-reference-machine-profile)
10. [Files the app writes](#10-files-the-app-writes)
11. [Testing](#11-testing)
12. [Troubleshooting](#12-troubleshooting)
13. [Project history: problems found and solved](#13-project-history-problems-found-and-solved)
14. [Limitations and ideas for the future](#14-limitations-and-ideas-for-the-future)
15. [Glossary](#15-glossary)

---

## 1. What the project is

A desktop application that makes a CNC pen plotter **write what you say**. You speak (English,
Turkish or another language), the words appear in a text box you can edit, and the machine writes
them on paper with a pen in a clean single-line font. It can also plot **pictures** (photos, scans,
sketches, logos: cleaned up and traced into pen lines with OpenCV), **vector drawings from PDF
files** and existing **G-code files**. Pictures and drawings go into a **drawing area** you choose
on the paper.

The app replaces a separate GRBL sender. Everything is in one window: connecting to the Arduino,
homing, jogging, reading and changing GRBL settings, previewing, plotting, pausing and stopping,
and a log of every byte sent to the machine.

**In numbers:** about 7,500 lines of Python in `app/` and `plotter/`, plus about 1,800 lines of
tests (201 automated tests), built in 38 git commits (before the picture feature) starting from
the untouched GRBL firmware source.

```
 🎤 Microphone ─► Google speech recognition ─► editable text box ─► layout (single-stroke font)
                                                                      │
 📄 PDF drawing ─► vector extraction ─────────────────────────────────┤
 🖼 Picture ─► colours/tone/smoothing ─► lines + shading ─► drawing order ┤
                                                                      ▼
                         G-code (pen up/down rules) ─► safety validator ─► streamer ─► GRBL ─► ✍️
```

---

## 2. The machine

### Hardware

| Part | What you have |
|---|---|
| Controller | Arduino **Uno** (ATmega328P) |
| Driver board | CNC Shield V3 with A4988 stepper drivers |
| Motors | NEMA 17 steppers on **X, Y and Z** |
| Pen lift | The **Z stepper** moves the pen up and down (not a servo) |
| Limit switches | One per axis (X, Y, Z), used for homing |
| Usable travel | **280 × 280 mm** (measured by you) |
| Paper | A4 portrait (210 × 297 mm) by default, or a larger sheet |

### Firmware

The board runs **GRBL 0.9j** (it reports `Grbl 0.9j` on connect). The matching GRBL source code
was kept only as a reference; it is no longer part of the project folder (it was moved out to
`voice-pen-plotter-extras/grbl-master/`). **This project never changes, rebuilds or flashes the
firmware.**

Your board's real settings, read from `$$`:

| Setting | Value | Meaning |
|---|---|---|
| `$3` | 4 | Z direction inverted (motor wiring). Don't change. |
| `$5` | 1 | Limit switches inverted (normally-closed switches) |
| `$20` | 0 | Soft limits **off** |
| `$21` | 1 | Hard limits **on**: hitting a switch while moving stops the machine |
| `$22` | 1 | Homing enabled; the board starts locked until you home |
| `$23` | 0 | Every axis homes toward its positive direction. Don't change. |
| `$27` | 5 | After touching a switch, homing backs off 5 mm |
| `$100` / `$101` | 80 | X / Y steps per mm (typical for GT2 belts) |
| `$102` | 40 | Z steps per mm |
| `$110`–`$112` | 4000 | Max speed per axis, mm/min |
| `$120` / `$121` / `$122` | 3500 / 3500 / 2500 | Acceleration, mm/s² |
| `$130` / `$131` / `$132` | 377 / 305 / 100 | Max travel the *firmware* believes in (see below) |

> **Note:** `$130`/`$131` say 377 × 305 mm, but the real travel is 280 × 280 mm. The app never
> trusts the larger number. It always uses the smaller of the firmware value and the "usable
> travel" in your profile (280 × 280). You can set `$130=280` and `$131=280` in the GRBL settings
> tab one day; the app doesn't need it.

One more firmware detail: after homing, your board reports position **0, 0, 0** (most GRBL builds
report −5 after backing off). That means your firmware sets the origin at the backed-off position.
This matters for the pen height (section 4.5).

### Your machine's directions (confirmed on paper)

Standing in front of the machine, reading the paper normally:

| | Direction |
|---|---|
| **+X** | moves the pen **to the left** |
| **+Y** | moves the pen **toward you** |
| **Home** | the **front-left** corner (closest to you, on the left) |
| Moving away from home | the pen moves **right** (X goes negative) and **away from you** (Y goes negative) |

---

## 3. How it works, end to end

Here is the full journey of "Günaydın İstanbul", from your mouth to ink.

**Step 1: recording.** You press 🎤 Record. The `sounddevice` library records 16 kHz mono audio
from the default microphone. The level meter shows loudness about 30 times a second. Recording
stops when you press the button again (or after 55 seconds).

**Step 2: speech recognition.** The audio goes to Google's speech recognition over HTTPS (the same
keyless service Chrome uses; no account or key needed). The language is the one chosen in the
Text & voice tab (`tr-TR` is set for you). Google returns plain text **without punctuation**.

**Step 3: command detection.** The transcript is checked for voice commands:
- If the *whole* utterance is a control command ("stop", "dur", "pause", "duraklat", "resume",
  "devam et"), it is executed instead of being written.
- "new line" or "yeni satır" anywhere in a sentence becomes a line break.

**Step 4: the text box.** The text is added to the editable text box. Nothing has moved yet.

**Step 5: layout.** The text is laid out on the page:
- Every character is looked up in a **Hershey single-stroke font**. These fonts draw each letter
  with one line, like handwriting, instead of an outline. Turkish letters are built from a base
  letter plus a drawn accent.
- Words wrap onto new lines using the real width of each letter. A word longer than a whole line
  is split with a hyphen.
- Each line is centered on the paper, and the block of lines is centered top to bottom (your
  setting).
- If the text is too long for one sheet, it continues on more pages.

**Step 6: G-code generation.** The letter strokes become machine instructions, following strict
pen rules:

```
G21 G90 G94          ; millimetres, absolute coordinates, feed per minute
G1 Z3 F300           ; lift the pen first
G1 X... Y... F2000   ; travel to the start of a stroke (pen up)
G1 Z-1 F300          ; pen down (Z only, no sideways movement)
G1 X... Y... F1000   ; draw the stroke
...
G1 Z3 F300           ; pen up after every stroke
...
G1 X10 Y10 F2000     ; park
M2                   ; program end
```

**Step 7: safety validation.** A separate checker reads the finished G-code line by line, as if it
knew nothing about how the code was made, and refuses the job if any rule is broken: outside the
paper, outside the machine, pen down during travel, and so on (section 5).

**Step 8: preview and time estimate.** The preview shows the page as you see it: dark lines for
pen-down strokes, dashed blue for travel moves, red for refused moves. The time estimate uses your
machine's real speeds and accelerations (section 6.6).

**Step 9: "Plot this text?"** A dialog shows what was heard and the estimated time, with **Plot
now**, **Continue speaking**, or **Edit text first**.

**Step 10: streaming.** The streamer sends the G-code over USB at 115200 baud. It never sends more
than GRBL's input buffer can hold (127 bytes). It checks every line against the safety rules once
more just before sending, and asks for the machine's position 5 times a second for the live
preview.

**Step 11: GRBL moves the motors.** The pen writes. The progress bar and the remaining-time
estimate update as lines are confirmed. At the end the pen lifts and parks.

---

## 4. Coordinates: the most important idea

Most of the problems during setup came from coordinates. They are worth understanding.

### 4.1 Three coordinate systems

| Name | Who uses it | Origin (0, 0) | Units |
|---|---|---|---|
| **Machine coordinates** (MPos) | GRBL | The home position, set by homing | mm |
| **Work coordinates** (WPos) | G-code jobs | The **paper origin** you set | mm |
| **Page coordinates** (u, v) | Layout and preview | Top-left of the paper *as you see it* | mm, u → right, v ↓ toward you |

GRBL keeps the difference between machine and work coordinates as the **work offset (WCO)**:

```
work position = machine position − WCO
```

The app sets the WCO with `G10 L20 P1`, which means "the pen's current position is this work
position". GRBL saves it in its memory (EEPROM), so it survives power cycles as long as you home.

### 4.2 Machine space is negative

After homing, the pen is at machine (0, 0). Every reachable position is **negative**:

```
machine X:  0  ───────────────►  −280      (on your machine: from the left side toward the right)
machine Y:  0  ───────────────►  −280      (from the front toward the back)
```

That is why the app checks every job against `[−280, 0]` on X and Y. Anything positive would mean
going past the home switches.

### 4.3 Page frame and axis orientation

Text is laid out as you read it: left to right, top to bottom. The profile's **axis orientation**
(+X left/right, +Y toward/away) converts the page into work coordinates:

```
x = u              if +X moves right      x = paper_width − u    if +X moves left   (yours)
y = height − v     if +Y moves away       y = v                  if +Y moves toward (yours)
```

So text is never mirrored as long as the orientation matches the real machine. A wrong orientation
is fixed in the profile, never in firmware.

### 4.4 Your paper origin

Your saved origin is **WCO = (−200, −287)**. In plain words:

> **At home, the pen sits 10 mm inside the paper's front-left corner** (10 mm to the right of
> the left edge and 10 mm in from the front edge).

With that origin, the page safe area (paper minus 10 mm on every side) covers machine
X −190 … 0 and Y −277 … 0, which is inside the 280 mm travel.

If you move the paper, set the origin again: home, then choose "front-left corner" and "10 mm
inside the corner", then **Set paper origin**. The app checks that the page fits before sending
anything.

### 4.4b A4 vs. your travel

An A4 sheet is 297 mm long but the Y travel is 280 mm. The whole sheet can't be reached, but the
**safe area** (190 × 277 mm) fits with 3 mm to spare. On a larger sheet, use **Center page in
machine travel**, which places the page in the middle of the reachable area automatically.

### 4.5 Z (pen height)

```
Z switch ─────────── about Z +5   (homing touches it, then backs off 5 mm)
pen up    ────────── Z +3          2 mm under the switch, pen in the air
work Z0   ────────── Z  0          where homing stops; on your machine the pen already touches here
pen down  ────────── Z −1          light press on the paper
```

Pen up must be **above** zero on your machine, because Z0 already touches the paper. Don't set pen
up higher than about +4, or the Z switch will trigger a hard-limit alarm.

---

## 5. The safety system

Safety is built in layers, so one mistake can't reach the machine.

### 5.1 Homing interlock

No motion command (jog, pen up/down, park, plot) is sent until homing (`$H`) has succeeded **in
this session**. After any alarm, reset or Stop, the machine counts as not homed again. There's an
override checkbox for special cases. It asks for confirmation and is never saved.

### 5.2 The independent validator

`plotter/gcode/validator.py` reads finished G-code and refuses a job when:

| Rule | Why |
|---|---|
| Any XY point is outside the **paper minus the safety margin** | The pen would leave the paper |
| Any XY point is outside the **machine travel** at the current origin | The machine would crash or hit a switch |
| An XY move happens before the pen height is known | Could drag the pen |
| A rapid (G0) move happens with the pen down | Would scratch a line at full speed |
| XY and Z move together with the pen down | Plunging or dragging; pen moves must be Z-only |
| Z goes below the pen-down height | Would crush the pen |
| The program ends with the pen down | The pen would stay on the paper |
| Arcs (G2/G3), inches (G20), G28/G30/G53/G92/G10, other coordinate systems, `$` commands | Would bypass the bounds check or change the coordinate system mid-job |

**Nothing is ever clamped** (forced inside the limits). Clamping would silently distort the
drawing. A bad job is refused, and the first offending line is shown.

### 5.3 The in-stream guard

The streamer runs the same checker **again** on every line just before sending it. Even if
something changed after the preview, a bad line is never sent.

### 5.4 Stop, errors and alarms

| Event | What the app does |
|---|---|
| **STOP button** | Feed hold (`!`) so the machine decelerates, then a soft reset (Ctrl-X). The position is no longer trusted, so homing is required. After re-homing, the pen is moved to pen-up height. |
| GRBL replies `error:` to a job line | The stream stops at once, the machine is held (`!`), and the message shows the line and an explanation. Only Stop continues. |
| GRBL reports `ALARM` | The job fails, homing is cleared, and the alarm is explained in plain language with what to do. |
| USB unplugged | The job fails and the app shows "connection lost". |
| Voice "stop" / "dur" | Same as the STOP button, but it takes seconds to recognize. **It is not an emergency stop.** |

### 5.5 Origin check

Before **Set paper origin** sends anything, the app calculates where the page would end up. If the
page wouldn't fit inside the machine travel, it explains why (for example "X: the page would reach
190 mm past the home end of the axis") and sends nothing.

---

## 6. The code, module by module

```
pen_plotter/
├── plotter/                  core logic, pure Python, no GUI code
│   ├── config.py             machine profile + coordinate conversions
│   ├── pipeline.py           text / G-code / PDF → validated, estimated jobs
│   ├── calibration.py        100 mm calibration pattern
│   ├── text/
│   │   ├── hershey.py        single-stroke fonts + Turkish letters
│   │   ├── layout.py         word wrap, pages, alignment
│   │   └── fonts/            futural.jhf, scripts.jhf, rowmand.jhf, NOTICE.txt
│   ├── gcode/
│   │   ├── generator.py      strokes → G-code with pen rules
│   │   ├── validator.py      independent safety checker
│   │   └── estimate.py       plot-time estimate
│   ├── grbl/
│   │   ├── protocol.py       understanding GRBL's replies (0.9 and 1.1)
│   │   ├── connection.py     USB serial port + simulated GRBL
│   │   └── streamer.py       the controller: sending, polling, interlock, jobs
│   ├── stt/
│   │   ├── google_stt.py     microphone + Google speech recognition
│   │   └── commands.py       voice command detection
│   └── drawing/
│       ├── pdf_import.py     vector drawings from PDF
│       ├── image_import.py   pictures → pen lines (OpenCV)
│       └── ordering.py       joining strokes, drawing order
├── app/                      PySide6 desktop GUI (only calls plotter/)
├── tests/                    201 automated tests
├── profiles/my_plotter.json  this plotter's confirmed settings (used automatically on first start)
├── docs/
│   ├── USER_GUIDE.md         using every feature, calibration, troubleshooting
│   ├── DEVELOPMENT.md        running from source, tests, building, design decisions
│   ├── PROJECT_GUIDE.md      this file
│   ├── ANALYSIS.md           the original hardware/firmware analysis
│   └── images/               screenshots
├── packaging/                building the installable program (Windows setup .exe, Linux)
├── .github/workflows/        tests on every push; installers + Releases on version tags
├── README.md                 overview, download, quick start
├── LICENSE, THIRD_PARTY_NOTICES.md
├── requirements.txt          pinned dependencies
└── run.sh / run.bat          launchers for running from source
```

**Design rule:** all machine logic lives in `plotter/`. The GUI only calls it. That keeps the
logic testable without a screen, and the same code was used to drive your machine from a script
during setup.

### 6.1 `config.py`: the machine profile

A `MachineProfile` holds every setting (section 9) and saves it as JSON. It also contains the
coordinate conversions:

- `page_to_work(u, v)` / `work_to_page(x, y)` apply the axis orientation.
- `home_corner_name()` gives the physical corner of the home position (from orientation and `$23`).
- `corner_work_coords(corner, inset)` gives the work coordinates of any paper corner, used by
  "Set paper origin".
- `jog_vector(direction)` makes the ▶ button move the pen right on screen *and* on the machine.
- `problems()` lists inconsistent settings; the Plot button stays disabled while any exist.

### 6.2 `text/hershey.py`: fonts

The **Hershey fonts** were made in the 1960s for vector plotters. Each letter is a set of lines
the pen follows, not an outline to fill, which is exactly what a pen plotter needs. Three are
bundled:

| File | Name | Look |
|---|---|---|
| `futural.jhf` | Roman Simplex | Clean single line (default) |
| `scripts.jhf` | Script Simplex | Joined handwriting style |
| `rowmand.jhf` | Roman Duplex | Bolder, double line |

The files only contain English (ASCII) characters. **Turkish letters** are composed when the font
loads:

| Letter | Built from |
|---|---|
| ç Ç ş Ş | c C s S + a cedilla hook under the baseline |
| ğ Ğ | g G + a breve (small curve) above |
| ö Ö ü Ü | o O u U + two dots above |
| İ | I + one dot above |
| ı | i with its dot stroke removed |
| â î û Â Î Û | + a circumflex (^) |

![Turkish letters in Roman Simplex, Script Simplex and Roman Duplex](images/turkish_fonts.png)

Accents are placed using the measured top and bottom of each base letter, so they fit every font.
Characters still missing are replaced by `?` with a warning. Curly quotes and long dashes from
speech recognition are converted to plain ones.

### 6.3 `text/layout.py`: layout

- **Measuring:** each letter has a known width (its "advance"), scaled to the chosen character
  height (the height of capital letters, 7 mm by default).
- **Wrapping:** words are added to a line until the next one wouldn't fit. Explicit new lines are
  kept, and over-long words are split with a hyphen.
- **Pages:** the number of lines per page is calculated so that even the tallest accent and the
  deepest descender (g, y, ç) stay inside the margins.
- **Alignment:** left/top, or centered on the paper (yours). Centering uses the paper's center, so
  unequal margins don't shift the text. A line is only pushed inward if it would cross a margin.

### 6.4 `gcode/generator.py`: G-code

Turns strokes into G-code with the pen rules shown in section 3. Details:
- Coordinates are rounded to 0.001 mm and written without unnecessary zeros, to save bytes.
- Duplicate points closer than 0.01 mm are dropped.
- A single-point stroke becomes a dot (pen down, pen up).
- Travel uses `G1` at the travel feed (2000 mm/min), or `G0` if the travel feed is 0.
- Only straight moves are produced, never arcs.

### 6.5 `gcode/validator.py`: the safety checker

Described in section 5.2. It is **stateful**: it tracks absolute/relative mode, the current
position and the pen height line by line, which is how the streamer can check one line at a time.
It also records every move (draw, travel, Z, dwell) for the preview and the time estimate.

### 6.6 `gcode/estimate.py`: time estimate

Mirrors GRBL's motion planner:
1. Each move's top speed is limited by the requested feed and each axis's max rate (`$110`–`$112`).
2. Each move's acceleration is limited by each axis's acceleration (`$120`–`$122`).
3. Cornering speed comes from GRBL's junction-deviation formula (`$11`).
4. A backward and forward pass makes sure the machine can always stop in time.
5. Each move's time comes from its accelerate/cruise/decelerate profile.

Measured on your machine: estimate **1:08**, real **1:09**.

### 6.7 `grbl/protocol.py`: understanding GRBL

GRBL 0.9 and 1.1 talk differently, and the app understands both (it detects the version
automatically):

| | GRBL 0.9 (yours) | GRBL 1.1 |
|---|---|---|
| Status | `<Idle,MPos:0,0,0,WPos:200,287,0>` | `<Idle\|MPos:0,0,0\|FS:0,0\|WCO:…>` |
| Error | `error: Alarm lock` | `error:9` |
| Alarm | `ALARM: Hard limit` | `ALARM:1` |
| Settings | `$100=80.000 (x, step/mm)` | `$100=80.000` |

Every error and alarm has a **plain-language explanation** with what to do. Every GRBL setting has
a name, a unit and a description for the settings table. Setting values are validated before
writing (for example, booleans must be 0 or 1 and steps/mm must be positive).

### 6.8 `grbl/connection.py`: serial port and simulator

- `SerialTransport` wraps the USB serial port (pyserial) and returns complete lines.
- `SimulatedGrbl` is a fake GRBL used for **Simulation mode** and the tests. It behaves like the
  real thing:
  - it boots locked in ALARM and homes Z first, then X and Y;
  - it has the same **128-byte input buffer**, and records if anyone ever overflows it;
  - its motion planner holds 15 moves;
  - it supports work offsets, feed hold/resume and soft reset (with "abort during cycle"),
    `$$`, `$I`, jogging and settings;
  - it can imitate both GRBL 0.9 and 1.1;
  - it runs at 1× to 100× speed;
  - tests can inject errors and alarms.

### 6.9 `grbl/streamer.py`: the controller

The heart of the machine communication: `GrblController`.

**Character counting.** GRBL can only hold 128 bytes of incoming text. Sending one line and waiting
for `ok` each time is slow and makes the pen stutter on short segments. Sending everything at once
overflows the buffer and corrupts commands. The streamer keeps a running count:

```
bytes in GRBL's buffer = sum of (length + 1) of every line sent but not yet answered with "ok"
send the next line only if that sum + its length + 1 ≤ 127
every "ok" or "error" frees the oldest line's bytes
```

Example: lines of 9, 26, 26, 26 and 26 bytes = 113. The next 26-byte line would make 139 > 127, so
it waits. When the first `ok` arrives, 9 bytes are freed (104 + 26 = 130, still too much), so it
keeps waiting. The second `ok` frees 26 bytes (78 + 26 = 104 ≤ 127), and one more line goes out.
This is tested exactly.

**Other duties:**
- **Status polling:** sends `?` 5 times a second (only after GRBL has booted) for the live position.
- **Version detection** from the startup banner and `$I`, and reading settings with `$$`.
- **Homing interlock** (section 5.1), and a "lift the pen after re-homing" step after a Stop.
- **Jogging:** `$J=` on GRBL 1.1; on 0.9 a small `G91 G1` move followed by `G90`.
- **Jobs:** start, pause (`!`), resume (`~`), stop, progress and the in-stream safety guard.
- **Job completion** is declared only after every line is confirmed *and* GRBL has reported Idle
  twice in a row, which avoids a timing race at the end.
- **Events** go to the GUI through a callback (`rx`, `tx`, `status`, `alarm`, `error`, `job`,
  `homed`, `settings`, `connection`, `info`).
- It runs on two background threads (a reader and a poller), so the window never freezes.

### 6.10 `stt/google_stt.py` and `stt/commands.py`: voice

- `Recorder` records 16-bit mono audio, reports the level for the meter, and stops at 55 s.
- `transcribe()` sends the audio to Google's keyless speech endpoint **over HTTPS** with the chosen
  language, and turns every failure into a clear message: offline, no speech heard, and so on.
- `parse_transcript()` separates commands from dictation (section 3, step 3). Commands are
  editable in the profile.

| Say | English | Turkish |
|---|---|---|
| Line break (anywhere) | "new line" | "yeni satır" |
| Stop (whole utterance) | "stop" | "dur" |
| Pause (whole utterance) | "pause" | "duraklat" |
| Resume (whole utterance) | "resume" | "devam et" |

### 6.11 `drawing/pdf_import.py`: PDF drawings

![A PDF drawing being plotted](images/pdf_drawing.png)

Uses **PyMuPDF** to read the vector paths on a PDF page:
- Straight lines are kept as they are.
- Bézier curves are flattened into short straight pieces (about 0.35 mm).
- Rectangles and quadrilaterals are closed.
- Filled shapes are drawn as outlines (a pen can't fill).
- White fill-only shapes, usually the page background, are skipped.
- Images and text are ignored, with a warning: convert text to paths in your drawing program first.

The drawing is then scaled to fit inside the margins (keeping proportions) or kept at actual size,
centered on the paper, and the strokes are **reordered** (nearest stroke next, possibly reversed)
to reduce pen-up travel. A 4,000-shape test drawing loads in 0.3 s and plans in about 2.6 s.

### 6.12 `drawing/image_import.py` and `drawing/ordering.py`: pictures

A pen can only draw lines, so a picture has to be turned into lines first. `ImageProcessor` does
this with **OpenCV** in seven steps. Each step is a setting in the picture window, and the window
shows the result live:

1. **Crop and resize** to a working size (900 px on the long side by default). Small pictures are
   enlarged so the traced lines come out smooth. Transparent areas become white paper.
2. **Colour palette.** k-means clustering in Lab colour space (where distances match how
   different colours look) finds the main colours (10 by default). Colours you remove are painted
   white, so they are never drawn. Their lighter shades, meaning blends of the colour with the
   paper, are removed with them, and so is the blended fringe around removed areas. A kept line
   that a removed line crossed is mended afterwards: two line ends that point straight at each
   other across a gap as wide as the removed line are joined.
3. **Edge-preserving smoothing.** A bilateral filter removes noise, paper grain and JPEG blocks
   but keeps edges sharp. This is what makes the drawing smooth instead of scratchy.
4. **Tone.** Auto levels (at most 4×, so noise isn't blown up), optional local contrast (CLAHE),
   contrast, brightness, invert.
5. **Lines**, in one of three styles:
   - Ink is chosen with **hysteresis**: pixels darker than the threshold are ink, and paler
     pixels touching them count too. Thin anti-aliased lines then stay whole instead of breaking
     into dashes, while faint grid paper stays out.
   - *Centre lines* for sketches and line art: dark areas are thinned to a 1-pixel skeleton
     (Zhang-Suen), and the skeleton is traced into lines that end exactly on their junctions.
     Where two thick lines cross, the short "bridge" between the two junctions is merged, so
     each line continues straight through the crossing.
   - *Outlines* for logos and silhouettes: the outline of every dark shape (`findContours`).
   - *Edges* for photos: Canny edge detection, with thresholds taken from the picture's own
     gradient strength and a noise floor, so dull and high-contrast photos both work.
6. **Shading (optional).** Dark areas are hatched: one hatch direction per tone level (45°,
   135°, 0°, 90°), so darker tones get denser cross-hatching. Neighbouring hatch rows are linked
   into zig-zags whenever the link stays inside the area, so the pen stays down across a whole
   area.
7. **Clean-up and drawing order.** Every line is smoothed along its length (Gaussian), then
   simplified (Douglas-Peucker: fewer, longer moves, so GRBL's planner runs smoothly), and specks
   shorter than the minimum are dropped. Both scale with the line's length, so long lines come out
   straight while small text keeps its shape.

**Drawing order** (`ordering.py`) is what makes the machine draw "logically" instead of jumping
around:
- **Layers:** outlines first, then shading from the lightest tone to the darkest, as you would
  draw by hand. Each layer starts where the previous one ended.
- **Joining:** lines whose ends touch become one continuous stroke. At a junction the
  continuation that keeps the line's direction wins, so a line through a crossing stays one
  line. A chain that comes back to its start is closed into a loop.
- **Order inside a layer:** first *nearest neighbour* (always draw the closest remaining line
  next, from whichever end is closer), then *2-opt*, which reverses a run of strokes whenever
  that shortens the pen-up travel. Closed shapes start at the point nearest the pen.

On the test pictures this cuts pen-up travel to between a quarter and a thirtieth of a random
order. A typical photo is processed in under 2 seconds.

**Drawing area.** `pipeline.drawing_area(profile)` is the rectangle (page frame, mm) that
pictures and PDF drawings are fitted into: the profile's `drawing_area`, or the text margins when
none is set. `preset_drawing_area` gives the presets (halves, quarters, centre, whole printable
area) and `clamp_drawing_area` keeps a user-drawn area inside the safety margin. A picture is
fitted by its whole (cropped) frame, so removing a colour never changes the scale.

### 6.13 `pipeline.py`: putting it together

| Function | Does |
|---|---|
| `plan_text(text, profile, settings, wco)` | Layout → G-code per page → validate → estimate |
| `plan_drawing(drawing, profile, …, mode)` | PDF or picture → fit into the drawing area → join + order (PDF) → G-code → validate → estimate |
| `drawing_area(profile)` | The rectangle drawings are placed in |
| `plan_gcode(lines, profile, …)` | Validate and estimate an imported G-code file |
| `machine_bounds(profile, settings, wco)` | Safe area + machine travel for the validator |
| `effective_travel(profile, settings)` | The smaller of `$130/$131` and your measured travel |
| `centered_work_offset(profile, settings)` | The origin that centers the page in the travel |
| `page_fit_problems(profile, settings, wco)` | Why a proposed origin wouldn't fit |

### 6.14 `calibration.py`

Generates two 100 mm lines (X and Y) with end ticks. Measure them with a ruler: the new steps/mm
is `current × 100 ÷ measured`.

---

## 7. The desktop app (GUI)

Built with **PySide6** (Qt for Python). Start it with `./run.sh` or `python -m app`.

### 7.1 Window layout

![Main window while plotting](images/main_window.png)

```
┌──────────────────┬─────────────────────────┬────────────────────────────┐
│ Connection       │                         │ [Text & voice] [Machine    │
│ Machine control  │      A4 PREVIEW         │  profile] [GRBL settings]  │
│ Plot             │                         │                            │
├──────────────────┴─────────────────────────┴────────────────────────────┤
│ Serial log  (+ command line)                                             │
└──────────────────────────────────────────────────────────────────────────┘
```

| Panel | What's in it |
|---|---|
| **Connection** | Port, baud, **Simulation** checkbox (version, speed), Connect, live state (colored), work and machine position, alarm box with explanation, Unlock (`$X`), Soft reset |
| **Machine control** | Home, homed indicator, interlock checkbox, jog pad (▲ away, ▼ toward, ◀ left, ▶ right, Z up/down, ■ cancel), step size, Pen up/down, Go to park, home-corner hint, **Set paper origin** (corner + exact/10 mm inside), Set Z zero here, Restore saved origin, **Center page in machine travel** |
| **Plot** | Summary (or refusal reason), page selector, **▶ Plot**, Pause, Resume, **■ STOP**, progress bar, elapsed/remaining time |
| **Picture & drawing** | Open picture…, Adjust picture…, Open PDF drawing…, **drawing area** (preset, left/top/width/height in mm) |
| **Picture window** | Original (click a colour to remove it, drag to crop), live result with **▶ Replay drawing order**, colour swatches, smoothing and tone, line style, shading, pen-line clean-up, line count / length / estimated time |
| **Preview** | The paper as you see it: green dashed safe area, dotted text margins, orange drawing area (on the Picture & drawing tab: drag its edges to resize, inside to move, outside to draw a new one), dark pen strokes, dashed blue travel, grey already-plotted strokes, red refused moves, orange cross = work X0 Y0, footer = which corner is home, dot = live pen (green up / orange down) |
| **Text & voice** | 🎤 Record + level meter, language, "Append to text", **"Ask to plot after speaking"**, the editable text box, layout options (font, character height, line spacing, letter spacing, margins, **horizontal/vertical alignment**, hyphenation) |
| **Machine profile** | Axis orientation, paper size, safety margin, usable travel, pen heights, feeds, park position, jog settings, voice command table, Apply & save |
| **GRBL settings** | Read `$$`, table with names/units/descriptions, editing with validation and a confirmation dialog (extra warning for `$3`/`$23`), consistency warnings |
| **Serial log** | Every line sent (`>>`) and received (`<<`), app messages (`··`), refusals (`##`), voice (`🎤`); a box to send raw commands like `$G` or `$#` |

**File menu:** Open picture… (Ctrl+I), Adjust picture…, Open PDF drawing…, Open G-code file…,
Export G-code…, Load calibration pattern, Back to text job, Save profile, Quit.

### 7.2 How the GUI stays responsive

- The controller's threads talk to the window through a **Qt signal** (`ControllerBridge`), which
  Qt delivers safely on the GUI thread.
- Recording runs on the audio library's thread; transcription runs on its own thread.
- Changes to text and settings re-plan after a short pause (250 ms), so typing doesn't stutter.
- The picture window processes on a worker thread. Only the newest result is shown, so dragging
  a slider never queues up stale work.

---

## 8. Using it every day

### 8.0 Installing on a new computer

The easiest way is the installer from the repository's **Releases** page (Windows setup `.exe` or
Linux archive): it contains everything. To run from the source code instead, follow
[Development](DEVELOPMENT.md): install the system packages (Linux: `libxcb-cursor0`,
`libportaudio2`, `dialout` group), then run `./run.sh` (or `run.bat` on Windows). Either way, on
the first start the app copies `profiles/my_plotter.json` to the profile location (section 10), so
it starts with this plotter's confirmed settings. Then try Simulation mode before connecting the
machine.

### 8.1 Writing what you say

1. Switch on the machine and run `./run.sh`.
2. **Connect**, then **⌂ Home**. Z rises first, then X and Y go home (front-left).
3. **Restore saved origin**, as long as the paper is in the same place. If you moved it, use
   **Set paper origin**.
4. Press **🎤 Record**, speak, then press it again.
5. The **"Plot this text?"** window appears: choose **▶ Plot now**.
6. Watch it write. **■ STOP** if anything looks wrong.

### 8.2 Typing instead

Type in the text box, check the preview, then press **▶ Plot**.

### 8.3 Plotting a picture

1. **File → Open picture…** (Ctrl+I) and choose a PNG/JPEG photo, scan or sketch.
2. In the picture window, choose the **line style**: *Centre lines* for drawings and handwriting,
   *Outlines* for logos, *Edges* for photos. For photos, tick **Shading**.
3. **Remove colours** you don't want drawn (background, coloured fills) by clicking their swatch
   or clicking them on the picture. **Drag on the picture** to crop it.
4. If it's noisy, raise **Smooth the picture**. If it's too faint or too dark, use brightness,
   contrast or the ink threshold. If there are too many tiny lines, raise **Shortest line**.
5. Press **▶ Replay drawing order** to watch the order, check the estimated time, then **Use this
   drawing**. Check the main preview, then **▶ Plot**.

### 8.4 Choosing where it goes on the paper

On the **Picture & drawing** tab, pick a preset (top half, a quarter, …), type the position and
size in mm, or drag on the preview: edges and corners resize the orange area, inside moves it,
outside draws a new one. The drawing is scaled to fit inside the area and centred in it. The area
is saved in the profile.

### 8.5 Plotting a PDF drawing

**File → Open PDF drawing…**, pick the page and **Fit to the page**, check the preview and the
estimated time, then **▶ Plot**. Typing in the text box switches back to text.

### 8.6 G-code files

**File → Open G-code file…** runs any file through the same validator first. **Export G-code…**
saves the current job (one file per page).

### 8.7 Trying things without the machine

Tick **Simulation (no hardware)** before Connect. Everything works the same, including alarms,
homing and plotting, at up to 100× speed.

### 8.8 After a Stop or an alarm

Read the explanation in the red box. If it says reset is required, press **Soft reset**. Then
**Home** again. The pen lifts automatically after homing.

---

## 9. Settings reference (machine profile)

Edited in the **Machine profile** and **Text & voice** tabs, and saved automatically.

| Setting | Your value | Meaning |
|---|---|---|
| Paper width × height | 210 × 297 mm | Paper size (portrait) |
| Safety margin | 10 mm | Nothing is drawn closer to the paper edge |
| Usable travel X / Y | 280 / 280 mm | Real machine travel (overrides `$130/$131` when smaller) |
| Axis orientation | +X left, +Y toward | Confirmed on paper |
| Paper origin | machine X −200, Y −287 | Home = 10 mm inside the front-left corner |
| Pen up Z / Pen down Z | +3 / −1 | Relative to the homed Z height |
| Z feed | 300 mm/min | Pen up/down speed |
| Draw feed | 1000 mm/min | Writing speed |
| Travel feed | 2000 mm/min | Speed between strokes (0 = rapid G0) |
| Park X / Y | 10 / 10 (work) | Where the pen waits after a job |
| Jog steps | 0.1, 1, 10 mm | Jog step choices |
| Jog feed XY / Z | 1000 / 200 mm/min | Jog speeds |
| Font | Roman Simplex | Also Script Simplex, Roman Duplex |
| Character height | 7 mm | Height of capital letters |
| Line spacing | 1.8 × | Distance between lines |
| Letter spacing | 0 mm | Extra space between letters |
| Margins L / R / T / B | 15 / 10 / 15 / 15 mm | Text area (never less than the safety margin) |
| Horizontal / vertical | center / middle | Text centered on the paper |
| Hyphenate | on | Split over-long words with "-" |
| Port / baud | /dev/ttyUSB0 / 115200 | Remembered from last connection |
| Speech language | tr-TR | Turkish (en-US for English) |
| Max recording | 55 s | Longest single recording |
| Ask to plot after speaking | on | The "Plot this text?" dialog |
| Voice commands | 8 phrases | English + Turkish (section 6.10) |

---

## 10. Files the app writes

| File | Contents |
|---|---|
| `~/.config/voice_plotter/profile.json` (Windows: `%APPDATA%\voice_plotter\profile.json`) | Your machine profile (section 9). A copy of the confirmed settings is kept in the repository as `profiles/my_plotter.json`. |
| `~/.config/voice_plotter/logs/session-YYYYMMDD-HHMMSS.log` | Everything sent/received in each session, refusals with diagnostic values, app messages (repeated identical status lines are collapsed) |
| Exported `.gcode` files | Wherever you save them |

The session logs are the first thing to check when something goes wrong. They're how the
"out of reach" problem was diagnosed.

---

## 11. Testing

Run all tests (no machine, microphone or internet needed):

```bash
.venv/bin/python -m pytest
```

| Test file | Tests | Covers |
|---|---|---|
| `test_gcode.py` | 49 | Generator pen rules, every validator rule, machine envelope, time estimate, calibration pattern, origin/centering math with your real numbers, axis orientation |
| `test_protocol.py` | 33 | GRBL 0.9 and 1.1 status/error/alarm/settings/banner parsing, setting validation, consistency warnings |
| `test_streamer.py` | 29 | Exact character counting, full jobs without buffer overflow, errors, alarms, pause/resume, stop + re-home + pen lift, interlock, in-stream guard, version-specific jogging, settings writes, lost connection (each against simulated GRBL **0.9j and 1.1h**) |
| `test_config_and_voice.py` | 28 | Profile save/load, problems check, jog direction, voice commands (English + Turkish), speech errors (offline, no speech, HTTPS endpoint) |
| `test_layout.py` | 24 | Fonts, wrapping, hyphenation, pages, margins, alignment/centering, Turkish letters and accents, Unicode normalization |
| `test_pdf_drawing.py` | 8 | PDF lines/rectangles/curves, background skipping, image/text warnings, page selection, fitting, stroke ordering, oversize refusal |
| `test_image_drawing.py` | 21 | Picture loading (transparency, Turkish file names), centre lines (once, straight through crossings), outlines, colour removal, crop, edges on dull photos, hatching density and zig-zags, outlines-before-shading order, joining and 2-opt ordering, drawing-area presets and placement, thin pale lines on grid paper, mending lines crossed by a removed colour, background removal without merging letters, straight long lines with intact small text |
| `test_gui_smoke.py` | 9 | The real window driven headlessly: connect → home → origin → plot; center page; origin at home corner; wrong corner refused; "Plot now" after speech; PDF plotting; picture → remove a colour → resize/move/draw the drawing area on the preview → plot |
| **Total** | **201** | All passing |

`pytest.ini` turns off the ROS 2 pytest plugins that leak in through your ROS `PYTHONPATH`.

---

## 12. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| "Out of reach" / "outside the machine travel" | The paper origin puts the page past the home switches or the far end | Home, then **Set paper origin** at the right corner, or **Center page in machine travel** |
| Text mirrored left-right | +X direction wrong in the profile | Machine profile → flip "+X" |
| Text mirrored top-bottom | +Y direction wrong | Flip "+Y" |
| Text upside down (rotated 180°) | Both wrong | Flip both |
| Pen never lifts, lines between letters | Pen-up height too low | Raise **Pen up Z** (yours is +3; max about +4) |
| Lines too faint / pen presses too hard | Pen-down height | Pen down −1.5 to −2 for darker, −0.5 for lighter |
| Letters wrong size | Steps/mm | Plot the calibration pattern, correct `$100/$101` |
| `ALARM: Hard limit` | A switch was hit while moving | Soft reset, check the origin/pen-up height, home |
| `ALARM: Abort during cycle` | Reset while moving (e.g. STOP) | Home again |
| `error: Alarm lock` | Command sent before homing | Home |
| Plot button grey | Not homed, job refused, or profile problems | Read the Plot box / Machine profile tab |
| "No GRBL banner received" | Wrong port or baud, or another program has the port | Close other senders, check `/dev/ttyUSB0`, 115200 |
| App won't open (`xcb` plugin error) | Missing system library | `sudo apt install libxcb-cursor0` |
| `ModuleNotFoundError: plotter` | Ran a file outside the project | Use `./run.sh` or `python -m app` (running `app/main_window.py` also works now) |
| Voice: "Cannot reach Google" | No internet | Check the connection or type the text |
| PDF: "No vector drawing found" | Scanned/photo PDF | Use a vector PDF, or export the page as PNG and use **Open picture…** |
| Picture: a web of lines over a photo | *Centre lines* on a photo | Use *Edges* (+ shading) for photos, *Outlines* for logos |
| Picture: background drawn | Its colour isn't removed | Click its swatch (or the colour on the picture), or raise brightness |
| Picture: plot would take hours | Too much detail or dense shading | Raise *Smooth the picture* and *Shortest line*, widen hatch spacing, fewer tone levels |
| "OpenCV is not installed" | Old `.venv` | Run `./run.sh`: it installs requirements again when `requirements.txt` changes |
| PDF text missing | Text isn't converted to paths | Inkscape: *Path → Object to Path*, then export again |

---

## 13. Project history: problems found and solved

| # | Problem | What we learned / did |
|---|---|---|
| 1 | The original brief described a working Python pipeline, but the repository only contained the GRBL firmware source | Built the whole application from scratch |
| 2 | The first analysis assumed a servo pen lift (from the brief) | You confirmed a **Z stepper** with a top switch; the servo-related risks were removed |
| 3 | A4 (297 mm) is longer than the 280 mm Y travel | Safe-area origin mode, machine-envelope check, later "Center page" |
| 4 | Running `main_window.py` directly failed (`No module named plotter`) | The file now relaunches itself as `python -m app` |
| 5 | Qt wouldn't open a window | Installed `libxcb-cursor0` (documented) |
| 6 | No Google credentials wanted | Switched to Google's keyless speech endpoint over HTTPS |
| 7 | "Out of reach" for every job | The origin had been set at home as if it were the opposite corner. Added "Center page", any-corner origin with a fit check, and file logging for diagnosis |
| 8 | Firmware says 377 × 305 mm travel, reality is 280 × 280 | The app uses the smaller value |
| 9 | Pen didn't lift | On your firmware Z0 (after homing) already touches the paper. Pen up is now Z+3, pen down Z−1 |
| 10 | Text came out mirrored, then still mirrored | Found step by step: **+X left, +Y toward**, home front-left |
| 11 | Wanted text in the center | Added horizontal/vertical centering on the paper |
| 12 | Wanted to plot drawings | Added PDF vector import |
| 13 | Wanted Turkish | `tr-TR` speech, Turkish voice commands, composed Turkish letters |
| 14 | The professor asked for pictures drawn smoothly, in a logical order, in a chosen area of the paper | Picture import with OpenCV (colour removal, smoothing, tone, centre lines/outlines/edges, hatching), stroke joining + nearest-neighbour/2-opt ordering with outlines before shading, and a drawing area set by preset, numbers or dragging on the preview |
| 16 | An installable program was wanted, with the owner's name and a Kali Linux look | Kali-inspired dark theme with a dragon-curve backdrop and icon, **MOHAMED OSSAMA ELIWA** in the banner, title, status bar, About box and installer. The project folder was tidied (GRBL source and editor caches moved out, docs together in `docs/`). PyInstaller + Inno Setup build a Windows installer with everything inside (`packaging/`, plus a GitHub workflow), and a Linux package is built the same way |
| 15 | The system Python was upgraded (3.12 → 3.14) and `.venv` stopped working | `run.sh` rebuilds `.venv` when the Python version changes and reinstalls when `requirements.txt` changes |

Bugs caught by tests or simulation before they reached the machine include: the re-plan timer being
reset forever by status reports, a stale position used when saving the origin, a race that could
start page 2 by itself, a Qt timer touched from a background thread, and the `[VER:]` version parser
capturing the build date.

---

## 14. Limitations and ideas for the future

**Current limitations**
- Only letters in the fonts can be plotted: English + Turkish. Arabic and other scripts become `?`.
- The keyless Google speech endpoint is unofficial and rate-limited, needs internet, and adds no
  punctuation.
- Voice "stop" takes seconds to recognize and is not an emergency stop.
- PDF: vectors only. Images inside a PDF are ignored (save them as files and use Open picture),
  text must be converted to outlines, and fills are drawn as outlines.
- Pictures are drawn with one pen: colours become lines and shading, not ink colours. Dense
  shading of a large photo can take more than an hour; the picture window shows the estimate.
- Re-planning a very large PDF (thousands of shapes) takes a few seconds and briefly pauses the
  window.
- GRBL 0.9 has no jog cancel; keep jog steps small.
- Firmware soft limits are off and `$130/$131` don't match reality. The app protects against this
  itself, but other software wouldn't.
- One paper size at a time (set in the profile).
- PyMuPDF is AGPL-licensed: fine for personal use, but it matters if the app is ever distributed.

**Ideas**
- SVG and DXF import (same pipeline as PDF).
- Hatching for PDF fills (pictures already have it).
- Multi-colour plotting: one layer per palette colour, with a pen-change pause in between.
- More fonts, or a font with Arabic glyphs.
- Offline speech recognition (Vosk or Whisper).
- A "pen change" pause between colors.
- Setting `$130=280`, `$131=280` and enabling soft limits (`$20=1`) as an extra firmware-level
  safety net.

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **GRBL** | The firmware on the Arduino that turns G-code into stepper motor pulses |
| **G-code** | Text commands for CNC machines, e.g. `G1 X10 Y20 F1000` = move in a straight line to X10 Y20 at 1000 mm/min |
| **Homing** (`$H`) | Moving each axis to its limit switch to find a known reference position |
| **MPos / WPos** | Machine position (from home) / work position (from your paper origin) |
| **WCO** | Work coordinate offset: the difference between MPos and WPos |
| **G10 L20 P1** | "Set the work origin so that the current position has these work coordinates" |
| **Feed rate (F)** | Speed in mm/min |
| **Feed hold** (`!`) / **resume** (`~`) | Pause / continue motion (real-time commands) |
| **Soft reset** (Ctrl-X, 0x18) | Restarts GRBL immediately; position is lost if it was moving |
| **Hard limit** | Alarm triggered by hitting a limit switch |
| **Soft limit** | Firmware refusal of moves beyond `$13x` (off on your machine) |
| **Hershey font** | Single-stroke vector fonts made for plotters |
| **Character counting** | Sending lines only while the total unacknowledged bytes fit GRBL's 128-byte buffer |
| **Safe area** | The paper minus the safety margin on every side |
| **Simulation mode** | Running everything against a fake GRBL, with no hardware |
| **STT** | Speech-to-text |
