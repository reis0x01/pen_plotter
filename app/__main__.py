"""Entry point: ``python -m app`` (and the packaged program)."""
import shutil
import sys
from pathlib import Path

# This plotter's confirmed settings, shipped with the app (and inside the installed program).
BUNDLED_PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "my_plotter.json"


def seed_profile():
    """On the very first start, begin with this plotter's confirmed profile instead of generic
    defaults. An existing profile is never touched."""
    from plotter.config import default_profile_path
    target = default_profile_path()
    if not target.exists() and BUNDLED_PROFILE.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BUNDLED_PROFILE, target)
        except OSError:
            pass                                    # the app still starts, with defaults


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from . import theme
    from .main_window import MainWindow

    seed_profile()
    app = QApplication(sys.argv)
    app.setApplicationName(theme.APP_NAME)
    app.setOrganizationName(theme.OWNER)
    theme.apply(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
