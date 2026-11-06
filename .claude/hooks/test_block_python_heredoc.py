"""Behavioral regression checks for the Claude Code Bash policy hook."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


HOOK = Path(__file__).with_name("block-python-heredoc.py")


def run_hook(command, tool="Bash"):
    event = {"tool_name": tool, "tool_input": {"command": command}}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
    )


class HeredocPolicyTests(unittest.TestCase):
    def test_rejects_python_heredoc_after_cd(self):
        command = (
            'cd "c:/Desktop/saas accounting project/backend" && python - <<\'EOF\'\n'
            "open('core/ports/fakes.py', 'w').write('x')\nEOF"
        )
        result = run_hook(command)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Edit/Write", result.stderr)

    def test_rejects_common_interpreter_variations(self):
        commands = [
            "python <<EOF\nprint(1)\nEOF",
            "python3 - <<PY\nprint(1)\nPY",
            'py - << "SCRIPT"\nprint(1)\nSCRIPT',
            "python.exe - <<-EOF\nprint(1)\nEOF",
            "python -<<EOF\nprint(1)\nEOF",
            "python -u - <<EOF\nprint(1)\nEOF",
            "bash -c 'python - <<EOF\nprint(1)\nEOF'",
        ]
        for command in commands:
            with self.subTest(command=command.splitlines()[0]):
                self.assertEqual(run_hook(command).returncode, 2)

    def test_keeps_regular_development_commands(self):
        commands = [
            "python manage.py check",
            "python -m pytest -q",
            "pytest -q",
            "ruff check .",
            "docker compose exec -T backend pytest -q",
            "git status --short",
            "gh issue create --body \"$(cat <<EOF\nTest body\nEOF\n)\"",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(run_hook(command).returncode, 0)

    def test_unrelated_tools_are_ignored(self):
        self.assertEqual(run_hook("python - <<EOF", tool="Edit").returncode, 0)

    def test_powershell_python_heredoc_form_is_also_blocked(self):
        self.assertEqual(run_hook("python - <<EOF", tool="PowerShell").returncode, 2)

    def test_bad_hook_input_fails_closed(self):
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input="not JSON",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
