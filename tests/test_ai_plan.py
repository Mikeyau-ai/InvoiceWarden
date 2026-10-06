"""Which Gemini model is used for a free key vs a paid (or unset) one. Network faked."""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import parser_ai                       # noqa: E402

MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-pro"]


class GeminiPlanTests(unittest.TestCase):
    """_pick_gemini_model with and without the free-key preference."""

    def setUp(self):
        """Forget any model resolved by an earlier test."""
        parser_ai._gemini_resolved.clear()

    def _pick(self, free, models=MODELS):
        """Pick with a faked model list."""
        with patch.object(parser_ai, "gemini_available_models", return_value=models):
            return parser_ai._pick_gemini_model("key", free)

    def test_free_key_uses_flash_lite(self):
        """A free key gets a Flash-Lite model (bigger free allowance)."""
        self.assertEqual(self._pick(True), "gemini-flash-lite-latest")

    def test_paid_or_unset_key_is_unchanged(self):
        """Paid, or never set (like existing installs), keeps the full Flash model."""
        self.assertEqual(self._pick(False), "gemini-2.5-flash")

    def test_free_key_falls_back_when_no_lite_model(self):
        """No Flash-Lite available: still uses a working Flash model."""
        self.assertEqual(self._pick(True, ["gemini-2.5-flash", "gemini-2.5-pro"]),
                         "gemini-2.5-flash")

    def test_setting_reads_free(self):
        """gemini_free() follows the 'ai.gemini_plan' setting; blank means not free."""
        class S:
            def __init__(self, v):
                self.v = v

            def get(self, key, default=""):
                return self.v if key == "ai.gemini_plan" else default
        self.assertTrue(parser_ai.gemini_free(S("free")))
        self.assertFalse(parser_ai.gemini_free(S("paid")))
        self.assertFalse(parser_ai.gemini_free(S("")))


if __name__ == "__main__":
    unittest.main()
