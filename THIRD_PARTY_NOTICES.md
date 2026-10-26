# Third-party notices

Voice Pen Plotter uses the following components. Each keeps its own license; the installers
include their binaries.

| Component | License | Notes |
|---|---|---|
| [Python](https://www.python.org/) | PSF License | Runtime packed into the installers |
| [Qt 6](https://www.qt.io/) / [PySide6](https://doc.qt.io/qtforpython-6/) | LGPL-3.0 | Dynamically linked; replaceable libraries in the program folder |
| [OpenCV](https://opencv.org/) (`opencv-python-headless`) | Apache-2.0 | Includes FFmpeg and other libraries under their own (L)GPL/BSD licenses |
| [NumPy](https://numpy.org/) | BSD-3-Clause | Includes OpenBLAS (BSD) |
| [PyMuPDF](https://pymupdf.readthedocs.io/) / MuPDF | **AGPL-3.0** (or a commercial license from Artifex) | Used to read PDF drawings. Distributing the program to others requires complying with the AGPL (including offering the source) or a commercial license. |
| [pySerial](https://github.com/pyserial/pyserial) | BSD-3-Clause | |
| [SpeechRecognition](https://github.com/Uberi/speech_recognition) | BSD-3-Clause | Includes a FLAC encoder binary (BSD / GPL tools) |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | MIT | |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0 with a bootloader exception | The exception allows packaging programs under any license |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Inno Setup License | Used to build the Windows installer |
| Hershey fonts | See `plotter/text/fonts/NOTICE.txt` | Single-stroke fonts used for text |

The machine runs [GRBL](https://github.com/gnea/grbl) (GPL-3.0) firmware, which is not part of this
software and is never modified or distributed by it.
