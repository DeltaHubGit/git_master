#!/usr/bin/env bash
# Scan each repo's full git history, make sure its GitHub repo exists and is private, then push.
# A repo with any unreviewed gitleaks finding is skipped. A plain push only, never forced: if
# GitHub has commits this clone lacks, the push is refused and ./sync.sh down merges them first.
# After each push, main is branch-protected on GitHub (rule 5, needs GitHub Pro for private repos).
# Usage: ./push.sh [name ...]            (default: every repo in ~/.git_master/config.json)
#        ./push.sh --protect [name ...]  only switch on branch protection, push nothing
set -euo pipefail

source "$(dirname "$0")/lib.sh"

"$GH" auth status >/dev/null 2>&1 || { echo "Not logged in to GitHub: run ./setup.sh" >&2; exit 1; }
OWNER="$(setting github_owner)"
[ -n "$OWNER" ] || OWNER="$("$GH" api user --jq .login)"
"$GH" auth setup-git >/dev/null

PROTECT_ONLY=""
[ "${1:-}" = --protect ] && { PROTECT_ONLY=1; shift; }

while IFS=$'\t' read -r name dir; do
  echo "=== $name"
  [ -d "$dir/.git" ] || { echo "  SKIPPED: $dir is not a git repo (./setup.sh add $dir)"; continue; }
  if [ -n "$PROTECT_ONLY" ]; then s="$(python3 "$ROOT/gm.py" slug "$name")"; protect_main "${s:-$OWNER/$name}"; continue; fi

  if ! scan_history "$name"; then
    echo "  SKIPPED: gitleaks found secrets in history: ~/.git_master/reports/$name.git.json"
    continue
  fi
  if [ -n "$(git -C "$dir" status --porcelain)" ]; then
    echo "  SKIPPED: uncommitted changes"
    continue
  fi

  slug="$(python3 "$ROOT/gm.py" slug "$name")"; [ -n "$slug" ] || slug="$OWNER/$name"
  if ! "$GH" repo view "$slug" >/dev/null 2>&1; then
    "$GH" repo create "$slug" --private >/dev/null
    echo "  created private repo $slug"
  elif [ "$("$GH" repo view "$slug" --json visibility --jq .visibility)" != PRIVATE ]; then
    echo "  SKIPPED: $slug already exists and is not private"
    continue
  fi

  # Push only to this repo's own GitHub copy (the clone's remote may be SSH or HTTPS)
  if ! git -C "$dir" remote get-url origin >/dev/null 2>&1; then
    git -C "$dir" remote add origin "https://github.com/$slug.git"
  fi
  git -C "$dir" remote get-url origin | grep -qE "[:/]$slug(\.git)?$" \
    || { echo "  SKIPPED: origin is not $slug"; continue; }
  git -C "$dir" push -u origin main
  echo "  pushed -> https://github.com/$slug"
  protect_main "$slug"
done < <(projects "$@")
