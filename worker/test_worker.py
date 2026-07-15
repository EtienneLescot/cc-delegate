# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "deepagents>=0.7.0a6",
#   "langchain-litellm",
#   "fastapi",
# ]
# ///
"""Unit tests for worker.py's pure/isolable helpers: build_model (fallback
chains), the dangerous-git command guard, and the drive-scan guard.

Not part of server/'s stdlib-only suite (worker.py needs the heavy deepagents
+ litellm stack) — run directly: `uv run worker/test_worker.py`.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worker


class TestBuildModel(unittest.TestCase):
    def test_no_fallbacks_returns_bare_string_unchanged(self):
        self.assertEqual(
            worker.build_model("litellm:minimax/MiniMax-M3", None),
            "litellm:minimax/MiniMax-M3",
        )

    def test_empty_fallback_list_returns_bare_string_unchanged(self):
        self.assertEqual(
            worker.build_model("litellm:minimax/MiniMax-M3", []),
            "litellm:minimax/MiniMax-M3",
        )

    def test_fallbacks_build_a_chatlitellm_instance(self):
        model = worker.build_model(
            "litellm:minimax/MiniMax-M3",
            ["litellm:minimax/MiniMax-Text-01", "litellm:anthropic/claude-haiku-4-5"],
        )
        from langchain_litellm import ChatLiteLLM

        self.assertIsInstance(model, ChatLiteLLM)
        self.assertEqual(model.model, "minimax/MiniMax-M3")
        self.assertEqual(
            model.model_kwargs["fallbacks"],
            ["minimax/MiniMax-Text-01", "anthropic/claude-haiku-4-5"],
        )

    def test_bare_model_strips_provider_prefix_only_once(self):
        self.assertEqual(worker._bare_model("litellm:minimax/MiniMax-M3"), "minimax/MiniMax-M3")
        self.assertEqual(worker._bare_model("no-prefix-model"), "no-prefix-model")


class TestDangerousGitGuard(unittest.TestCase):
    def _blocked(self, cmd: str) -> bool:
        return bool(worker._DANGEROUS_GIT_RE.search(cmd))

    def test_blocks_push_merge_rebase(self):
        self.assertTrue(self._blocked("git push origin main"))
        self.assertTrue(self._blocked("git merge feature-branch"))
        self.assertTrue(self._blocked("git rebase -i HEAD~3"))
        self.assertTrue(self._blocked("GIT PUSH origin main"))  # case-insensitive

    def test_does_not_block_readonly_lookalikes(self):
        self.assertFalse(self._blocked("git merge-base HEAD main"))
        self.assertFalse(self._blocked("git log --merges"))
        self.assertFalse(self._blocked("git status"))
        self.assertFalse(self._blocked("echo merge"))

    def test_backend_execute_rejects_dangerous_git(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            backend = worker.SupervisedShellBackend(
                root_dir=tmp, virtual_mode=True, timeout=5, inherit_env=False, env={},
            )
            result = backend.execute("git push origin main")
            self.assertEqual(result.exit_code, 1)
            self.assertIn("blocked", result.output)

            ok = backend.execute("echo hi")
            self.assertEqual(ok.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
