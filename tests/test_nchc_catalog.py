import json
import unittest
from pathlib import Path

from probe.profile import load_profiles, match_profile

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "configs" / "models"
CATALOG = MODELS / "_nchc_catalog.json"


class TestNchcCatalog(unittest.TestCase):
    def test_catalog_covers_105_gateway_ids(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        chat = [m for ids in catalog["chat"].values() for m in ids]
        non_chat = [m for ids in catalog["non_chat"].values() for m in ids]
        self.assertEqual(len(chat), 78)
        self.assertEqual(len(non_chat), 27)
        self.assertEqual(len(set(chat + non_chat)), 105)

    def test_every_chat_id_matches_its_family(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        profiles = load_profiles(MODELS)
        mismatches = []
        for family, ids in catalog["chat"].items():
            for model_id in ids:
                got = match_profile(model_id, profiles).id
                if got != family:
                    mismatches.append((model_id, family, got))
        self.assertEqual(mismatches, [])

    def test_non_chat_ids_are_not_forced_into_chat_families(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        profiles = load_profiles(MODELS)
        chat_families = set(catalog["chat"])
        leaked = []
        for ids in catalog["non_chat"].values():
            for model_id in ids:
                got = match_profile(model_id, profiles).id
                if got in chat_families:
                    leaked.append((model_id, got))
        self.assertEqual(leaked, [])

    def test_nchc_gateway_aliases_hit_existing_contracts(self) -> None:
        profiles = load_profiles(MODELS)
        self.assertEqual(
            match_profile("NVIDIA-Nemotron-3-Ultra-550B-A55B", profiles).id,
            "nemotron-3-ultra",
        )
        self.assertEqual(match_profile("gpt-oss-20b", profiles).id, "gpt-oss-120b")
        self.assertEqual(match_profile("Google-Gemma-3-27B", profiles).id, "gemma-3")
        self.assertEqual(match_profile("MiniMax-M3", profiles).id, "minimax-m3")
        self.assertEqual(match_profile("MiniMax-M2.7", profiles).id, "minimax-m2")
        self.assertEqual(match_profile("GLM-5.2", profiles).id, "glm-5")
        self.assertEqual(
            match_profile("NVIDIA-Nemotron-3-Super-120B-A12B", profiles).id,
            "nemotron-3-super",
        )
        self.assertEqual(
            match_profile("Phi-4-Reasoning-Plus", profiles).id, "phi-4-reasoning"
        )
        self.assertNotEqual(
            match_profile("Phi-4-Reasoning-Plus", profiles).id, "phi-instruct"
        )


if __name__ == "__main__":
    unittest.main()
