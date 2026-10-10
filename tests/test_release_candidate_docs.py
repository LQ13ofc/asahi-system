import pathlib
import unittest

from niri_plus import source_update


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIRST_PIN = "79b093e60f72a2e31f29f819ca2e13d3a1f296e5"
NEXT_PIN = "b440538d342822eac6cb046f0c0dd5e96212d6f2"
CURRENT_PIN = "fadf99f1e95dd8763082b7553fb55216e42121a3"


class ReleaseCandidateDocsTests(unittest.TestCase):
    def test_first_and_following_candidate_procedures_keep_distinct_pins(self):
        first = (ROOT / "docs/release-candidate-m1.md").read_text(encoding="utf-8")
        following = (ROOT / "docs/release-candidate-m1-next.md").read_text(encoding="utf-8")
        current = (ROOT / "docs/release-candidate-m1-pr22.md").read_text(encoding="utf-8")

        self.assertIn("refs/pull/19/head", first)
        self.assertIn(FIRST_PIN, first)
        self.assertIn("refs/pull/21/head", following)
        self.assertIn(NEXT_PIN, following)
        self.assertIn("refs/pull/22/head", current)
        self.assertIn(CURRENT_PIN, current)
        self.assertIn("niri+ gaming status", current)
        self.assertIn("Gamescope-only launcher/no-fallback contract", current)
        self.assertEqual(source_update.RELEASE_CANDIDATE_PULL_REQUEST, 22)
        self.assertEqual(source_update.RELEASE_CANDIDATE_QUICKSHELL_COMMIT, CURRENT_PIN)
        self.assertEqual(source_update.RELEASE_CANDIDATE_REF, "refs/pull/22/head")


if __name__ == "__main__":
    unittest.main()
