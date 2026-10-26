# Voice CNC Pen Plotter: Analysis

Phase 1 date: 2026-09-23. Revised 2026-09-23 after the owner's hardware corrections
(section 0). The original servo-related findings C1, C2, H2, H3 and H4 have been removed.
They assumed a servo pen lift, and this machine uses a Z stepper.

## 0. Confirmed hardware (from the owner; this overrides the source-code defaults)

| Item | Confirmed value |
|---|---|
| Controller | Arduino **Uno** (ATmega328P) + CNC Shield V3, A4988 drivers |
| Axes | X, Y, Z are all NEMA 17 steppers. **Z is a stepper; pen up/down are ordinary Z moves.** |
| Limit switches | 3 (X, Y, Z). The machine homes on every power-up. |
| Homing | Z first (Z homes to its switch at the **top**), then X+Y. This matches `HOMING_CYCLE_0/1` in `config.h` and is correct. |
| Pen stroke | Pen contacts the paper about 4 mm below Z home. Profile defaults: pen up **Z0**, pen down **Z-4** (work Z), editable in the GUI. |
| Usable travel | **280 × 280 mm** (X × Y) |
| Motor directions | X clockwise and Y counter-clockwise on positive motion and homing. These are already handled by `$3` / `$23` and **must not be changed**. |
| Firmware | GRBL is already flashed and configured. **No firmware changes, rebuilds or flashing.** |
| Jog check (+X / +Y pen direction) | **Not yet provided** (template placeholders `[fill: left / right]`, `[fill: toward me / away from me]`). This is stored as the profile settings `x_positive` / `y_positive` (defaults: right / away) and confirmed on the first hardware run. |

## 0.1 Board output

The `$I` and `$$` blocks in the approval message were still `[paste here]` placeholders, so the
real EEPROM values are **not available yet**. Until they are:

- The GUI reads `$I` and `$$` from the board on every connect, detects the version (0.9 or 1.1
  report format), shows each setting with its description, and uses the real `$110–$122` values
  for time estimates and the real `$130/$131` for the machine-envelope check.
- The consistency checks below are what to look at once `$$` is available. The GUI's settings
  panel flags them automatically.

| Setting | Must be consistent with | Check |
|---|---|---|
| `$130`, `$131` (max travel X, Y) | 280 × 280 mm usable travel | Should be ≈ 280. If it is still the source default of 200, soft limits (`$20=1`) would stop the machine 80 mm short. |
| `$132` (max travel Z) | Pen stroke ≈ 4 mm below Z home | Must be > 4 + `$27` (pull-off). |
| `$22` (homing enable) | Homes on every power-up | Must be 1. |
| `$20` (soft limits) | Travel values | If 1, it needs correct `$130–$132`. |
| `$100`, `$101`, `$102` (steps/mm) | Real mechanics | Verify by drawing the 100 mm calibration line (see the User Guide). The source default of 250 is only a placeholder. |
| `$3`, `$23` | Motor directions | **Do not change**, even if they look odd. |

**Settings are only read and reported. Nothing is written without the owner confirming it in the GUI.**

## 1. What is in this repo

```
cncp/
├── grbl-master/     unmodified GRBL source (git baseline commit); reference only, never rebuilt
│                    (now moved out of the project, to voice-pen-plotter-extras/)
├── plotter/         core logic, pure Python (no Qt)
├── app/             PySide6 GUI (only calls plotter/)
├── tests/           pytest suite (simulated GRBL, no hardware)
├── ANALYSIS.md      this file
└── README.md        setup, running, calibration, troubleshooting (now split into docs/)
```

The Python pipeline described in the original brief was not present when this analysis started.
It was built from scratch in Phase 2.

## 2. Firmware facts from the source (`grbl-master/`)

| Item | Value in source | Relevance |
|---|---|---|
| Version | GRBL **0.9j** (build 20160726) | The flashed board may be different. The app auto-detects 0.9 or 1.1 from the banner and `$I`. |
| Homing cycle | Z, then X+Y | Correct for this machine. |
| `HOMING_INIT_LOCK` | on | With `$22=1`, the board boots into ALARM until `$H`. |
| After homing | Machine coordinates are ≤ 0 (`HOMING_FORCE_SET_ORIGIN` off) | The machine envelope is `[-$13x, 0]` per axis. Paper position comes from the G54 work offset (`G10 L20 P1`), which GRBL keeps in EEPROM. |
| RX buffer | 128 bytes | The streamer keeps ≤ 127 bytes in flight (character-counting protocol). |
| Line buffer | 80 characters | Generated lines are well under this. |
| 0.9 reports | `<Idle,MPos:…,WPos:…>`, `error: Bad number format`, `ALARM: Hard limit` | The parser supports these… |
| 1.1 reports | `<Idle\|MPos:…\|FS:…\|WCO:…>`, `error:2`, `ALARM:1`, `[MSG:…]` | …and these. |
| `$$` format | 0.9 appends descriptions: `$100=250.000 (x, step/mm)`. 1.1 prints only `$100=250.000`. | The parser strips the description. |
| Jogging | 0.9 has no `$J=` | On 0.9, jogging uses small `G91 G1` moves followed by `G90`. On 1.1 it uses `$J=` and jog cancel (0x85). |
| Soft reset during motion | Position lost (`ALARM: Abort during cycle` / `ALARM:3`) | Stop always requires re-homing. |

