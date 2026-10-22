#!/usr/bin/env bash
# Keep files out of a repo: "ignore this file", "don't include the raw-photos folder".
#
#   ./ignore.sh <name> <pattern>...            add the patterns to the repo's .gitignore (shared:
#                                              everyone who clones the repo gets the same list)
#   ./ignore.sh --local <name> <pattern>...    add them to ~/.git_master/rules/<name>.exclude
#                                              instead: this computer only, the names never leave it
#   ./ignore.sh --untrack <name> <pattern>...  also stop tracking matching files already in the repo:
#                                              they stay in the folder and leave the repo with the
#                                              next commit (older versions still have them)
#
# Patterns are gitignore lines: "raw-photos/" a folder anywhere, "/raw-photos/" only at the top,
# "*.zip" every zip file, "/briefs/old-draft.md" one exact file. Then commit and ./push.sh.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

local_only=0 untrack=0
while [ $# -gt 0 ]; do
  case "$1" in
    --local)   local_only=1; shift ;;
    --untrack) untrack=1; shift ;;
    -*) echo "unknown option $1" >&2; exit 1 ;;
    *) break ;;
  esac
done
[ $# -ge 2 ] || { sed -n '2,13p' "$0"; exit 1; }
name="$1"; shift
dir="$(repo_dir "$name")"
[ -n "$dir" ] && [ -d "$dir/.git" ] || { echo "$name: no git repo for it in the config (./gm.py list)"; exit 1; }

if [ $local_only = 1 ]; then list="$RULES/$name.exclude"; mkdir -p "$RULES"
else list="$dir/.gitignore"; fi
touch "$list"
# Make sure the file ends with a newline before adding lines to it
if [ -s "$list" ] && [ -n "$(tail -c 1 "$list")" ]; then echo >> "$list"; fi
for pat in "$@"; do
  if grep -qxF -- "$pat" "$list"; then echo "  already on the list: $pat"
  else echo "$pat" >> "$list"; echo "  added: $pat"; fi
done
if [ $local_only = 1 ]; then
  apply_local_excludes "$name" "$dir"
  echo "  list: ${list/#$HOME/\~} (this computer only)"
else
  echo "  list: $dir/.gitignore (goes to GitHub with the next commit)"
fi

# Files already in the repo are not affected by the list until they are untracked
tracked=()
while IFS= read -r -d '' f; do tracked+=("$f"); done < <(
  for pat in "$@"; do git -C "$dir" ls-files -z -c -i -x "$pat"; done | sort -zu)
if [ ${#tracked[@]} -eq 0 ]; then echo "  nothing matching is in the repo yet: from now on these stay on this computer"; exit 0; fi

if [ $untrack = 0 ]; then
  echo "  ALREADY IN THE REPO, still backed up until untracked (${#tracked[@]} file(s)):"
  printf '    %s\n' "${tracked[@]}" | head -n 40 || true
  echo "  To stop tracking them: ./ignore.sh --untrack $name$(printf ' %q' "$@")"
  echo "  (they stay in this folder; on other computers they are removed at the next pull; older versions keep them)"
  exit 0
fi
printf '%s\0' "${tracked[@]}" | git -C "$dir" --literal-pathspecs rm -r -q --cached --pathspec-from-file=- --pathspec-file-nul
echo "  untracked (still in this folder, leave the repo with the next commit):"
printf '    %s\n' "${tracked[@]}" | head -n 40 || true
