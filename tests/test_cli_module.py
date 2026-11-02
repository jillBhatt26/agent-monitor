import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliModuleTest(unittest.TestCase):
    def test_python_m_runs_main(self):
        # cmd_watch re-execs into .venv-watch with `python -m token_ledger.cli`; without a main guard it exits silently.
        r = subprocess.run([sys.executable, "-m", "token_ledger.cli", "watch", "--help"], cwd=ROOT,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage: tokens watch", r.stdout.replace("cli.py", "tokens"))


if __name__ == "__main__":
    unittest.main()
