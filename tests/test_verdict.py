import unittest

from probe.verdict import (
    has_reasoning,
    match_official_example,
    official_reply_spec,
    refine_effect,
    similar_thinking,
    verdict_expect_reject,
    verdict_http,
)


def _shot(*, status=200, reasoning=None, tokens=None, content="ok"):
    usage = None
    if tokens is not None:
        usage = {"completion_tokens_details": {"reasoning_tokens": tokens}}
    return {
        "http_status": status,
        "reasoning": reasoning,
        "reasoning_content": reasoning,
        "content": content,
        "usage": usage,
    }


class TestVerdict(unittest.TestCase):
    def test_reject_4xx_pass_5xx_fail_200_fail(self) -> None:
        self.assertEqual(verdict_expect_reject(400)[0], "pass")
        self.assertEqual(verdict_expect_reject(500)[0], "fail")
        v, n = verdict_http("reject", 200, what="temperature=0.2")
        self.assertEqual(v, "fail")
        self.assertIn("應拒", n)

    def test_accept_4xx_is_skip(self) -> None:
        self.assertEqual(verdict_http("accept", 400, what="x")[0], "skip")
        self.assertEqual(verdict_http("effect", 200, what="x")[0], "pass")

    def test_similar_thinking_token_threshold(self) -> None:
        a = _shot(reasoning="x", tokens=100)
        b = _shot(reasoning="x", tokens=120)
        self.assertTrue(similar_thinking(a, b)["similar"])
        c = _shot(reasoning="x", tokens=200)
        self.assertFalse(similar_thinking(a, c)["similar"])

    def test_refine_has_reasoning_and_disable(self) -> None:
        baseline = _shot(reasoning="think")
        on = _shot(reasoning="think")
        off = _shot(reasoning=None, content="ans")
        v, _, _ = refine_effect(
            effect="has_reasoning",
            disable=False,
            can_disable=True,
            value=True,
            default=False,
            result=on,
            baseline=baseline,
            group_results={},
        )
        self.assertEqual(v, "pass")
        v, _, _ = refine_effect(
            effect="no_reasoning",
            disable=True,
            can_disable=True,
            value=False,
            default=False,
            result=off,
            baseline=baseline,
            group_results={},
        )
        self.assertEqual(v, "pass")
        v, n, _ = refine_effect(
            effect="no_reasoning",
            disable=True,
            can_disable=False,
            value="none",
            default=None,
            result=on,
            baseline=baseline,
            group_results={},
        )
        self.assertEqual(v, "fail")
        self.assertIn("不允許關", n)

    def test_refine_rank_like_default_is_noop(self) -> None:
        baseline = _shot(reasoning="t", tokens=100)
        same = _shot(reasoning="t", tokens=100)
        v, _, compare = refine_effect(
            effect="reasoning_tokens_rank",
            disable=False,
            can_disable=False,
            value="max",
            default="max",
            result=same,
            baseline=baseline,
            group_results={"max": same},
        )
        self.assertEqual(v, "noop")
        assert compare is not None
        self.assertEqual(compare["vs_default"], "like_default")
        different = _shot(reasoning="t" * 80, tokens=400)
        v, _, compare = refine_effect(
            effect="reasoning_tokens_rank",
            disable=False,
            can_disable=False,
            value="low",
            default="max",
            result=different,
            baseline=baseline,
            group_results={"max": same, "low": different},
        )
        self.assertEqual(v, "pass")
        assert compare is not None
        self.assertEqual(compare["vs_default"], "not_default")

    def test_has_reasoning_accepts_either_field(self) -> None:
        self.assertTrue(has_reasoning({"reasoning": "a", "reasoning_content": None}))
        self.assertTrue(has_reasoning({"reasoning": None, "reasoning_content": "b"}))
        self.assertFalse(has_reasoning({"reasoning": None, "reasoning_content": None}))

    def test_official_example_shape_pass_and_alias(self) -> None:
        spec = {
            "id": "ex",
            "source": "https://example.test/docs",
            "message_fields": ["content", "reasoning_content"],
            "expect_http": 200,
        }
        aliased = {
            "http_status": 200,
            "reasoning": "think",
            "reasoning_content": None,
            "content": "ans",
            "message_keys": ["content", "reasoning"],
        }
        v, n, ev = match_official_example(aliased, spec)
        self.assertEqual(v, "pass")
        self.assertTrue(ev["matches_example"])
        self.assertEqual(ev["proof"], "response_shape")
        self.assertTrue(ev["shape"]["reasoning_content"]["alias_accepted"])
        self.assertIn("寫死的回覆欄位", n)
        self.assertEqual(ev["proof_layer"], "docs_response_fields")

        miss = match_official_example(_shot(reasoning=None, content="ans"), spec)
        self.assertEqual(miss[0], "fail")
        self.assertFalse(miss[2]["matches_example"])
        self.assertEqual(miss[2]["proof"], "shape_mismatch")

        http_miss = match_official_example(_shot(status=400, content="ans"), spec)
        self.assertEqual(http_miss[0], "fail")
        self.assertEqual(http_miss[2]["proof"], "http_mismatch")

    def test_official_absent_fields_fail_when_reasoning_present(self) -> None:
        spec = {
            "id": "off",
            "message_fields": ["content"],
            "absent_fields": ["reasoning_content"],
            "expect_http": 200,
        }
        still_thinking = match_official_example(_shot(reasoning="think", content="ans"), spec)
        self.assertEqual(still_thinking[0], "fail")
        self.assertEqual(still_thinking[2]["proof"], "unexpected_field")

        off = match_official_example(_shot(reasoning=None, content="ans"), spec)
        self.assertEqual(off[0], "pass")
        self.assertTrue(off[2]["absent"]["reasoning_content"]["ok"])

    def test_official_reply_spec_is_docs_fields_not_essay(self) -> None:
        from probe.cases import ProbeCase

        def _case(**kwargs):
            base = dict(
                case_id="A.enable_thinking_false",
                step="A",
                desc="off",
                extra={"chat_template_kwargs": {"enable_thinking": False}},
                expect="effect",
                effect="no_reasoning",
                param_name="chat_template_kwargs.enable_thinking",
                value=False,
                in_official=True,
                enforce_local=False,
                claimed=True,
                disable=True,
            )
            base.update(kwargs)
            return ProbeCase(**base)

        off = official_reply_spec(_case(), source="https://example.test/docs")
        self.assertEqual(off["message_fields"], ["content"])
        self.assertEqual(off["absent_fields"], ["reasoning_content"])
        self.assertEqual(off["proof_layer"], "docs_response_fields")

        combo_off = official_reply_spec(
            _case(
                case_id="F.think_off_stream",
                step="F",
                extra={"chat_template_kwargs": {"enable_thinking": False}},
                effect="official_shape",
                disable=True,
            ),
            source="https://example.test/docs",
        )
        self.assertEqual(combo_off["absent_fields"], ["reasoning_content"])

        on = official_reply_spec(
            _case(
                case_id="A.effort_high",
                extra={"reasoning_effort": "high"},
                effect="reasoning_tokens_rank",
                param_name="reasoning_effort",
                value="high",
                disable=False,
            ),
            source="https://example.test/docs",
        )
        self.assertEqual(on["message_fields"], ["content", "reasoning_content"])
        self.assertNotIn("absent_fields", on)

        example = official_reply_spec(
            _case(
                official={
                    "id": "ex",
                    "prompt": "What is the water formula?",
                    "message_fields": ["content", "reasoning_content"],
                    "absent_fields": [],
                }
            ),
            source="https://ignored.test",
        )
        self.assertEqual(example["prompt"], "What is the water formula?")
        self.assertEqual(example["id"], "ex")


if __name__ == "__main__":
    unittest.main()
