import pathlib
import unittest

from niri_plus import source_update


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIRST_PIN = "79b093e60f72a2e31f29f819ca2e13d3a1f296e5"
NEXT_PIN = "b440538d342822eac6cb046f0c0dd5e96212d6f2"
CURRENT_PIN = "d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3"


class ReleaseCandidateDocsTests(unittest.TestCase):
    def test_first_and_following_candidate_procedures_keep_distinct_pins(self):
        first = (ROOT / "docs/release-candidate-m1.md").read_text(encoding="utf-8")
        following = (ROOT / "docs/release-candidate-m1-next.md").read_text(encoding="utf-8")
        previous = (ROOT / "docs/release-candidate-m1-pr22.md").read_text(encoding="utf-8")
        current = (ROOT / "docs/M1_FIRST_TEST_FINAL.md").read_text(encoding="utf-8")

        self.assertIn("refs/pull/19/head", first)
        self.assertIn(FIRST_PIN, first)
        self.assertIn("refs/pull/21/head", following)
        self.assertIn(NEXT_PIN, following)
        self.assertIn("refs/pull/22/head", previous)
        for archived in (first, following, previous):
            self.assertIn("Archived procedure", archived)
        self.assertIn(CURRENT_PIN, current)
        self.assertIn("0.1.9 atualmente instalado não tem", current)
        self.assertIn("--dry-run", current)
        self.assertIn("prepara e verifica automaticamente", current)
        self.assertNotIn("sudo niri+ recovery prepare", current)
        self.assertIn("niri+ gaming status", current)
        self.assertIn("--with-quickshell --dry-run", current)
        self.assertIn("--with-quickshell --apply", current)
        self.assertNotIn("```sh\nsudo niri+ install --dry-run", current)
        self.assertIn("0.1.12", (ROOT / "VERSION").read_text(encoding="utf-8"))
        self.assertEqual(source_update.RELEASE_CANDIDATE_PULL_REQUEST, 24)
        self.assertEqual(source_update.RELEASE_CANDIDATE_QUICKSHELL_COMMIT, CURRENT_PIN)
        self.assertEqual(source_update.RELEASE_CANDIDATE_REF, "refs/pull/24/head")


if __name__ == "__main__":
    unittest.main()
