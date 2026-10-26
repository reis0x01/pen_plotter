"""Entry script of the packaged program (PyInstaller needs a script, not ``python -m app``).

``VoicePenPlotter --self-test[=report.txt]`` checks, without opening a window, that everything
the app needs was packed in: the libraries, the fonts, the bundled profile, picture tracing and
planning. Exit code 0 means OK. The windowed Windows program has no console, so the result can
also be written to a report file.
"""
import sys


def self_test() -> str:
    import numpy as np

    import cv2
    import pymupdf  # noqa: F401  (packed?)
    import serial  # noqa: F401
    import speech_recognition  # noqa: F401
    from app.__main__ import BUNDLED_PROFILE
    from plotter.config import MachineProfile
    from plotter.drawing.image_import import ImageProcessor, ImageSettings
    from plotter.pipeline import plan_drawing, plan_text
    from plotter.text.hershey import available_fonts

    assert set(available_fonts()) >= {"futural", "scripts", "rowmand"}, available_fonts()
    assert BUNDLED_PROFILE.exists(), BUNDLED_PROFILE
    profile = MachineProfile()
    assert plan_text("Merhaba dünya", profile).ok
    img = np.full((200, 300, 3), 255, np.uint8)
    cv2.circle(img, (150, 100), 60, (0, 0, 0), 4)
    res = ImageProcessor(img, "self-test").process(ImageSettings(), (180, 180))
    assert res.drawing.strokes and plan_drawing(res.drawing, profile).ok
    try:
        import sounddevice  # noqa: F401
        audio = "ok"
    except OSError as exc:                  # no PortAudio on this computer: only recording is affected
        audio = f"unavailable ({exc})"
    return f"self-test OK: OpenCV {cv2.__version__}, fonts, profile, tracing, planning; audio {audio}"


def run_self_test(report: str | None) -> int:
    import traceback
    try:
        text, code = self_test(), 0
    except Exception:                       # never a crash dialog: report and fail
        text, code = "self-test FAILED\n" + traceback.format_exc(), 1
    if sys.stdout:
        print(text)
    if report:
        with open(report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return code


if __name__ == "__main__":
    flag = next((a for a in sys.argv[1:] if a.startswith("--self-test")), None)
    if flag:
        sys.exit(run_self_test(flag.partition("=")[2] or None))
    from app.__main__ import main
    sys.exit(main())
