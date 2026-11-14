from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_factory_server.domain.schemas import ContextPolicy
from agent_factory_server.runtime.context import (
    estimate_messages_tokens,
    estimate_tokens,
    project_run_items,
    resolve_context_policy,
    truncate_tool_content,
)


def _item(**kwargs):
    defaults = {
        "id": "i1",
        "run_id": "r1",
        "session_id": "s1",
        "seq": 1,
        "type": "user_message",
        "call_id": None,
        "name": None,
        "content": "",
        "payload_json": None,
        "visibility": "user",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class ContextHelpersTest(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("abcd", 4.0), 1)
        self.assertEqual(estimate_tokens("abcdefgh", 4.0), 2)

    def test_truncate_tool_content(self):
        text = "x" * 5000
        out, truncated = truncate_tool_content(text, 200)
        self.assertTrue(truncated)
        self.assertLess(len(out), len(text))
        self.assertIn("truncated", out)

    def test_resolve_context_policy_legacy(self):
        policy = resolve_context_policy(3000, ContextPolicy())
        self.assertEqual(policy.tool_result_max_chars, 3000)

    def test_project_fold_tools_skips_raw_results(self):
        import json

        items = [
            _item(type="user_message", content="list files", seq=1),
            _item(
                type="assistant_message",
                content=None,
                seq=2,
                payload_json=json.dumps(
                    {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "list_dir", "arguments": "{}"},
                            }
                        ]
                    }
                ),
            ),
            _item(
                type="tool_result",
                call_id="c1",
                name="list_dir",
                content='{"ok":true,"data":"' + ("y" * 5000) + '"}',
                seq=3,
            ),
            _item(type="assistant_message", content="done", seq=4, payload_json="{}"),
        ]
        folded = project_run_items(items, fold_tools=True, tool_result_max_chars=100)
        roles = [m["role"] for m in folded]
        self.assertEqual(roles, ["user", "assistant", "assistant"])
        self.assertIn("compacted", folded[1]["content"])
        self.assertNotIn("tool", roles)

    def test_project_l1_truncates_tool_result(self):
        items = [
            _item(type="user_message", content="hi", seq=1),
            _item(
                type="assistant_message",
                content=None,
                seq=2,
                payload_json='{"tool_calls":[{"id":"c1","type":"function","function":{"name":"t","arguments":"{}"}}]}',
            ),
            _item(
                type="tool_result",
                call_id="c1",
                name="t",
                content="z" * 500,
                seq=3,
            ),
        ]
        msgs = project_run_items(items, fold_tools=False, tool_result_max_chars=80)
        tool_msg = next(m for m in msgs if m["role"] == "tool")
        self.assertLess(len(tool_msg["content"]), 500)
        self.assertIn("truncated", tool_msg["content"])

    def test_estimate_messages_tokens_positive(self):
        n = estimate_messages_tokens(
            [{"role": "system", "content": "hello world"}], chars_per_token=4.0
        )
        self.assertGreater(n, 0)

    def test_project_skill_body_not_truncated_at_generic_cap(self):
        # Regression: a 14.6KB SKILL.md was fragmented by the 12000-char cap.
        body = "STEP " + ("s" * 15000) + " FINAL_STEP"
        items = [
            _item(type="user_message", content="hi", seq=1),
            _item(
                type="assistant_message",
                content=None,
                seq=2,
                payload_json='{"tool_calls":[{"id":"c1","type":"function","function":{"name":"skill_load","arguments":"{}"}}]}',
            ),
            _item(
                type="tool_result",
                call_id="c1",
                name="skill_load",
                content=body,
                seq=3,
            ),
        ]
        msgs = project_run_items(items, fold_tools=False, tool_result_max_chars=12000)
        tool_msg = next(m for m in msgs if m["role"] == "tool")
        self.assertEqual(tool_msg["content"], body)
        self.assertNotIn("truncated", tool_msg["content"])

    def test_project_skill_body_truncates_head_only_with_paging_hint(self):
        body = "HEAD_MARKER" + ("m" * 5000) + "TAIL_MARKER"
        items = [
            _item(type="user_message", content="hi", seq=1),
            _item(
                type="assistant_message",
                content=None,
                seq=2,
                payload_json='{"tool_calls":[{"id":"c1","type":"function","function":{"name":"skill_load","arguments":"{}"}}]}',
            ),
            _item(
                type="tool_result",
                call_id="c1",
                name="skill_load",
                content=body,
                seq=3,
            ),
        ]
        msgs = project_run_items(items, fold_tools=False, skill_result_max_chars=1000)
        tool_msg = next(m for m in msgs if m["role"] == "tool")
        content = tool_msg["content"]
        self.assertTrue(content.startswith("HEAD_MARKER"))
        # Head-only: the tail must not be spliced in as if it were contiguous.
        self.assertNotIn("TAIL_MARKER", content)
        self.assertIn("skill body truncated", content)
        self.assertIn("skill_read", content)
        self.assertIn("next_offset", content)

    def test_project_skill_read_uses_skill_cap(self):
        body = "r" * 15000
        items = [
            _item(type="user_message", content="hi", seq=1),
            _item(
                type="assistant_message",
                content=None,
                seq=2,
                payload_json='{"tool_calls":[{"id":"c1","type":"function","function":{"name":"skill_read","arguments":"{}"}}]}',
            ),
            _item(
                type="tool_result",
                call_id="c1",
                name="skill_read",
                content=body,
                seq=3,
            ),
        ]
        msgs = project_run_items(items, fold_tools=False, tool_result_max_chars=12000)
        tool_msg = next(m for m in msgs if m["role"] == "tool")
        self.assertEqual(tool_msg["content"], body)

    def test_context_policy_skill_cap_default(self):
        policy = ContextPolicy()
        self.assertEqual(policy.tool_result_max_chars, 12000)
        self.assertEqual(policy.skill_result_max_chars, 40000)
        self.assertGreater(policy.skill_result_max_chars, policy.tool_result_max_chars)



if __name__ == "__main__":
    unittest.main()
