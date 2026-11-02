---
name: show-diff
description: Show a merge conflict or a diff as an IDE-style side-by-side page (like Rider's merge window): this computer | result | theirs, changed lines coloured, changed words marked. Use whenever someone asks to "show me the conflict", "where is the conflict", "what conflicts", "show me the diff", "what's different", "compare the versions", "what changed in this file", or before asking them to choose between two versions of a file.
---

# Show a conflict or a diff

Always answer these questions with this page, never with a text diff or a description alone: the page is
what the person looks at to decide. Short words in the chat can go with it (which file, what the choice is).

1. Find git_master's folder: `readlink -f <this skill's folder>/../../..` (on a shared machine it is
   `/opt/git_master`). Run the commands below from there.
2. Build the page:
   - **A conflict** (a held repo, `sync.sh down` stopped, a merge in progress, a branch to merge):
     `python3 diffview.py merge <project> [theirs] [--ours REF] [--theirs-first]`. Default left = this
     computer (HEAD); right = the merge in progress, else GitHub's copy (`origin/<branch>`, the `down`
     conflict). For a branch: `merge <project> <branch>`. To show a conflict that is already resolved, pass
     the commits it was between (`--ours <commit before the merge>`, theirs = the branch).
     Where both sides only added lines at the same place, the middle proposes keeping both, this computer's
     first; `--theirs-first` when the other side's lines belong first (a dated log: the older first).
   - **A diff**: `python3 diffview.py diff <project> [from] [to]`. Nothing given: uncommitted changes. One
     commit: that commit against the folder. Two: between them.
   `<project>` is a name from `./gm.py list`, `git_master`, or a path. It prints the page's path and a
   summary line. Read-only on the repo.
3. Publish it with the Artifact tool, one private page per project and kind, always the same link:
   - Look up `<project>-conflict` or `<project>-diff` in `~/.git_master/visualize/urls.json`.
   - Known: publish `file_path` = the html with `url` = that link. If the publish is refused because this
     conversation has not read it, the refusal counts as the read: publish the same file again.
   - New: publish with `icon: "code"` and a one-sentence `description`, then add the link to `urls.json`.
4. Answer with the link and, in a few lines: which files conflict, what each side did there, and the
   choice they have (left, right, keep both, or a mix). Never pick a side for them (CLAUDE.md, Conflicts).

How to read it (tell people who are new to it): left pane = this computer, right pane = the other side
(GitHub or the branch), middle = the file after merging. Orange = conflict, blue = changed on that side,
green = what the result takes, hatched = nothing on that side, grey = unchanged. Changed words are
highlighted. "N unchanged lines" opens the folded stretch. On a phone the panes scroll sideways.

The page shows file contents (the changed lines and a few around them), unlike the history page. Key files
(`.env`, keys, `credentials/`) are never shown, binary files (Word, Excel, PDF) are named but not drawn,
and a page where gitleaks finds a secret is not written: run `./sync.sh up <project>` and deal with the
finding first. The page is private; share it only when its owner asks.
