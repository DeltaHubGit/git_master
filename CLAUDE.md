# git_master

Backs up the git repos of the person using it to private GitHub repos, with safety checks before
anything leaves the computer, and draws their history. This folder holds **only code**; never put
anything personal in it. Each person's setup lives in their own `~/.git_master/`:

- `config.json`: the repos to look after, each a folder anywhere on the computer that is its own
  git clone (`name`, `path`, and `github` when its online copy is not `github_owner/name`), plus
  `github_owner`, `git_name`/`git_email` for new repos, `watch_dirs` (folders to check for repos not
  backed up yet) and optional `slack_post` for the nightly summary. Read and edit it with `./gm.py`
  (`list`, `add`, `remove`, `scan`), never by hand. Template: `config.example.json`.
- `rules/<name>.exclude`: gitignore patterns kept on this computer only (never pushed).
- `rules/<name>.allow`: exact paths reviewed as OK despite a sensitive-looking filename.
- `gitleaks-allow/<name>.gitleaksignore`: verified gitleaks false positives.
- `reports/`, `conflicts/`, `visualize/`: local output.
- `NOTES.md`: the person's own notes for the agent, read with this file through the import below.
  Personal facts and decisions go there, not here.

The person's notes: @~/.git_master/NOTES.md

Code: `setup.sh` (first run, `add`, `clone`), `sync.sh` (status / up / down), `push.sh`,
`ignore.sh` (keep files out), `nightly.sh`, `gm.py` (the config), `visualize.py`, `commit_message.py`, `audit_pii.py`,
`safety.gitignore` (credential/cache rules for every repo), `hooks/git_guard.py`. `tools/` holds
`gh` and `gitleaks` for this computer (downloaded by setup.sh, not in git).

On a computer several people share, the admin installs this folder once (e.g. `/opt/git_master`, with
`tools/`), read-only for everyone else; each person's `~/.git_master/` stays their own. Nothing in this
folder is written while it runs, so one copy serves everyone, and only the admin changes or updates it.

## First run: "set me up"

If `~/.git_master/config.json` is missing (every script then says "not set up yet"), this is a new
person. Walk them through it, one step at a time, in plain words:

1. `./setup.sh`: installs the tools, logs in to GitHub in the browser, creates `~/.git_master/`.
   On a Mac the first git command may ask to install Apple's developer tools: tell them to click
   Install and wait a few minutes.
2. Find their folders: `./gm.py scan ~/projects ~/Documents` (or wherever they work) lists folders,
   whether each is a git repo, and its GitHub copy. Ask with AskUserQuestion (multi-select) which to
   back up. Add only what they pick; suggest leaving out folders with videos, downloads or other
   people's files.
3. For each picked folder: `./setup.sh add <folder> [name]`. It never changes or deletes files; it
   runs `git init` where there is no repo yet and creates a private GitHub repo as the online copy.
   Ask whether to remember the parent folders as `watch_dirs` (to spot new folders later).
4. First backup of each: `./nightly.sh --no-slack <name>` (three gates, PII audit, commit, push).
   Anything it holds needs their decision (see "Held repos").
5. Their own setup should be backed up too: `./setup.sh add ~/.git_master git_master_config`.
6. Optional, on a Linux computer that is always on: `./nightly.sh install` backs everything up at
   01:00. Only one computer per set of repos runs it. Otherwise they say "back up my work".
7. Tell them what to say from now on: "back up my work", "get the latest changes", "visualize my
   project", "keep this file out of git". Anything personal you learn goes into
   `~/.git_master/NOTES.md`.

## Commit messages

Before every commit in a session the person drives, ask for the message with AskUserQuestion: the
first option is your proposed message in full, a short description of what is being committed; they
pick it or type their own under "Other". Commits made together can share one question. When nobody
is watching (the nightly, background jobs), write a message that says what changed; the nightly uses
`commit_message.py` for that ("Sync from <machine> <date>: <what changed>").

## Safety rules: no work is ever lost

1. **Commit before every pull.** `sync.sh down` refuses while tracked files have uncommitted
   changes; the order is always up (gates), commit, down, push. Committed work can always be
   recovered.
2. **Pull with merge, never rebase.** If both this computer and GitHub have new commits, `down`
   merges them (a "Merge GitHub changes into <machine>" commit); both sides are kept.
3. **Same lines changed on both sides: stop, keep both versions, ask.** `down` saves GitHub's
   version in `~/.git_master/conflicts/<name>/<path>`, undoes the merge (this computer's commit
   stays as it was) and stops; the nightly holds the repo. Resolve as in "Conflicts".
4. **Never force anything.** No `push --force`/`-f`/`+refspec`, `reset --hard`, `git clean`,
   discarding uncommitted changes (`checkout --`, `checkout .`, `restore`, `switch
   --discard-changes`), dropping stashes, force-deleting branches, rebasing. Enforced for Claude
   by `hooks/git_guard.py` (PreToolUse, registered in `.claude/settings.json`, with a matching deny
   list) and in the scripts' own code. To unstage use `git reset -q <path>`. Test the hook after
   changing it: a sample of allowed and refused commands piped into it.
5. **GitHub protects main** (no force-push, no deletion, owner included): `push.sh` switches it on
   after every push, `./push.sh --protect [names]` without pushing. Private repos need GitHub Pro
   (or an organisation plan); on the Free plan it prints "needs GitHub Pro" and moves on.
6. **Push only to private repos**, and only to the repo's own GitHub copy (`push.sh` checks both).

## Back up ("back up my work")

