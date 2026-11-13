"""Release notes: a release without the Mac and ARM builds doesn't list them, and says where they are."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "tools" / "release-notes.py")
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)
TEXT = (ROOT / "packaging" / "release-notes.md").read_text(encoding="utf-8")
TABLE = re.compile(r"^\|.*`(orbit-[^`]+)`", re.M)
EVERY = set(TABLE.findall(TEXT))
X86 = {"orbit-windows-x64.exe", "orbit-linux-x86_64", "orbit-linux-x86_64.deb", "orbit-android.apk"}


class ReleaseNotesTests(unittest.TestCase):
    def test_every_system_has_a_row(self):
        self.assertEqual(EVERY, X86 | {"orbit-windows-arm64.exe", "orbit-linux-arm64", "orbit-linux-arm64.deb",
                                       "orbit-macos-apple-silicon.dmg", "orbit-macos-intel.dmg"})

    def test_a_release_with_every_build_keeps_the_notes_as_written(self):
        self.assertEqual(rn.notes(TEXT, EVERY), TEXT)

    def test_a_release_for_x86_lists_only_its_files_and_says_where_the_rest_are(self):
        out = rn.notes(TEXT, X86)
        self.assertEqual(set(TABLE.findall(out)), X86)
        self.assertEqual(out.count(rn.ELSEWHERE), 1)
        # right after the table, before the next paragraph
        self.assertRegex(out, r"\| `orbit-android\.apk` \|[^\n]*\n\n" + re.escape(rn.ELSEWHERE) + r"\n\n\*\*First launch")


if __name__ == "__main__":
    unittest.main()
