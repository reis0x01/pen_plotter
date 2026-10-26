#!/usr/bin/env bash
# Builds the self-contained Linux program: dist/VoicePenPlotter/ and
# dist/VoicePenPlotter-1.0-linux-x86_64.tar.gz (unpack anywhere, run ./VoicePenPlotter/install.sh
# for a menu entry, or just ./VoicePenPlotter/VoicePenPlotter). Run from anywhere.
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=          # ROS setups leak unrelated packages in through PYTHONPATH
if [ ! -x build/venv/bin/python ]; then
    python3 -m venv build/venv
fi
build/venv/bin/python -m pip install --upgrade pip
build/venv/bin/python -m pip install -r requirements.txt -r packaging/requirements-build.txt
QT_QPA_PLATFORM=offscreen build/venv/bin/python -m pytest -q -x -p no:cacheprovider
build/venv/bin/python -m PyInstaller --noconfirm --clean --distpath dist \
    --workpath build/pyinstaller packaging/voice_pen_plotter.spec
cp packaging/install_linux.sh dist/VoicePenPlotter/install.sh
cp packaging/icon.png dist/VoicePenPlotter/icon.png
"dist/VoicePenPlotter/VoicePenPlotter" --self-test
tar -C dist -czf dist/VoicePenPlotter-1.0-linux-x86_64.tar.gz VoicePenPlotter
echo "Done: dist/VoicePenPlotter-1.0-linux-x86_64.tar.gz"
