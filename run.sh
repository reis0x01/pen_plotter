#!/usr/bin/env bash
# Voice Pen Plotter launcher for Linux/macOS. Creates .venv on the first run, rebuilds it when the
# system Python's version changed, and installs requirements.txt again whenever that file changes.
set -e
cd "$(dirname "$0")"
py=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
venv_py=$(sed -n 's/^version[_a-z]* *= *\([0-9]*\.[0-9]*\).*/\1/p' .venv/pyvenv.cfg 2>/dev/null | head -n 1)
if [ ! -x .venv/bin/python ] || [ "$venv_py" != "$py" ]; then
    echo "Creating the virtual environment for Python $py..."
    python3 -m venv --clear .venv
fi
req=$(cksum < requirements.txt)
if [ "$(cat .venv/.requirements 2>/dev/null)" != "$req" ]; then
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
    echo "$req" > .venv/.requirements
fi
# ROS setups export PYTHONPATH with unrelated packages; keep the app isolated.
PYTHONPATH= exec .venv/bin/python -m app "$@"
