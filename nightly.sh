#!/usr/bin/env bash
# Nightly backup of every repo in ~/.git_master/config.json, on the one computer that runs it
# (two computers pushing the same repos would collide). Runs the same steps CLAUDE.md lists for a
# manual backup, per repo: stage + three gates, PII audit of new documents, commit "Sync from
# <machine> <date>: <what changed>", then pull (commit before every pull; merge, never rebase; a
# conflict holds the repo with both versions kept), push.sh.
#
#   ./nightly.sh [names]      the night run; saves the morning summary (01:00 timer)
#   ./nightly.sh --post       post the saved summary to Slack, if config.json has "slack_post" (08:00 timer)
#   ./nightly.sh --no-slack   night run without saving or sending the summary (testing)
#   ./nightly.sh install      install and start the two timers (routine/*.service, *.timer)
#
# A repo that trips a gate is HELD: left uncommitted, and named in the summary with no other detail.
# The reason is in ~/routine-output/git-backup/<date>/report.txt (also `latest/`), to resolve with
# the owner during the day (~/.git_master/rules/<name>.allow or .exclude); the next night retries.
set -uo pipefail
source "$(dirname "$0")/lib.sh"

OUT="$HOME/routine-output/git-backup"
POST_HOUR=8
TZ_NAME=Europe/Ljubljana
DOC_RE='\.(pdf|docx?|xlsx|pptx|rtf|odt)$'

held=(); pushed=(); unchanged=(); REPORT=""; DONE=""; NO_SLACK=""

log() { printf '%s\n' "$*"; }
hold() { held+=("$1"); log "  HELD: $2"; }
join_comma() { local IFS=,; echo "$*" | sed 's/,/, /g'; }
allow_list() { if [ -f "$RULES/$1.allow" ]; then grep -v '^#' "$RULES/$1.allow"; fi; }
# Commits not on GitHub? A repo with no origin/main yet (first backup) counts as ahead if it has any commit.
ahead() {
  if git -C "$1" rev-parse -q --verify origin/main >/dev/null 2>&1; then [ -n "$(git -C "$1" rev-list origin/main..HEAD)" ]
  else git -C "$1" rev-parse -q --verify HEAD >/dev/null 2>&1; fi
}

backup_project() {
  local name="$1" src="$2" today="$3" docs t flagged n
  log "=== $name"
  [ -d "$src" ] || { hold "$name" "folder missing: $src"; return; }

  if ! "$ROOT/sync.sh" up "$name" | sed 1d; then hold "$name" "safety gate or stop (above)"; return; fi

  # Gates cannot read PDF, Word or Excel: audit the new/changed documents before they are committed
  docs=$(git -C "$src" diff --cached --name-only --diff-filter=AM | grep -iE "$DOC_RE" || true)
  if [ -n "$docs" ]; then
    t="$(mktemp -d)"
    printf '%s\n' "$docs" | git -C "$src" checkout-index --prefix="$t/" --stdin
    flagged=$(python3 "$ROOT/audit_pii.py" --list "$t" | grep -vxF -f <(allow_list "$name") || true)
    rm -rf "$t"
    if [ -n "$flagged" ]; then
      git -C "$src" reset -q
      log "  personal data in document(s):"; printf '%s\n' "$flagged" | sed 's/^/    /'
      log "    -> add patterns to rules/$name.exclude (keep local) or exact paths to rules/$name.allow (reviewed OK)"
      hold "$name" "personal data in documents"; return
    fi
    log "  $(printf '%s\n' "$docs" | wc -l | xargs) document(s) audited clean"
  fi

  if ! git -C "$src" diff --cached --quiet; then
    n=$(git -C "$src" diff --cached --name-only | wc -l | xargs)
    # The message says what changed (commit_message.py); nobody is there to describe it
    git -C "$src" diff --cached --name-status | python3 "$ROOT/commit_message.py" "Sync from $MACHINE $today" \
      | git -C "$src" commit -q -F - || { hold "$name" "commit failed"; return; }
    log "  committed $n file(s)"
  fi
  # Committed first, so the pull can merge without touching uncommitted work
  if ! "$ROOT/sync.sh" down "$name" | sed 1d; then hold "$name" "pull from GitHub stopped (above)"; return; fi
  if ! ahead "$src"; then unchanged+=("$name"); log "  nothing new to push"; return; fi
  "$ROOT/push.sh" "$name" | sed 1d
  if ahead "$src"; then hold "$name" "push refused (above)"; return; fi
  pushed+=("$name")
}