## 3. Risks, ranked by severity

### Critical

**C3. `$130/$131` may not match the 280 × 280 mm travel.**
The source default is 200 mm. If the board still has it, soft limits reject valid moves, or (with
soft limits off) the machine-envelope check can't protect the frame. *Mitigation:* the GUI flags
the mismatch after reading `$$`. The owner confirms before any setting is written.

**C4. Steps/mm must be verified.**
If `$100/$101` are wrong, text prints at the wrong scale and the A4 bounds check no longer matches
the physical paper. *Mitigation:* the calibration procedure in the User Guide (100 mm line), run before real
jobs.

**C5 (new). An A4 portrait sheet (297 mm) is longer than the 280 mm Y travel.**
The machine can't reach the whole sheet in either orientation. The safe area (A4 minus 10 mm on
each side) is 190 × 277 mm, which fits within 280 × 280 with only **3 mm to spare in Y**. A
consequence: the paper corner itself is usually out of reach, so "jog to the corner and set the
origin" is not always possible.
*Mitigations (implemented):*
(a) "Set paper origin" has a second mode, *pen is at the safe-area corner*, which sends
`G10 L20 P1 X10 Y10` from a reachable point 10 mm inside the corner.
(b) Every job is also checked against the machine envelope (`[-$13x, 0]` in machine coordinates)
at the live work offset, both before and during streaming, and is refused if it would leave the
travel.

### High

**H1. The pipeline was absent.** It was built in Phase 2 with tests. See README.

**H5 (new). Jog and axis orientation is not confirmed yet.**
If `x_positive` / `y_positive` in the profile don't match the machine, jog arrows point the wrong
way and text comes out mirrored. *Mitigation:* the first-run checklist verifies this. It is fixed
in the profile (GUI), never in firmware.

**H6 (new). Stop loses position.**
Stop sends feed hold (`!`), then soft reset (0x18). The app then requires `$H` before any motion,
lifts the pen after homing (Z homes first, so the pen rises immediately), and never assumes the old
position.

### Medium

**M1.** No jog cancel on 0.9. Jog steps stay small (0.1 / 1 / 10 mm). On 1.1, `$J=` and `0x85` are used.

**M2.** Blocking send-and-wait starves the planner. It is replaced by the character-counting
streamer.

**M3.** The two report formats differ. The parser handles both, with tests for each.

**M4.** The source-default acceleration (10 mm/s²) would make plots slow. The ETA uses the real
`$120/$121`.

**M7 (new). Voice "stop" is not an emergency stop.**
Speech recognition takes one to several seconds. The on-screen Stop button (and the machine's
power) is the real stop.

### Low

- `.vscode/` contains ROS2 IntelliSense paths and large binary DB files. It is excluded via `.gitignore`.
- `grbl-master/doc/script/*.py` are Python 2 examples and are not used.

## 4. Requirements implemented in Phase 2

- Single-stroke Hershey fonts (Roman Simplex default, Script Simplex, Roman Duplex).
- Word wrap using real advance widths × scale. Long words are hard-broken with a hyphen, explicit
  newlines are honored, and page overflow asks before continuing on a new sheet.
- Pen discipline: Z up first, Z up before every travel move, Z-only pen down before each stroke,
  Z up and park at the end. G0/G1 only.
- An independent validator checks pen discipline and bounds on the final G-code, both before
  streaming and line by line inside the streamer. Coordinates are never clamped; a bad job is
  refused and the offending line is shown.
- A homing interlock blocks motion until `$H` succeeds in the current session. An override exists
  but must be switched on explicitly and is never saved.
- Speech recognition uses Google's keyless Web Speech endpoint (the owner chose "no credentials"),
  over HTTPS, with `en-US` by default. This endpoint gives no automatic punctuation. Recognized
  voice commands are configurable.

## 5. Open items (need the owner / hardware)

1. Paste the real `$I` and `$$` output (section 0.1). The GUI shows the same checks live on
   connect.
2. Confirm the jog directions (+X → left/right, +Y → toward/away) on the first run and set them in
   *Machine profile → Axis orientation*.
3. Calibrate steps/mm with the built-in 100 mm pattern, then confirm the pen-down depth (default
   Z−4).
