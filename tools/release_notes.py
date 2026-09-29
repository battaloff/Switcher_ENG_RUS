"""Description of a GitHub release: the version's CHANGELOG.md section plus the install note.

    python tools/release_notes.py 0.2.0 > release-notes.md

Fails when the version has no section, so a release cannot go out undocumented.
Everything after the "---" line is for people reading GitHub; the app's updates
page shows only the list above it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^##\s+v?(\d+(?:\.\d+)+)\b")


def section(version: str, changelog: Path = ROOT / "CHANGELOG.md") -> str:
    lines, inside = [], False
    for line in changelog.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if match:
            if inside:
                break
            inside = match.group(1) == version
            continue
        if inside:
            lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return text


def notes(version: str) -> str:
    install = (ROOT / "packaging" / "windows" / "RELEASE_NOTES.md").read_text(encoding="utf-8").strip()
    return f"{section(version)}\n\n---\n\n{install}\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(notes(sys.argv[1].lstrip("v")))
