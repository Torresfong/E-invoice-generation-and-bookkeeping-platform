# Claude Code Instructions

@AGENTS.md

The shared `AGENTS.md` is the single entry point for both Claude Code and Codex. Read linked project documents selectively for the current issue; do not automatically load every linked file.

Claude Code may implement code. The final fresh PR review belongs to a separate, read-only Codex task. Follow `docs/agents/ai-workflow.md` for handoffs and review steps.

## Source editing tools
- Prefer Edit/Write for source changes; use Bash for tests, linting, and inspection.
- Avoid Python heredocs for routine source edits; see `docs/agents/dev-environment.md` for exceptions.
