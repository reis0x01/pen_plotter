#!/usr/bin/env bash
# Adds Voice Pen Plotter to the applications menu (for this user). Run it from the unpacked
# VoicePenPlotter folder; keep the folder where it is afterwards.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/voice-pen-plotter.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Voice Pen Plotter
Comment=Speech, text, pictures and drawings to a GRBL pen plotter - by MOHAMED OSSAMA ELIWA
Exec="$here/VoicePenPlotter"
Icon=$here/icon.png
Terminal=false
Categories=Graphics;Engineering;
EOF
echo "Added 'Voice Pen Plotter' to the applications menu."
echo "For the USB serial port: sudo usermod -aG dialout \$USER (then log out and in)."
