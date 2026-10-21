#!/usr/bin/env python3
"""PreToolUse hook: the git safety rules, enforced on every shell command Claude runs here.

No work may be lost to an override, a forced push or a bad merge.
  1. Commit before every pull: no pull or merge while tracked files have uncommitted changes.
  2. Pull with merge, never rebase.
  3. A conflict is never resolved by throwing work away (git_master stops and asks; see CLAUDE.md).
  4. Never: push --force (or +refspec), reset --hard, git clean, discarding uncommitted changes
     (checkout -- / restore / switch --discard-changes), dropping stashes, deleting unmerged branches.
  5. (GitHub side) main is branch-protected where the plan allows it; see push.sh.
A refused command gets permissionDecision "deny" with the reason, so Claude can take the safe route.
The scripts (sync.sh, nightly.sh, push.sh) follow the same rules in their own code.
"""
import json
import os
import re
import shlex
import subprocess
import sys

OPERATORS = "();<>|&\n"
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
SHELLS = {"bash", "sh", "zsh", "dash"}
# git's own options that come before the subcommand and take a value
GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}


def strip_heredocs(command: str) -> str:
    """Drop heredoc bodies: they are text fed to a program (a script, a message), not commands."""
    out, pending = [], []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        pending += [m.group(2) for m in HEREDOC.finditer(line)]
    return "\n".join(out)


def segments(command: str):
    """Yield the token list of each simple command; quoted text stays one token (a commit message
    is never read as a command), except a string run by bash -c / sh -c / eval."""
    lex = shlex.shlex(strip_heredocs(command), posix=True, punctuation_chars=OPERATORS)
    lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
    try:
        tokens = list(lex)
    except ValueError:  # unbalanced quotes: judge it word by word
        tokens = re.split(r"([;&|()\n])|\s+", command)
        tokens = [t for t in tokens if t]
    seg = []
    for tok in tokens + ["\n"]:
        if tok and all(c in OPERATORS for c in tok):
            if seg:
                yield seg
                for k, t in enumerate(seg[:-1]):
                    if (os.path.basename(t) in SHELLS and seg[k + 1] == "-c" and k + 2 < len(seg)) or t == "eval":
                        yield from segments(seg[k + 2] if t != "eval" else " ".join(seg[k + 1:]))
            seg = []
        else:
            seg.append(tok)


def git_calls(tokens):
    """(repo_dir_or_None, subcommand, args) for every git invocation in one simple command."""
    for i, t in enumerate(tokens):
        if t != "git" and not t.endswith("/git"):
            continue
        j, repo = i + 1, None
        while j < len(tokens) and tokens[j].startswith("-"):
            opt = tokens[j]
            if opt in GLOBAL_WITH_VALUE:
                if opt == "-C" and j + 1 < len(tokens):
                    repo = tokens[j + 1]
                j += 2
            else:
                if opt.startswith("--git-dir=") or opt.startswith("--work-tree="):
                    repo = repo or opt.split("=", 1)[1]
                j += 1
        if j < len(tokens):
            yield repo, tokens[j], tokens[j + 1:]


def short_flags(args):
    """Letters of combined short options (-fu -> {'f','u'}), not of --long ones."""
    out = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--") and len(a) > 1:
            out.update(a[1:])
    return out


def uncommitted(repo, cwd):
    where = os.path.expanduser(repo) if repo else cwd
    if repo and not os.path.isabs(where):
        where = os.path.join(cwd, where)
    try:
        out = subprocess.run(["git", "-C", where, "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def problem(repo, sub, args, cwd):
    flags, longs = short_flags(args), {a.split("=", 1)[0] for a in args if a.startswith("--")}
    if sub == "push":
        if longs & {"--force", "--force-with-lease", "--force-if-includes", "--mirror"} or "f" in flags \
                or any(a.startswith("+") for a in args):
            return "Rule 4: never force-push. If GitHub refuses a push, pull (merge) first, then push."
        if "--delete" in longs or "d" in flags or any(a.startswith(":") for a in args if a != ":"):
            return "Rule 4: never delete a branch on GitHub from here; ask the owner."
    if sub == "reset" and ("--hard" in longs or "--merge" in longs):
        return "Rule 4: never reset --hard; it throws away uncommitted work. Commit first, or ask the owner."
    if sub == "clean":
        return "Rule 4: never git clean; it deletes files that were never committed."
    if sub == "checkout":
        resolving = longs & {"--ours", "--theirs"}
        if not resolving and ("--" in args or "." in args or "f" in flags or "--force" in longs):
            return "Rule 4: this checkout would overwrite uncommitted changes. Commit them first."
    if sub == "restore" and not ("--staged" in longs and not (longs & {"--worktree"} or "W" in flags)):
        return "Rule 4: git restore discards uncommitted changes. To unstage use git reset -q <path>; else commit first."
    if sub == "switch" and ("--discard-changes" in longs or "--force" in longs or "f" in flags):
        return "Rule 4: this switch discards uncommitted changes. Commit them first."
    if sub == "stash" and args and args[0] in ("drop", "clear"):
        return "Rule 4: dropping a stash throws work away; ask the owner."
    if sub == "branch" and ("D" in flags or (("d" in flags or "--delete" in longs) and ("f" in flags or "--force" in longs))):
        return "Rule 4: force-deleting a branch can lose commits; ask the owner."
    if sub == "rebase" or (sub == "pull" and ("--rebase" in longs or "r" in flags)):
        return "Rule 2: never rebase. Pull with merge (git pull --no-rebase) or use ./sync.sh down."
    if sub in ("pull", "merge") and not (longs & {"--abort", "--continue", "--quit"}) and uncommitted(repo, cwd):
        return "Rule 1: commit before every pull or merge; this repo has uncommitted changes."
    return None


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if event.get("tool_name") != "Bash":
        return
    command = (event.get("tool_input") or {}).get("command", "")
    cwd = event.get("cwd") or os.getcwd()
    for tokens in segments(command):
        for repo, sub, args in git_calls(tokens):
            reason = problem(repo, sub, args, cwd)
            if reason:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"git_master safety rules: {reason}",
                }}))
                return


if __name__ == "__main__":
    main()
