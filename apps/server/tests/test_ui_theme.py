from __future__ import annotations

import unittest

from agent_factory_server.runtime.ui_theme import (
    normalize_ui_theme,
    theme_to_css_vars,
)


class UiThemeTest(unittest.TestCase):
    def test_keeps_safe_colors_and_lengths(self):
        theme = normalize_ui_theme(
            {
                "bg": "#0f1115",
                "accent": "rgba(37, 99, 235, 0.9)",
                "radiusLg": "20px",
                "evil": "red; background:url(x)",
                "text": "javascript:alert(1)",
            }
        )
        self.assertEqual(theme["bg"], "#0f1115")
        self.assertEqual(theme["accent"], "rgba(37, 99, 235, 0.9)")
        self.assertEqual(theme["radiusLg"], "20px")
        self.assertNotIn("evil", theme)
        self.assertNotIn("text", theme)

    def test_dark_preset_expands(self):
        theme = normalize_ui_theme({"preset": "dark", "accent": "#2563eb"})
        self.assertEqual(theme["preset"], "dark")
        self.assertEqual(theme["bg"], "#0f1115")
        self.assertEqual(theme["accent"], "#2563eb")

    def test_css_var_map(self):
        vars_out = theme_to_css_vars({"bg": "#111", "userBubble": "#222"})
        self.assertEqual(vars_out["--af-bg"], "#111")
        self.assertEqual(vars_out["--af-user-bubble"], "#222")
        self.assertNotIn("--af-text", vars_out)

    def test_rejects_injection(self):
        theme = normalize_ui_theme(
            {
                "bg": "#fff;}",
                "surface": "url(https://evil)",
                "text": "<script>",
            }
        )
        self.assertEqual(theme, {})

    def test_message_and_composer_tokens(self):
        theme = normalize_ui_theme(
            {
                "userBubble": "#2563eb",
                "userBubbleText": "#fff",
                "userBubbleBorder": "transparent",
                "assistantBubble": "#1a1d24",
                "composerCard": "#111",
                "composerSendBg": "#3b82f6",
            }
        )
        vars_out = theme_to_css_vars(theme)
        self.assertEqual(vars_out["--af-user-bubble"], "#2563eb")
        self.assertEqual(vars_out["--af-user-bubble-text"], "#fff")
        self.assertEqual(vars_out["--af-user-bubble-border"], "transparent")
        self.assertEqual(vars_out["--af-assistant-bubble"], "#1a1d24")
        self.assertEqual(vars_out["--af-composer-card"], "#111")
        self.assertEqual(vars_out["--af-composer-send-bg"], "#3b82f6")


if __name__ == "__main__":
    unittest.main()