# Folders under the config's watch_dirs that it does not list (so they have no backup at all)
unlisted() { python3 "$ROOT/gm.py" scan 2>/dev/null | awk '{print $1}' | sed "s#^~/##"; }

# The morning message. Held projects are named and nothing more: no filenames, no reasons.
summary() {
  local today="$1" total=$(( ${#pushed[@]} + ${#unchanged[@]} + ${#held[@]} )) extra
  echo "*GIT BACKUP* $today: $total projects, ${#pushed[@]} pushed, ${#unchanged[@]} already up to date."
  [ ${#held[@]} -gt 0 ] && echo ":warning: Not backed up: $(join_comma "${held[@]}"). Ask git_master to sort it out."
  extra="$(unlisted | paste -sd, - | sed 's/,/, /g')"
  [ -n "$extra" ] && echo ":grey_question: Not in the backup at all: $extra"
  return 0
}

post() {
  local text dir cmd
  dir="$(python3 "$ROOT/gm.py" get slack_post | python3 -c 'import json,sys,os; d=json.loads(sys.stdin.read() or "{}"); print(os.path.expanduser(d.get("dir","")))' 2>/dev/null)"
  cmd="$(python3 "$ROOT/gm.py" get slack_post | python3 -c 'import json,sys,shlex; d=json.loads(sys.stdin.read() or "{}"); print(shlex.join(d.get("command",[])))' 2>/dev/null)"
  if [ -z "$cmd" ]; then echo "no slack_post in ~/.git_master/config.json: the summary stays in $OUT"; return 0; fi
  if [ -f "$OUT/pending-slack.txt" ]; then text="$(cat "$OUT/pending-slack.txt")"
  else text=":x: *GIT BACKUP* The night run left no result. Details on the laptop: \`journalctl --user -u git-backup -n 80\`"; fi
  printf '%s' "$text" | (cd "${dir:-.}" && eval "$cmd") || { echo "Slack post failed" >&2; return 1; }
  mkdir -p "$OUT/latest/" 2>/dev/null || true
  [ -f "$OUT/pending-slack.txt" ] && mv "$OUT/pending-slack.txt" "$OUT/posted-slack.txt"
  echo "posted to Slack"
}

on_exit() {
  local rc=$?
  if [ -z "$DONE" ] && [ -z "$NO_SLACK" ] && [ -n "$REPORT" ]; then
    printf '%s\n' ":x: *GIT BACKUP* The night run failed (exit $rc). Details on the laptop: \`journalctl --user -u git-backup -n 80\`" > "$OUT/pending-slack.txt"
  fi
}

night() {
  local today text
  today="$(TZ=$TZ_NAME date +%F)"
  mkdir -p "$OUT/$today"; REPORT="$OUT/$today/report.txt"; : > "$REPORT"
  ln -sfn "$OUT/$today" "$OUT/latest"
  exec 9>"$OUT/.lock"; flock -n 9 || { echo "another git-backup run is in progress"; exit 1; }
  exec > >(tee -a "$REPORT") 2>&1
  trap on_exit EXIT
  log "git backup from $MACHINE, $(date '+%F %T')${*:+ (only: $*)}"

  while IFS=$'\t' read -r name src; do backup_project "$name" "$src" "$today"; done < <(projects "$@")

  text="$(summary "$today")"
  echo; echo "$text"
  rm -f "$OUT/$today/held.txt"
  [ ${#held[@]} -gt 0 ] && printf '%s\n' "${held[@]}" > "$OUT/$today/held.txt"
  if [ -z "$NO_SLACK" ]; then
    printf '%s\n' "$text" > "$OUT/pending-slack.txt"
    if [ "$(TZ=$TZ_NAME date +%-H)" -ge "$POST_HOUR" ]; then post; fi
  fi
  DONE=1
}

install_units() {
  local f
  mkdir -p "$HOME/.config/systemd/user"
  for f in "$ROOT"/routine/git-backup*.service "$ROOT"/routine/git-backup*.timer; do
    sed "s#@GIT_MASTER@#$ROOT#g" "$f" > "$HOME/.config/systemd/user/$(basename "$f")"
  done
  systemctl --user daemon-reload
  systemctl --user enable --now git-backup.timer git-backup-slack.timer
  systemctl --user list-timers 'git-backup*' --no-pager
}

python3 "$ROOT/gm.py" tsv >/dev/null || exit 1   # not set up yet: gm.py says so
mkdir -p "$OUT"
case "${1:-}" in
  --post)     post ;;
  install)    install_units ;;
  --no-slack) shift; NO_SLACK=1; night "$@" ;;
  -h|--help)  sed -n '2,15p' "$0" ;;
  *)          night "$@" ;;
esac
