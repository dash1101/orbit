"""A release's notes: packaging/release-notes.md with only the downloads this release has.

    python3 tools/release-notes.py <notes.md> <folder with the release's files> [more file names] > notes.md

Macs and ARM computers get a build at milestones (see RELEASING.md), so the download table drops the rows whose file
isn't in this release, and says where those systems find Orbit instead. Names given after the folder count as present
(the Android app, which is signed and added after the build).
"""
import re
import sys
from pathlib import Path

ELSEWHERE = (
    "**Not listed above (Macs and ARM computers)?** This release is for Windows and Linux on x86-64, and Android. "
    "Builds for Macs and ARM come with each milestone release (one whose version ends in .0, like 0.6.0): download Orbit from the newest "
    "[release](https://github.com/dash1101/orbit/releases) that has your file. Orbit installed there updates itself to "
    "each release with a build for it."
)


def notes(text, present):
    out, dropped, in_table = [], False, False
    for line in text.splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 3:
            in_table = True
            name = re.search(r"`(orbit-[^`]+)`", cells[2])
            if name and name.group(1) not in present:
                dropped = True
                continue
        elif in_table:
            in_table = False
            if dropped:
                out += ["", ELSEWHERE]
        out.append(line)
    if in_table and dropped:
        out += ["", ELSEWHERE]
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    present = {p.name for p in Path(sys.argv[2]).iterdir()} | set(sys.argv[3:])
    sys.stdout.write(notes(Path(sys.argv[1]).read_text(encoding="utf-8"), present))


if __name__ == "__main__":
    main()
