#!/usr/bin/env python3
"""Bump the patch version and append a release entry."""

from datetime import date
from pathlib import Path
import json
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "version.py"
RELEASES_FILE = ROOT / "releases" / "index.json"
VERSION_PATTERN = re.compile(r'(__version__\s*=\s*["\'])(\d+)\.(\d+)\.(\d+)(["\'])')


def main() -> None:
    changed_files = subprocess.check_output(
        ["git", "diff", "--cached", "--name-status"], text=True, cwd=ROOT
    ).splitlines()
    summary = [
        f"{status}: {path}"
        for status, path in (line.split("\t", 1) for line in changed_files)
        if path not in {"version.py", "releases/index.json"}
    ] or ["No source file changes recorded."]

    version_text = VERSION_FILE.read_text()
    match = VERSION_PATTERN.search(version_text)
    if match is None:
        raise RuntimeError(f"Could not find a semantic version in {VERSION_FILE}")
    major, minor, patch = (int(value) for value in match.group(2, 3, 4))
    old_version = f"{major}.{minor}.{patch}"
    new_version = f"{major}.{minor}.{patch + 1}"
    VERSION_FILE.write_text(VERSION_PATTERN.sub(rf"\g<1>{new_version}\g<5>", version_text, count=1))

    releases = json.loads(RELEASES_FILE.read_text())
    releases["releases"].insert(0, {
        "version": new_version,
        "date": date.today().isoformat(),
        "summary": summary,
    })
    RELEASES_FILE.write_text(f"{json.dumps(releases, indent=2)}\n")
    print(f"Bumped AuraCall from {old_version} to {new_version}")


if __name__ == "__main__":
    main()