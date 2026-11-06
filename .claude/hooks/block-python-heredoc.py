"""Reject inline Python heredocs in Claude Code shell tool calls.

This is a focused tool-choice guard, not an OS-level file access sandbox.
Regular Python commands (manage.py, -m pytest) remain available.
"""

import json
import re
import sys


PYTHON_STDIN_HEREDOC = re.compile(
    r"(?<![\w./-])(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+(?:-[A-Za-z]+\s+)*-?\s*<<-?\s*['\"]?[A-Za-z_][\w-]*['\"]?",
    re.IGNORECASE,
)


def main():
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("expected hook event object")
        if event.get("tool_name") not in {"Bash", "PowerShell"}:
            return 0
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
            raise ValueError("missing shell command")
    except (ValueError, TypeError, json.JSONDecodeError):
        print("BLOCKED: invalid shell hook input; command was not executed.", file=sys.stderr)
        return 2

    if PYTHON_STDIN_HEREDOC.search(tool_input["command"]):
        print(
            "BLOCKED: Python heredoc scripts are not permitted in Bash/PowerShell. "
            "Use the Edit/Write tools for source changes. For justified bulk "
            "transformations, save a reviewable script and request approval.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
