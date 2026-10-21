# Shared helpers for sync.sh / push.sh / nightly.sh / setup.sh. Source, don't run.
#
# git_master's folder holds only code. Everything that belongs to one person lives in their own
# ~/.git_master/ (or $GIT_MASTER_HOME): config.json lists the repos to look after (any folders on
# the computer, each one its own git clone), rules/ and gitleaks-allow/ hold their decisions,
# reports/ and conflicts/ the local output. gm.py reads and edits the config.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GM_HOME="${GIT_MASTER_HOME:-$HOME/.git_master}"
RULES="$GM_HOME/rules"
ALLOW_LEAKS="$GM_HOME/gitleaks-allow"
REPORTS="$GM_HOME/reports"
CONFLICTS="$GM_HOME/conflicts"
GH="$ROOT/tools/gh"
GITLEAKS="$ROOT/tools/gitleaks"
MACHINE="${GIT_MASTER_MACHINE:-$(hostname -s)}"
export LC_ALL=C
mkdir -p "$REPORTS" 2>/dev/null || true

# Print non-ASCII paths (e.g. "č") as-is instead of octal-escaped
git() { command git -c core.quotepath=off "$@"; }

# Filenames that may hold personal/financial/identity data. New or changed files
# matching this need an explicit decision in rules/<name>.allow or rules/<name>.exclude.
SENSITIVE_NAME_RE='bank|iban|revolut|paypal|account[ _-]?confirm|statement|izpisek|passport|potni[ _-]?list|osebna|id[ _-]?card|driver.?s?[ _-]?licen|payslip|salary|pla[cč]a|(^|[^a-z])tax([^a-z]|$)|dav[cč]n|password|geslo|credential|secret|recovery|backup[ _-]?codes|2fa|private[ _-]?key|wallet'
# IBAN-shaped strings in text files (country code, 2 check digits, then 4-character groups).
IBAN_RE='\b[A-Z]{2}[0-9]{2}( ?[0-9A-Z]{4}){3,7}( ?[0-9A-Z]{1,3})?\b'

# The repos from config.json -> lines "name<TAB>folder", optionally only the names in $@
projects() { python3 "$ROOT/gm.py" tsv "$@"; }

# The git clone for a repo: its folder
repo_dir() { python3 "$ROOT/gm.py" tsv "$1" | cut -f2; }

# One setting from config.json (empty if unset)
setting() { python3 "$ROOT/gm.py" get "$1"; }

# Keep safety.gitignore + rules/<name>.exclude out of commits without touching the repo's own
# .gitignore: they are written to the clone's .git/info/exclude
apply_local_excludes() {
  local name="$1" dir="$2"
  mkdir -p "$dir/.git/info"
  { echo "# Written by git_master; edit its safety.gitignore or ~/.git_master/rules/$name.exclude instead"
    cat "$ROOT/safety.gitignore"
    if [ -f "$RULES/$name.exclude" ]; then cat "$RULES/$name.exclude"; fi; } > "$dir/.git/info/exclude"
}

# During a merge conflict: save GitHub's side of each conflicted path (one per line on stdin) to
# conflicts/<name>/<path>, so both versions survive the merge --abort that follows: this
# computer's stays in the folder (it is committed), GitHub's waits in conflicts/ for a person to pick.
keep_github_versions() {
  local name="$1" dir="$2" path keep
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    keep="$CONFLICTS/$name/$path"
    mkdir -p "$(dirname "$keep")"
    if git -C "$dir" cat-file -e ":3:$path" 2>/dev/null; then git -C "$dir" show ":3:$path" > "$keep"
    else : > "$keep.deleted-on-github"; fi
    echo "  CONFLICT $path (changed here and on GitHub; yours is unchanged, GitHub's version in ~/.git_master/conflicts/$name/)"
  done
}

# Rule 5: GitHub itself refuses force-pushes to main and deleting it, for everyone, owner included.
# Private repos need GitHub Pro (or an organisation plan) for this; on Free it says so and moves on.
protect_main() {
  local repo="$1" out
  if out=$("$GH" api -X PUT "repos/$repo/branches/main/protection" --input - 2>&1 <<'JSON'
{"required_status_checks": null, "enforce_admins": true, "required_pull_request_reviews": null,
 "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
JSON
  ); then echo "  main is protected on GitHub (no force-push, no delete)"
  elif grep -q 'Upgrade to GitHub Pro' <<< "$out"; then echo "  main not protected on GitHub: needs GitHub Pro for private repos"
  else echo "  main not protected on GitHub: $(tail -n 1 <<< "$out")"; fi
}

# Safety gates over what's staged in the clone. Prints what needs a decision; returns 1 if any.
gate_staged() {
  local name="$1" dir flagged ibans leaks=0
  dir="$(repo_dir "$1")"
  # Gate 1: filenames that look personal/financial, not yet decided on
  flagged=$(git -C "$dir" diff --cached --name-only --diff-filter=AM \
    | grep -iE "$SENSITIVE_NAME_RE" \
    | grep -vxF -f <([ -f "$RULES/$name.allow" ] && grep -v '^#' "$RULES/$name.allow" || true) || true)
  # Gate 2: IBANs (checksum-valid) in added lines of text files
  ibans=$(git -C "$dir" diff --cached -U0 --no-color | grep '^+' | grep -v '^+++' \
    | grep -oE "$IBAN_RE" | python3 "$ROOT/audit_pii.py" --valid-ibans | sort -u || true)
  # Gate 3: gitleaks over every file, including inside .docx/.xlsx
  scan_dir "$name" || leaks=1

  [ -z "$flagged" ] && [ -z "$ibans" ] && [ "$leaks" = 0 ] && return 0
  echo "  needs a decision:"
  [ -n "$flagged" ] && echo "$flagged" | sed 's/^/    sensitive-looking file: /'
  [ -n "$ibans" ] && echo "    valid IBAN(s) in text: $(echo "$ibans" | wc -l | xargs)"
  [ "$leaks" = 1 ] && echo "    gitleaks findings: ~/.git_master/reports/$name.dir.json"
  echo "    -> add patterns to ~/.git_master/rules/$name.exclude (keep local) or exact paths to rules/$name.allow (OK to push)"
  return 1
}

# gitleaks over every staged file (incl. inside .docx/.xlsx). Returns non-zero on findings.
scan_dir() {
  local name="$1" dir scan rc=0 args=()
  dir="$(repo_dir "$1")"
  [ -f "$ALLOW_LEAKS/$name.gitleaksignore" ] && args=(--gitleaks-ignore-path "$ALLOW_LEAKS/$name.gitleaksignore")
  # The folder also holds ignored local files (.env, venvs); scan only what is staged
  scan="$(mktemp -d)"
  git -C "$dir" checkout-index -a --prefix="$scan/"
  (cd "$scan" && "$GITLEAKS" dir . --no-banner --redact --max-archive-depth 2 ${args[@]+"${args[@]}"} \
    --report-format json --report-path "$REPORTS/$name.dir.json" >/dev/null 2>&1) || rc=1
  rm -rf "$scan"
  return $rc
}

# gitleaks over the full git history of the clone. Returns non-zero on findings.
scan_history() {
  local name="$1" dir args=()
  dir="$(repo_dir "$1")"
  [ -f "$ALLOW_LEAKS/$name.gitleaksignore" ] && args=(--gitleaks-ignore-path "$ALLOW_LEAKS/$name.gitleaksignore")
  "$GITLEAKS" git "$dir" --no-banner --redact --max-archive-depth 2 ${args[@]+"${args[@]}"} \
    --report-format json --report-path "$REPORTS/$name.git.json" >/dev/null 2>&1
}