1. `./sync.sh status`: what changed since the last backup, per repo.
2. `./sync.sh up [names]`: stages, and runs three gates: sensitive-looking filenames, IBAN-shaped
   text, gitleaks (incl. inside .docx/.xlsx). A repo that trips a gate is left unstaged with the
   reason printed.
3. `python3 audit_pii.py <folder>` on repos with new/changed PDF, Word or Excel files. Gates can't
   see text inside PDFs; this finds valid IBANs, card numbers, EMŠO, bank/ID keywords.
4. Any real bank, identity or credential data: **ask the owner** before it goes up. Default is to
   keep it local via `rules/<name>.exclude`; a pushed file stays in git history. False positives go
   in `rules/<name>.allow` (filenames) or `gitleaks-allow/` (gitleaks).
5. Commit each staged repo (ask for the message, see "Commit messages").
6. `./sync.sh down [names]`: merges in whatever GitHub got meanwhile (safety rules 1-3).
7. `./push.sh [names]`: re-scans the full history, refuses anything not private, plain push only.
8. `./sync.sh status` again: every repo should read "in sync" and "identical".

## Get the latest ("update this computer")

1. `./sync.sh status`. Uncommitted work: back it up first, since `down` refuses to pull over it.
2. `./sync.sh down [names]`: fast-forward, or a merge when both sides have new commits.

## Keep files out ("ignore this file", "don't include this", "add it to .gitignore")

1. Work out the repo and the pattern, and show the owner what it covers: `raw-photos/` a folder
   anywhere, `/raw-photos/` only the top one, `*.zip` every zip file, `/briefs/old-draft.md` one
   exact file (the path from the repo's top folder).
2. `./ignore.sh <name> <pattern>...` adds it to the repo's `.gitignore`, which goes to GitHub, so
   everyone working on the repo keeps it out too. Use `--local` instead when it concerns only this
   person, or when the name itself should not be on GitHub (a client's name, "passwords"): the line
   goes to `~/.git_master/rules/<name>.exclude` and never leaves the computer.
3. If it says **ALREADY IN THE REPO**, the list does nothing for those files until they are
   untracked. Ask the owner (AskUserQuestion) before `./ignore.sh --untrack <name> <pattern>...`,
   and say what it does: the files stay in their folder and leave the repo with the next commit;
   on other people's computers they disappear at their next pull (still recoverable from history);
   older versions in the history keep them.
4. Commit (ask for the message), then `./push.sh <name>`.

Already pushed something sensitive? A password, key or token counts as leaked: tell the owner to
change it or revoke it first. Removing a file from every old version means rewriting history and
force-pushing, which breaks rule 4 and makes everyone re-clone: never do it yourself; explain it and
leave the decision and the work to the owner.

## Conflicts

When `down` (or the nightly) stops on a conflict: show the owner both versions side by side, theirs
in the repo folder (committed) and GitHub's in `~/.git_master/conflicts/<name>/<path>`, and let them
pick A (this computer), B (GitHub) or a mix. Then: `git -C <dir> merge origin/main`, for each file
`git -C <dir> checkout --ours -- <path>` (A), `--theirs` (B) or write the combined text,
`git -C <dir> add <path>`, `git -C <dir> commit --no-edit`, then `./push.sh <name>`. Delete
`conflicts/<name>/` afterwards. Never pick a side for them. Markdown merges line by line, so only the
same line (paragraph) on both sides conflicts; Word, Excel and PDF files conflict as a whole file.

## Held repos (nightly)

`nightly.sh` runs, per repo: `sync.sh up` (gates), `audit_pii.py --list` over new or changed
documents (paths in `rules/<name>.allow` are accepted), commit, `sync.sh down`, `push.sh`. A repo
that trips any of these is **held**: nothing pushed, only its name in the summary (never filenames
or reasons in Slack). A gate hold leaves the changes uncommitted; a conflict hold leaves them
committed with GitHub's version in `conflicts/`. Full log: `~/routine-output/git-backup/<date>/
report.txt` (`latest/`), held names in `held.txt`; journal `journalctl --user -u git-backup -n 80`.
Folders in `watch_dirs` that the config does not list are reported as "Not in the backup at all".

When the owner asks about a held repo: read `latest/report.txt`; a conflict: see "Conflicts";
otherwise show what was flagged and let them decide between `rules/<name>.exclude` (keep local),
`rules/<name>.allow` (reviewed OK) or a gitleaks false positive in `gitleaks-allow/`. Then run
`./nightly.sh --no-slack <name>` or let the next night pick it up.

## Visualize ("show me the history")

`python3 visualize.py [name]` draws a repo's history as a Sourcetree-style page (lanes, branch
pills, ahead/behind; hollow dot = not on GitHub yet; click a commit for its message and files;
"Uncommitted changes" lists the working files). The `visualize` skill publishes it as the repo's
private page; links are kept in `~/.git_master/visualize/urls.json`.

## Adding, removing, another computer

- Add a folder: `./setup.sh add <folder> [name]`, then `./nightly.sh --no-slack <name>`.
- Stop backing one up: `./gm.py remove <name>` (folder and GitHub repo stay).
- Another computer: clone git_master, then their setup if it was backed up
  (`gh repo clone <owner>/git_master_config ~/.git_master`), then `./setup.sh`, then
  `./setup.sh clone <name>` for each repo they want there. `.env` files and other secrets are never
  in any repo: copy them over separately.

## Learnings

Whenever the owner corrects something, a task teaches something, or an assumption turns out wrong,
save it in your Claude memory for this folder right away; move it into `~/.git_master/NOTES.md`
once it is a settled rule for this person, or into this file once it holds for everyone.
