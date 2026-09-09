import unittest
from pathlib import Path

from probe.profile import load_profiles, match_profile, unknown_profile

MODELS = Path(__file__).resolve().parent.parent / "configs" / "models"


class TestProfileMatch(unittest.TestCase):
    def test_load_includes_core_families(self) -> None:
        profiles = load_profiles(MODELS)
        ids = {p.id for p in profiles}
        self.assertTrue({"kimi-k3", "gemma-4", "nemotron-3-ultra", "gpt-oss-120b"} <= ids)

    def test_match_basename_and_provider_prefix(self) -> None:
        profiles = load_profiles(MODELS)
        self.assertEqual(match_profile("gemma-4-31B-it", profiles).id, "gemma-4")
        self.assertEqual(match_profile("google/gemma-4-31B-it", profiles).id, "gemma-4")
        self.assertEqual(match_profile("Kimi-K3", profiles).id, "kimi-k3")
        self.assertEqual(match_profile("Kimi-K3-low", profiles).id, "kimi-k3")
        self.assertEqual(match_profile("hosted_vllm/gpt-oss-120b", profiles).id, "gpt-oss-120b")

    def test_match_does_not_use_loose_substring(self) -> None:
        profiles = load_profiles(MODELS)
        self.assertEqual(match_profile("not-gemma-4-at-all", profiles).id, "unknown")
        self.assertEqual(match_profile("llama-3-70b", profiles).id, unknown_profile().id)

    def test_longer_prefix_wins(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "short.json").write_text(
                """
                {
                  "id": "gpt-5",
                  "family": "short",
                  "known": true,
                  "match": {"id_prefix": ["gpt-5"]},
                  "params": []
                }
                """,
                encoding="utf-8",
            )
            (tmp / "long.json").write_text(
                """
                {
                  "id": "gpt-5-mini",
                  "family": "long",
                  "known": true,
                  "match": {"id_prefix": ["gpt-5-mini"]},
                  "params": []
                }
                """,
                encoding="utf-8",
            )
            profiles = load_profiles(tmp)
            self.assertEqual(match_profile("gpt-5-mini-2025", profiles).id, "gpt-5-mini")
            self.assertEqual(match_profile("gpt-5", profiles).id, "gpt-5")

    def test_profiles_with_params_cite_official_source(self) -> None:
        missing = [
            p.id for p in load_profiles(MODELS) if p.params and not p.source
        ]
        self.assertEqual(missing, [])

    def test_gemma4_source_is_google_not_vllm(self) -> None:
        gemma = match_profile("gemma-4-31B-it", load_profiles(MODELS))
        joined = " ".join(gemma.source)
        self.assertIn("ai.google.dev", joined)
        self.assertNotIn("vllm", joined.lower())


if __name__ == "__main__":
    unittest.main()
