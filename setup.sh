#!/usr/bin/env bash
# Set git_master up for one person on one computer (macOS or Linux), and add repos to it.
#
#   ./setup.sh                      first run (safe to repeat): install gh + gitleaks into tools/, log in
#                                   to GitHub, create ~/.git_master/ (config.json, rules/), check the repos
#   ./setup.sh add <folder> [name]  look after a folder: list it in the config; if it is not a git repo yet,
#                                   make it one, with a new private GitHub repo as its online copy
#   ./setup.sh clone <name>         a listed repo whose folder is missing here: clone it from GitHub
#
# Nothing in the folders is changed or deleted: add only runs `git init` where there is no repo yet.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

install_tools() {
  if { [ ! -x "$GH" ] || [ ! -x "$GITLEAKS" ]; } && [ ! -w "$ROOT" ]; then
    echo "gh or gitleaks is missing in $ROOT/tools and this folder is read-only: ask the admin to install them" >&2
    exit 1
  fi
  mkdir -p "$ROOT/tools"
  local os cpu v t glcpu glos
  case "$(uname -s)" in Darwin) os=mac ;; Linux) os=linux ;; *) echo "unsupported OS" >&2; exit 1 ;; esac
  case "$(uname -m)" in arm64|aarch64) cpu=arm64 ;; x86_64|amd64) cpu=amd64 ;; *) echo "unsupported CPU" >&2; exit 1 ;; esac
  # awk reads the whole response: stopping at the first match (grep -m1) breaks curl's pipe on Linux
  latest() { curl -fsSL "https://api.github.com/repos/$1/releases/latest" | awk -F'"' '/"tag_name"/ && !v {v=$4} END {sub(/^v/,"",v); print v}'; }
  if [ ! -x "$GH" ]; then
    v=$(latest cli/cli); t=$(mktemp -d)
    if [ $os = mac ]; then
      curl -fsSL -o "$t/gh.zip" "https://github.com/cli/cli/releases/download/v$v/gh_${v}_macOS_${cpu}.zip"
      unzip -q "$t/gh.zip" -d "$t"; cp "$t/gh_${v}_macOS_${cpu}/bin/gh" "$GH"
    else
      curl -fsSL "https://github.com/cli/cli/releases/download/v$v/gh_${v}_linux_${cpu}.tar.gz" | tar -xz -C "$t"
      cp "$t/gh_${v}_linux_${cpu}/bin/gh" "$GH"
    fi
    rm -rf "$t"; echo "installed gh $v"
  fi
  if [ ! -x "$GITLEAKS" ]; then
    v=$(latest gitleaks/gitleaks); glcpu=$([ $cpu = amd64 ] && echo x64 || echo arm64)
    glos=$([ $os = mac ] && echo darwin || echo linux)
    curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v$v/gitleaks_${v}_${glos}_${glcpu}.tar.gz" \
      | tar -xz -C "$ROOT/tools" gitleaks
    echo "installed gitleaks $v"
  fi
  python3 -c "import pypdf" 2>/dev/null || echo "note: 'pip install pypdf' so audit_pii.py can read PDFs"
}

# This computer's git identity for a repo, from the config (only where the repo has none)
identity() {
  local dir="$1" n e
  n="$(setting git_name)"; e="$(setting git_email)"
  git -C "$dir" config user.name >/dev/null || { [ -n "$n" ] && git -C "$dir" config user.name "$n"; } || true
  git -C "$dir" config user.email >/dev/null || { [ -n "$e" ] && git -C "$dir" config user.email "$e"; } || true
}

first_run() {
  install_tools
  # no screen (a server, an SSH session): gh cannot open a browser and would stop there, so "opening" it does nothing
  # and the person opens https://github.com/login/device on another device and types the code shown
  if [ "$(uname -s)" = Linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ -z "${BROWSER:-}" ]; then
    export BROWSER=true
    echo "No screen here: when asked, press Enter, then open https://github.com/login/device on your phone or laptop and type the code."
  fi
  "$GH" auth status >/dev/null 2>&1 || "$GH" auth login --hostname github.com --git-protocol https --web
  "$GH" auth setup-git >/dev/null
  python3 "$ROOT/gm.py" init --owner "$("$GH" api user --jq .login)"
  while IFS=$'\t' read -r name dir; do
    if [ -d "$dir/.git" ]; then apply_local_excludes "$name" "$dir"; echo "$name: ok ($dir)"
    elif [ -d "$dir" ]; then echo "$name: $dir is not a git repo yet: ./setup.sh add $dir"
    else echo "$name: folder missing here: ./setup.sh clone $name"; fi
  done < <(projects)
  python3 "$ROOT/gm.py" list >/dev/null 2>&1 && [ -z "$(projects)" ] && echo "no repos yet: ./setup.sh add <folder> for each folder to back up"
  true
}

add_repo() {
  local dir name slug owner
  [ -n "${1:-}" ] || { echo "usage: ./setup.sh add <folder> [name]" >&2; exit 1; }
  dir="$(cd "$1" && pwd)"; name="${2:-$(basename "$dir")}"
  python3 "$ROOT/gm.py" add "$dir" --name "$name"
  [ -d "$dir/.git" ] || { git -C "$dir" init -q -b main; echo "  made $dir a git repo (branch main)"; }
  identity "$dir"
  apply_local_excludes "$name" "$dir"
  if ! git -C "$dir" remote get-url origin >/dev/null 2>&1; then
    slug="$(python3 "$ROOT/gm.py" slug "$name")"
    [ -n "$slug" ] || { owner="$("$GH" api user --jq .login)"; slug="$owner/$name"; }
    if "$GH" repo view "$slug" >/dev/null 2>&1; then
      [ "$("$GH" repo view "$slug" --json visibility --jq .visibility)" = PRIVATE ] \
        || { echo "  STOP: $slug already exists and is not private; pick another name: ./gm.py remove $name, then ./setup.sh add $dir <name>"; exit 1; }
      echo "  $slug already exists on GitHub; using it as the online copy"
    else
      "$GH" repo create "$slug" --private >/dev/null
      echo "  created private repo $slug"
    fi
    git -C "$dir" remote add origin "https://github.com/$slug.git"
  fi
  echo "  next: ./nightly.sh --no-slack $name   (first scan, commit and push)"
}

clone_repo() {
  local name="${1:-}" dir slug
  dir="$(repo_dir "$name")"
  [ -d "$dir/.git" ] && { echo "$name: already here ($dir)"; return; }
  slug="$(python3 "$ROOT/gm.py" slug "$name")"
  [ -n "$slug" ] || slug="$("$GH" api user --jq .login)/$name"
  mkdir -p "$(dirname "$dir")"
  "$GH" repo clone "$slug" "$dir" -- -q
  identity "$dir"
  [ -d "$dir/.githooks" ] && git -C "$dir" config core.hooksPath .githooks
  apply_local_excludes "$name" "$dir"
  echo "$name: cloned into $dir"
}

case "${1:-}" in
  "")    first_run ;;
  add)   shift; add_repo "$@" ;;
  clone) shift; clone_repo "$@" ;;
  *)     sed -n '2,11p' "$0"; exit 1 ;;
esac
