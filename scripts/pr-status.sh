#!/usr/bin/env bash
# One look at everything a PR has received: CI, reviews, PR conversation comments (where independent reviews and Codex
# summaries land), inline comments, and which commit each one reviewed. Nothing notifies the agent, so run this after every
# push and at the start of each later turn (docs/agents/ai-workflow.md, "Fresh PR review gate").
#
# Usage: scripts/pr-status.sh [pr-number]   (default: the PR of the current branch)
set -euo pipefail

pr="${1:-}"
[ -n "$pr" ] || pr=$(gh pr view --json number --jq .number)

head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
echo "== PR #$pr  HEAD ${head:0:7}"
gh pr view "$pr" --json state,isDraft --jq '"state: \(.state)  draft: \(.isDraft)"'

echo
echo "== CI"
gh pr view "$pr" --json statusCheckRollup \
  --jq '.statusCheckRollup[] | "\(.name): \(if .status == "COMPLETED" then .conclusion else .status end)"'

echo
echo "== Reviews (commit vs HEAD)"
gh pr view "$pr" --json reviews \
  --jq ".reviews[] | \"\(.author.login) \(.state) on \(.commit.oid[0:7]) \(if .commit.oid == \"$head\" then \"CURRENT\" else \"STALE\" end)\n  \(.body[0:300])\""
[ "$(gh pr view "$pr" --json reviews --jq '.reviews | length')" != 0 ] || echo "(none)"

echo
echo "== PR conversation comments (independent reviews and Codex summaries land here)"
gh pr view "$pr" --json comments \
  --jq '.comments[] | "--- \(.author.login) \(.createdAt) [\(.body | length) chars]\n\(.body[0:500])"'
[ "$(gh pr view "$pr" --json comments --jq '.comments | length')" != 0 ] || echo "(none)"

echo
echo "== Codex receipts (reviewed commit must equal HEAD)"
gh pr view "$pr" --json comments --jq '
  .comments[] | select(.body | contains("codex-review-state"))
  | (.body | capture("\"code_review\":\\{\"commit_sha\":\"(?<sha>[0-9a-f]*)\",\"has_any_findings\":(?<any>[a-z]*),\"has_unresolved_findings\":(?<open>[a-z]*),\"status\":\"(?<status>[a-z_]*)\"") // empty)
  | "code_review status=\(.status) any_findings=\(.any) unresolved=\(.open) commit=\(.sha[0:7])"' || true

echo
echo "== Inline review comments (commit vs HEAD)"
gh api "repos/{owner}/{repo}/pulls/$pr/comments" \
  --jq ".[] | \"\(.user.login) \(.path):\(.line) on \(.commit_id[0:7]) \(if .commit_id == \"$head\" then \"CURRENT\" else \"STALE\" end)\n  \(.body[0:300])\""
[ "$(gh api "repos/{owner}/{repo}/pulls/$pr/comments" --jq 'length')" != 0 ] || echo "(none)"

echo
echo "Anything listed above is unread until you read it in full:"
echo "  gh pr view $pr --json comments,reviews --jq '.comments[].body, .reviews[].body'"
