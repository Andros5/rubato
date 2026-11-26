"""Double-click this file from a Windows checkout to open the local launcher."""

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
if pythonw.exists() and Path(sys.executable).resolve() != pythonw.resolve():
    os.execv(str(pythonw), [str(pythonw), str(Path(__file__).resolve())])

sys.path.insert(0, str(root / "src"))
from rubato.desktop import main  # noqa: E402

main(root)
