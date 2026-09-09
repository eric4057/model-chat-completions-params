import unittest
from pathlib import Path

from probe.cases import (
    generate_contract_cases,
    generate_thinking_stream_combos,
    messages_for_case,
    official_scope_cases,
)
from probe.profile import load_profiles, match_profile, parse_profile, unknown_profile

MODELS = Path(__file__).resolve().parent.parent / "configs" / "models"


def _profile(model_id: str):
    return match_profile(model_id, load_profiles(MODELS))


def _by_id(cases):
    return {c.case_id: c for c in cases}


class TestCaseGenerator(unittest.TestCase):
    def test_kimi_generates_effort_locked_forbidden_echo(self) -> None:
        ids = _by_id(generate_contract_cases(_profile("Kimi-K3"), probe_mode="probe"))
        self.assertEqual(ids["A.effort_low"].expect, "effect")
        self.assertEqual(ids["A.effort_high"].extra, {"reasoning_effort": "high"})
        self.assertEqual(ids["A.effort_max"].default, "max")
        self.assertEqual(ids["A.effort_none"].expect, "reject")
        self.assertEqual(ids["A.effort_medium"].expect, "reject")
        self.assertEqual(ids["A.effort_invalid"].expect, "reject")
        self.assertEqual(ids["A.effort_invalid"].extra, {"reasoning_effort": "not-a-level"})
        self.assertEqual(ids["A.forbidden_thinking"].expect, "reject")
        self.assertEqual(
            ids["A.forbidden_thinking"].extra, {"thinking": {"type": "enabled"}}
        )
        self.assertEqual(ids["E.temperature"].expect, "reject")
        self.assertEqual(ids["E.temperature"].extra, {"temperature": 0.2})
        self.assertEqual(ids["E.top_p"].extra, {"top_p": 0.1})
        self.assertEqual(ids["A.multiturn"].expect, "effect")
        self.assertFalse(ids["A.effort_low"].enforce_local)

    def test_kimi_enforce_blocks_unofficial_locally(self) -> None:
        ids = _by_id(generate_contract_cases(_profile("Kimi-K3"), probe_mode="enforce"))
        self.assertNotIn("A.effort_low", ids)
        self.assertTrue(ids["A.effort_none"].enforce_local)
        self.assertTrue(ids["A.effort_invalid"].enforce_local)
        self.assertTrue(ids["E.temperature"].enforce_local)
        self.assertTrue(ids["A.forbidden_thinking"].enforce_local)

    def test_effort_override_only_sends_asked_official_values(self) -> None:
        ids = _by_id(
            generate_contract_cases(
                _profile("Kimi-K3"),
                probe_mode="probe",
                effort_override=["low", "medium"],
            )
        )
        self.assertIn("A.effort_low", ids)
        self.assertNotIn("A.effort_high", ids)
        self.assertEqual(ids["A.effort_medium"].expect, "reject")

    def test_gemma_official_is_enable_thinking_only(self) -> None:
        ids = _by_id(
            generate_contract_cases(_profile("gemma-4-31B-it"), probe_mode="probe")
        )
        self.assertEqual(
            ids["A.enable_thinking_true"].extra,
            {"chat_template_kwargs": {"enable_thinking": True}},
        )
        self.assertEqual(ids["A.enable_thinking_true"].effect, "has_reasoning")
        self.assertTrue(ids["A.enable_thinking_false"].disable)
        self.assertEqual(ids["A.enable_thinking_false"].effect, "no_reasoning")
        self.assertNotIn("A.effort_none", ids)
        self.assertNotIn("A.effort_low", ids)
        self.assertNotIn("A.include_reasoning_false", ids)
        self.assertNotIn("E.temperature", ids)

    def test_nemotron_int_range_and_gpt_oss_enum(self) -> None:
        nemo = _by_id(
            generate_contract_cases(_profile("nemotron-3-ultra"), probe_mode="probe")
        )
        self.assertEqual(nemo["A.reasoning_budget_1024"].expect, "effect")
        self.assertEqual(nemo["A.reasoning_budget_neg1"].expect, "effect")
        self.assertEqual(nemo["A.reasoning_budget_999999_reject"].expect, "reject")
        self.assertTrue(nemo["A.effort_none"].disable)

        oss = _by_id(
            generate_contract_cases(_profile("gpt-oss-120b"), probe_mode="probe")
        )
        self.assertTrue(
            {
                "A.effort_low",
                "A.effort_medium",
                "A.effort_high",
                "A.effort_none",
                "A.effort_max",
                "A.effort_invalid",
            }
            <= set(oss)
        )
        self.assertEqual(oss["A.effort_none"].expect, "reject")

    def test_glm52_medium_is_official_not_reject(self) -> None:
        ids = _by_id(generate_contract_cases(_profile("GLM-5.2"), probe_mode="probe"))
        self.assertEqual(ids["A.effort_medium"].expect, "effect")
        self.assertEqual(ids["A.effort_none"].expect, "effect")
        self.assertTrue(ids["A.effort_none"].disable)
        self.assertTrue(ids["A.effort_minimal"].disable)
        self.assertEqual(ids["A.effort_xhigh"].expect, "effect")

    def test_kimi_official_example_uses_docs_prompt(self) -> None:
        ids = _by_id(generate_contract_cases(_profile("Kimi-K3"), probe_mode="probe"))
        case = ids["A.official_reasoning-effort-high"]
        self.assertEqual(case.effect, "official_shape")
        self.assertTrue(case.force_non_stream)
        self.assertIn("Derive the general formula", case.prompt or "")
        self.assertEqual(case.extra, {"reasoning_effort": "high"})
        assert case.official is not None
        self.assertEqual(
            case.official["source"],
            "https://platform.kimi.ai/docs/guide/use-reasoning-effort",
        )

    def test_official_scope_keeps_special_params_drops_echo(self) -> None:
        cases = generate_contract_cases(_profile("Kimi-K3"), probe_mode="probe")
        ids = {c.case_id for c in official_scope_cases(cases)}
        self.assertIn("A.effort_high", ids)
        self.assertIn("A.official_reasoning-effort-high", ids)
        self.assertIn("E.temperature", ids)
        self.assertNotIn("A.multiturn", ids)

    def test_messages_for_case_keeps_official_prompt_and_unique_nonce(self) -> None:
        ids = _by_id(generate_contract_cases(_profile("Kimi-K3"), probe_mode="probe"))
        official = messages_for_case("ignored", ids["A.official_reasoning-effort-high"])
        self.assertEqual(
            official[0]["content"],
            "Derive the general formula for this sequence: 1, 4, 9, 25, 64, ...",
        )
        a = messages_for_case("題目", ids["A.effort_low"])
        b = messages_for_case("題目", ids["A.effort_high"])
        self.assertIn("A.effort_low", a[0]["content"])
        self.assertIn("A.effort_high", b[0]["content"])
        self.assertNotEqual(a[0]["content"], b[0]["content"])
        self.assertTrue(a[0]["content"].startswith("題目"))

    def test_minimax_m2_disabled_still_thinks(self) -> None:
        ids = _by_id(
            generate_contract_cases(_profile("MiniMax-M2.7"), probe_mode="probe")
        )
        self.assertEqual(ids["A.type_disabled"].expect, "effect")
        self.assertFalse(ids["A.type_disabled"].disable)
        self.assertEqual(ids["A.type_disabled"].effect, "has_reasoning")

    def test_thinking_stream_combo_uses_family_switch(self) -> None:
        gemma = _by_id(generate_thinking_stream_combos(_profile("gemma-4-31B-it")))
        self.assertEqual(
            set(gemma),
            {
                "F.think_on_stream",
                "F.think_on_sync",
                "F.think_off_stream",
                "F.think_off_sync",
            },
        )
        self.assertEqual(
            gemma["F.think_on_stream"].extra,
            {"chat_template_kwargs": {"enable_thinking": True}},
        )
        self.assertTrue(gemma["F.think_on_stream"].stream_override)
        self.assertFalse(gemma["F.think_on_sync"].stream_override)
        self.assertTrue(gemma["F.think_off_sync"].disable)
        self.assertFalse(gemma["F.think_on_stream"].disable)

        kimi = _by_id(generate_thinking_stream_combos(_profile("Kimi-K3")))
        self.assertEqual(set(kimi), {"F.think_on_stream", "F.think_on_sync"})
        self.assertEqual(kimi["F.think_on_sync"].extra, {"reasoning_effort": "max"})

        m3 = _by_id(generate_thinking_stream_combos(_profile("MiniMax-M3")))
        self.assertEqual(m3["F.think_off_stream"].extra, {"thinking": {"type": "disabled"}})
        self.assertTrue(m3["F.think_off_stream"].disable)
        self.assertEqual(m3["F.think_on_sync"].extra, {"thinking": {"type": "adaptive"}})

        m2 = _by_id(generate_thinking_stream_combos(_profile("MiniMax-M2.7")))
        self.assertFalse(m2["F.think_off_sync"].disable)
        self.assertEqual(m2["F.think_off_sync"].extra, {"thinking": {"type": "disabled"}})

        oss = _by_id(generate_thinking_stream_combos(_profile("gpt-oss-120b")))
        self.assertEqual(set(oss), {"F.think_on_stream", "F.think_on_sync"})
        self.assertEqual(oss["F.think_on_stream"].extra, {"reasoning_effort": "medium"})

        ultra = _by_id(
            generate_thinking_stream_combos(_profile("NVIDIA-Nemotron-3-Ultra-550B-A55B"))
        )
        self.assertEqual(ultra["F.think_on_sync"].extra, {"reasoning_effort": "high"})
        self.assertEqual(ultra["F.think_off_stream"].extra, {"reasoning_effort": "none"})
        self.assertTrue(ultra["F.think_off_stream"].disable)

        glm = _by_id(generate_thinking_stream_combos(_profile("GLM-5.2")))
        self.assertEqual(glm["F.think_on_sync"].extra, {"thinking": {"type": "enabled"}})
        self.assertNotIn("reasoning_effort", glm["F.think_on_sync"].extra)

        self.assertEqual(generate_thinking_stream_combos(unknown_profile()), [])
        gemma3 = generate_thinking_stream_combos(_profile("Google-Gemma-3-27B"))
        self.assertEqual(gemma3, [])

    def test_official_scope_drops_thinking_stream_combo(self) -> None:
        mixed = generate_contract_cases(_profile("gemma-4-31B-it"), probe_mode="probe")
        mixed.extend(generate_thinking_stream_combos(_profile("gemma-4-31B-it")))
        ids = {c.case_id for c in official_scope_cases(mixed)}
        self.assertNotIn("F.think_on_stream", ids)
        self.assertIn("A.enable_thinking_true", ids)
        self.assertEqual(
            generate_contract_cases(unknown_profile(), probe_mode="probe"), []
        )

    def test_new_family_json_needs_no_code_change(self) -> None:
        profile = parse_profile(
            {
                "id": "deepseek-v4",
                "family": "DeepSeek V4",
                "known": True,
                "match": {"id_prefix": ["deepseek"]},
                "params": [
                    {
                        "name": "reasoning_effort",
                        "kind": "enum",
                        "values": ["high", "max"],
                        "default": "high",
                        "reject": ["none"],
                        "effect": "reasoning_tokens_rank",
                    },
                    {
                        "name": "thinking",
                        "kind": "forbidden",
                        "probe_value": {"type": "disabled"},
                    },
                ],
            },
            origin="memory",
        )
        ids = {c.case_id: c for c in generate_contract_cases(profile, probe_mode="probe")}
        self.assertEqual(ids["A.effort_high"].expect, "effect")
        self.assertEqual(ids["A.effort_max"].extra, {"reasoning_effort": "max"})
        self.assertEqual(ids["A.effort_none"].expect, "reject")
        self.assertEqual(
            ids["A.forbidden_thinking"].extra, {"thinking": {"type": "disabled"}}
        )


if __name__ == "__main__":
    unittest.main()
