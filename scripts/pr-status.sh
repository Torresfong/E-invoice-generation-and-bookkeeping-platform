#!/usr/bin/env bash
# One look at everything a PR has received: CI, reviews, PR conversation comments (where independent reviews and Codex
# summaries land), inline comments, and which commit each one reviewed. Nothing notifies the agent, so run this after every
# push and at the start of each later turn (docs/agents/ai-workflow.md, "Fresh PR review gate").
#
# Each section fetches its list with exactly one `gh` call (gh's own bundled --jq engine; no standalone jq binary
# required), so a section never contradicts itself while a bot is posting.
#
# Default mode is incremental: reviews, PR comments and inline comments already shown on an earlier run of this script
# for this PR (their ids tracked in .git/pr-status/<pr>.{reviews,comments,inline}.json) print only as a one-line
# "N new since last check", not their full text again. Items are matched by their own id, not by position, so a
# comment deleted between polls can't shift the rest of the list out of view and hide a genuinely new one. Each
# `gh --jq` call always emits the current id list as its first line, by construction, never by scanning later lines
# for a marker — so a comment body that happens to contain text resembling one can't be mistaken for it. That first
# line is read out with shell parameter expansion on the already-captured variable, not a `head -n1` pipe, since a
# pipe reader that exits after one line can SIGPIPE a large writer under `set -o pipefail` and abort the script
# before later sections run. Pass --full
# to force every item in full regardless of the tracked state — always do this on the first check of a fresh context
# window, since "already shown" refers to an earlier run of this script, not to what the current agent has actually
# read.
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
reviews_state="$state_dir/$pr.reviews.json"
comments_state="$state_dir/$pr.comments.json"
inline_state="$state_dir/$pr.inline.json"

seen_reviews='[]'
seen_comments='[]'
seen_inline='[]'
if [ "$full" -eq 0 ]; then
  [ -f "$reviews_state" ] && seen_reviews=$(cat "$reviews_state")
  [ -f "$comments_state" ] && seen_comments=$(cat "$comments_state")
  [ -f "$inline_state" ] && seen_inline=$(cat "$inline_state")
fi

head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
echo "== PR #$pr  HEAD ${head:0:7}"
gh pr view "$pr" --json state,isDraft --jq '"state: \(.state)  draft: \(.isDraft)"'

echo
echo "== CI"
gh pr view "$pr" --json statusCheckRollup \
  --jq '.statusCheckRollup | if length == 0 then "(none)" else .[] | "\(.name): \(if .status == "COMPLETED" then .conclusion else .status end)" end'

echo
export HEAD_SHA="$head" SEEN="$seen_reviews"
out=$(gh pr view "$pr" --json reviews --jq '
  .reviews as $all | ($ENV.SEEN | fromjson) as $seen |
  ($all | map(select(.id as $id | ($seen | index($id)) == null))) as $new |
  ($all | map(.id) | tojson),
  "== Reviews (commit vs HEAD) -- \($all | length) total, \($new | length) new since last check",
  ($new | if length == 0 then "(none new)" else .[] | "\(.author.login) \(.state) on \(.commit.oid[0:7]) \(if .commit.oid == $ENV.HEAD_SHA then "CURRENT" else "STALE" end) [\(.body | length) chars]\n  \(.body[0:150])" end)')
printf '%s\n' "${out%%$'\n'*}" > "$reviews_state"
echo "$out" | tail -n +2

echo
export SEEN="$seen_comments"
out=$(gh pr view "$pr" --json comments --jq '
  .comments as $all | ($ENV.SEEN | fromjson) as $seen |
  ($all | map(select(.id as $id | ($seen | index($id)) == null))) as $new |
  ($all | map(.id) | tojson),
  "== PR conversation comments (independent reviews and Codex summaries land here) -- \($all | length) total, \($new | length) new since last check",
  ($new | if length == 0 then "(none new)" else .[] | "--- \(.author.login) \(.createdAt) [\(.body | length) chars]\n\(.body[0:200])" end),
  "",
  "-- Codex receipts (reviewed commit must equal HEAD; always checked against every comment, not just new ones)",
  ([$all[] | select(.body | contains("codex-review-state"))
    | (.body | capture("\"code_review\":\\{\"commit_sha\":\"(?<sha>[0-9a-f]*)\",\"has_any_findings\":(?<any>[a-z]*),\"has_unresolved_findings\":(?<open>[a-z]*),\"status\":\"(?<status>[a-z_]*)\"") // empty)
    | "code_review status=\(.status) any_findings=\(.any) unresolved=\(.open) commit=\(.sha[0:7])"]
   | if length == 0 then "(none)" else .[] end)')
printf '%s\n' "${out%%$'\n'*}" > "$comments_state"
echo "$out" | tail -n +2

echo
export SEEN="$seen_inline"
out=$(gh api "repos/{owner}/{repo}/pulls/$pr/comments" --jq '
  . as $all | ($ENV.SEEN | fromjson) as $seen |
  ($all | map(select(.id as $id | ($seen | index($id)) == null))) as $new |
  ($all | map(.id) | tojson),
  "== Inline review comments (commit vs HEAD) -- \($all | length) total, \($new | length) new since last check",
  ($new | if length == 0 then "(none new)" else .[] | "\(.user.login) \(.path):\(.line) on \(.commit_id[0:7]) \(if .commit_id == $ENV.HEAD_SHA then "CURRENT" else "STALE" end) [\(.body | length) chars]\n  \(.body[0:150])" end)')
printf '%s\n' "${out%%$'\n'*}" > "$inline_state"
echo "$out" | tail -n +2

echo
echo "Anything listed above as new is unread until you read it in full:"
echo "  gh pr view $pr --json comments,reviews --jq '.comments[].body, .reviews[].body'"
echo "Items already counted on an earlier run are not reprinted; rerun with --full to see everything again."
