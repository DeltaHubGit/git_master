#!/usr/bin/env bash
# Back up the repos listed in ~/.git_master/config.json and bring GitHub's changes in.
# Each repo is a folder on this computer that is its own git clone.
#
#   ./sync.sh status [names]  what is uncommitted, and what differs from GitHub
#   ./sync.sh up     [names]  stage each repo's changes and run the three safety gates (then commit, down, ./push.sh)
#   ./sync.sh down   [names]  bring GitHub's new commits in, never overwriting work
#
# Safety rules (CLAUDE.md): commit before every pull; pull by merging, never rebasing; on a
# conflict keep both versions and stop; never force anything.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

local_state() {
  local name="$1" dir="$2" n
  apply_local_excludes "$name" "$dir"
  n=$(git -C "$dir" status --porcelain | wc -l | xargs)
  if [ "$n" = 0 ]; then echo "  computer vs backup: identical"
  else echo "  computer vs backup: $n uncommitted change(s) on this computer"
    if [ "${VERBOSE:-}" = verbose ]; then git -C "$dir" status --short | sed 's/^/    /'; fi
  fi
}

remote_state() {
  local dir="$1" ab
  git -C "$dir" fetch -q origin
  git -C "$dir" rev-parse -q --verify origin/main >/dev/null 2>&1 || { echo "  backup vs GitHub: nothing on GitHub yet"; return; }
  ab=$(git -C "$dir" rev-list --left-right --count HEAD...origin/main)
  set -- $ab
  if [ "$1" = 0 ] && [ "$2" = 0 ]; then echo "  backup vs GitHub: in sync"
  else echo "  backup vs GitHub: $1 commit(s) not pushed, $2 commit(s) on GitHub not pulled"; fi
}

cmd_status() {
  projects "$@" | while IFS=$'\t' read -r name dir; do
    echo "=== $name"
    [ -d "$dir/.git" ] || { echo "  not a git repo here: $dir (./setup.sh add $dir)"; continue; }
    remote_state "$dir"
    local_state "$name" "$dir"
  done
}

cmd_up() {
  local fail=0
  while IFS=$'\t' read -r name dir; do
    echo "=== $name"
    [ -d "$dir/.git" ] || { echo "  not a git repo here: $dir (./setup.sh add $dir)"; fail=1; continue; }
    # Rule 1: commit before every pull. So up does not wait for GitHub: commit what is here,
    # then ./sync.sh down merges GitHub's new commits in.
    git -C "$dir" fetch -q origin
    if [ -n "$(git -C "$dir" rev-list HEAD..origin/main 2>/dev/null)" ]; then
      echo "  GitHub has new commits too: commit this first, then ./sync.sh down $name merges them in"
    fi
    apply_local_excludes "$name" "$dir"
    git -C "$dir" add -A

    if git -C "$dir" diff --cached --quiet; then echo "  nothing to sync"; continue; fi
    git -C "$dir" diff --cached --shortstat | sed 's/^ */  /'

    if ! gate_staged "$name"; then
      git -C "$dir" reset -q
      echo "  NOT STAGED"
      fail=1; continue
    fi
    echo "  staged and scanned clean; ready to commit"
  done < <(projects "$@")
  return $fail
}

cmd_down() {
  local fail=0
  while IFS=$'\t' read -r name dir; do
    echo "=== $name"
    [ -d "$dir/.git" ] || { echo "  not a git repo here: $dir (./setup.sh add $dir)"; fail=1; continue; }
    git -C "$dir" fetch -q origin
    local base remote
    # A repo created on this computer has no commits on GitHub (or locally) until its first up + push
    remote=$(git -C "$dir" rev-parse -q --verify origin/main 2>/dev/null) || { echo "  nothing on GitHub yet"; continue; }
    base=$(git -C "$dir" rev-parse -q --verify HEAD 2>/dev/null) \
      || { echo "  STOP: GitHub has commits but this clone has none; clone it properly (./setup.sh clone $name)"; fail=1; continue; }
    [ "$base" = "$remote" ] && { echo "  already up to date"; continue; }
    git -C "$dir" merge-base --is-ancestor "$remote" "$base" \
      && { echo "  nothing new on GitHub (this computer is ahead; push it)"; continue; }
    # Rule 1: commit before every pull, so a merge can never touch work that isn't in history
    if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
      echo "  STOP: uncommitted changes here; commit them first (./sync.sh up $name, commit), then pull"
      fail=1; continue
    fi

    # Rule 2: both sides have new commits -> merge, never rebase. Rule 3: if the same lines
    # changed on both sides, keep GitHub's version in conflicts/<name>/, undo the merge (this
    # computer's commit stays as it was) and stop for a person to pick.
    local out
    if git -C "$dir" merge-base --is-ancestor "$base" "$remote"; then
      git -C "$dir" merge -q --ff-only origin/main || { echo "  STOP: fast-forward refused (above); nothing changed"; fail=1; continue; }
    elif out=$(git -C "$dir" merge --no-ff --no-edit -m "Merge GitHub changes into $MACHINE" origin/main 2>&1); then
      echo "  merged: both this computer and GitHub had new commits; both kept"
    else
      local unmerged; unmerged="$(git -C "$dir" diff --name-only --diff-filter=U)"
      if [ -n "$unmerged" ]; then
        keep_github_versions "$name" "$dir" <<< "$unmerged"
        echo "  STOP: the same lines changed here and on GitHub; nothing merged. Pick a version (CLAUDE.md, Conflicts)"
      else
        echo "  STOP: merge refused, nothing changed: $(tail -n 1 <<< "$out")"
      fi
      git -C "$dir" merge --abort 2>/dev/null || true
      fail=1; continue
    fi
    echo "  pulled $(git -C "$dir" rev-list --count "$base..$remote") commit(s)"
  done < <(projects "$@")
  return $fail
}

case "${1:-}" in
  status) shift; cmd_status "$@" ;;
  up)     shift; cmd_up "$@" ;;
  down)   shift; cmd_down "$@" ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
