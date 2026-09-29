"""releases.json for a new release: every published version plus this one.

    gh api "repos/$GITHUB_REPOSITORY/releases?per_page=100" > published.json
    python tools/release_manifest.py 0.2.1 dist/SwitcherSetup-0.2.1.exe notes.md published.json owner/repo > releases.json

The app reads this file from github.com/<repo>/releases/latest/download/releases.json,
which has no request quota, instead of the rate-limited REST API.  The script
checks that the app's own parser reads it back with the right version, size
and checksum.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from switcher import updater  # noqa: E402


def build(version: str, installer: Path, notes: str, published: list[dict], repo: str) -> dict:
    sha = hashlib.sha256(installer.read_bytes()).hexdigest()
    tag = f"v{version}"
    new = {
        "tag_name": tag, "name": f"Switcher {version}", "body": notes, "draft": False, "prerelease": False,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
        "assets": [{"name": installer.name, "size": installer.stat().st_size, "digest": f"sha256:{sha}",
                    "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{installer.name}"}],
    }
    result = updater.manifest([new] + [r for r in published if r.get("tag_name") != tag])
    first = updater.parse_releases(result["releases"])[0]
    assert (first.version, first.size, first.sha256) == (version, installer.stat().st_size, sha), first
    return result


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    version, installer, notes, published, repo = sys.argv[1:]
    data = build(version.lstrip("v"), Path(installer), Path(notes).read_text(encoding="utf-8"),
                 json.loads(Path(published).read_text(encoding="utf-8")), repo)
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
    versions = ", ".join(r["tag_name"] for r in data["releases"])
    print(f"releases.json: {versions}", file=sys.stderr)


if __name__ == "__main__":
    main()
