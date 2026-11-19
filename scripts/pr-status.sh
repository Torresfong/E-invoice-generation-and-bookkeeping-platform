#!/usr/bin/env bash
# One look at everything a PR has received: CI, reviews, PR conversation comments (where independent reviews and Codex
# summaries land), inline comments, and which commit each one reviewed. Nothing notifies the agent, so run this after every
# push and at the start of each later turn (docs/agents/ai-workflow.md, "Fresh PR review gate").
#
# Each section is one `gh` call, so a section never contradicts itself while a bot is posting. Codex receipts are read
# in the same call as the comments they come from.
#
# Default mode is incremental: reviews, PR comments and inline comments already shown on an earlier run of this script
# for this PR (tracked in .git/pr-status/<pr>.seen) print only as a one-line "N new since last check", not their full
# text again. Repeatedly polling while CI runs otherwise reprints the same growing comment history on every call, which
# is pure waste once something has genuinely been read. Pass --full to force every item in full regardless of the
# tracked state — always do this on the first check of a fresh context window, since "already shown" refers to an
# earlier run of this script, not to what the current agent has actually read.
#
# Usage: scripts/pr-status.sh [--full] [pr-number]   (default pr: the PR of the current branch)
set -euo pipefail

full=0
if [ "${1:-}" = "--full" ]; then
  full=1
  shift
fi

pr="${1:-}"
[ -n "$pr" ] || pr=$(gh pr view --json number --jq .number)

state_dir="$(git rev-parse --git-dir 2>/dev/null || echo .git)/pr-status"
mkdir -p "$state_dir"
state_file="$state_dir/$pr.seen"
seen_reviews=0
seen_comments=0
seen_inline=0
if [ "$full" -eq 0 ] && [ -f "$state_file" ]; then
  read -r seen_reviews seen_comments seen_inline < "$state_file"
fi

head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
echo "== PR #$pr  HEAD ${head:0:7}"
gh pr view "$pr" --json state,isDraft --jq '"state: \(.state)  draft: \(.isDraft)"'

echo
echo "== CI"
gh pr view "$pr" --json statusCheckRollup \
  --jq '.statusCheckRollup | if length == 0 then "(none)" else .[] | "\(.name): \(if .status == "COMPLETED" then .conclusion else .status end)" end'

echo
export HEAD_SHA="$head" SEEN=$seen_reviews
out=$(gh pr view "$pr" --json reviews --jq '
  .reviews as $all | ($all | length) as $total | ($ENV.SEEN | tonumber) as $seen |
  "##COUNT \($total)",
  "== Reviews (commit vs HEAD) -- \($total) total, \($total - $seen) new since last check",
  ($all[$seen:] | if length == 0 then "(none new)" else .[] | "\(.author.login) \(.state) on \(.commit.oid[0:7]) \(if .commit.oid == $ENV.HEAD_SHA then "CURRENT" else "STALE" end)\n  \(.body[0:300])" end)')
new_reviews=$(echo "$out" | sed -n 's/^##COUNT //p')
echo "$out" | grep -v '^##COUNT '

echo
export SEEN=$seen_comments
out=$(gh pr view "$pr" --json comments --jq '
  .comments as $all | ($all | length) as $total | ($ENV.SEEN | tonumber) as $seen |
  "##COUNT \($total)",
  "== PR conversation comments (independent reviews and Codex summaries land here) -- \($total) total, \($total - $seen) new since last check",
  ($all[$seen:] | if length == 0 then "(none new)" else .[] | "--- \(.author.login) \(.createdAt) [\(.body | length) chars]\n\(.body[0:500])" end),
  "",
  "-- Codex receipts (reviewed commit must equal HEAD; always checked against every comment, not just new ones)",
  ([$all[] | select(.body | contains("codex-review-state"))
    | (.body | capture("\"code_review\":\\{\"commit_sha\":\"(?<sha>[0-9a-f]*)\",\"has_any_findings\":(?<any>[a-z]*),\"has_unresolved_findings\":(?<open>[a-z]*),\"status\":\"(?<status>[a-z_]*)\"") // empty)
    | "code_review status=\(.status) any_findings=\(.any) unresolved=\(.open) commit=\(.sha[0:7])"]
   | if length == 0 then "(none)" else .[] end)')
new_comments=$(echo "$out" | sed -n 's/^##COUNT //p')
echo "$out" | grep -v '^##COUNT '

echo
export SEEN=$seen_inline
out=$(gh api "repos/{owner}/{repo}/pulls/$pr/comments" --jq '
  . as $all | ($all | length) as $total | ($ENV.SEEN | tonumber) as $seen |
  "##COUNT \($total)",
  "== Inline review comments (commit vs HEAD) -- \($total) total, \($total - $seen) new since last check",
  ($all[$seen:] | if length == 0 then "(none new)" else .[] | "\(.user.login) \(.path):\(.line) on \(.commit_id[0:7]) \(if .commit_id == $ENV.HEAD_SHA then "CURRENT" else "STALE" end)\n  \(.body[0:300])" end)')
new_inline=$(echo "$out" | sed -n 's/^##COUNT //p')
echo "$out" | grep -v '^##COUNT '

echo "$new_reviews $new_comments $new_inline" > "$state_file"

echo
echo "Anything listed above as new is unread until you read it in full:"
echo "  gh pr view $pr --json comments,reviews --jq '.comments[].body, .reviews[].body'"
echo "Items already counted on an earlier run are not reprinted; rerun with --full to see everything again."
