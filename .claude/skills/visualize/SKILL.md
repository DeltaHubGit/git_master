---
name: visualize
description: Show a project's git history as a Sourcetree-style graph page (lanes, branch pills, ahead/behind, hollow = not pushed yet, click a commit to see its files). Use when asked to "visualize/visualise my project", "show the history", "show the git graph" or "what changed".
---

# Visualize a repo's history

1. Build the page: `python3 visualize.py [project]` from the git_master folder. `project` is a name from
   `~/.git_master/config.json` (`./gm.py list`) or a path; no argument draws git_master's own code.
   `--limit N` draws more than the newest 400 commits. It writes `~/.git_master/visualize/<project>.html`
   (local) and prints a one-line summary. Read-only.
2. Publish it with the Artifact tool, one private page per project, always the same link:
   - Look up the project in `~/.git_master/visualize/urls.json`.
   - Known: publish `file_path` = the html with `url` = that link. If the publish is refused because
     this conversation has not read it, the refusal counts as the read: publish the same file again.
   - New: publish with `icon: "graph"` and a one-sentence `description`, then add
     `"<project>": "<link>"` to `urls.json`.
3. Answer with the link and the summary line: commits drawn, lanes, how many are only on this
   computer, ahead/behind, uncommitted files.

The page shows commit titles and messages, file paths with +/- line counts, and author names, never
file contents. It is private; share it only when its owner asks.

How to read it (tell people who are new to it): a dot is a commit, newest on top; full dot = on GitHub,
hollow dot = committed only on this computer; a two-colour dot is a merge; pills are branches
(dashed = GitHub's copy, e.g. `origin/main`); "3 ahead" = three commits to push. Clicking a row shows
the files it changed; "Copy for your agent" copies `<short hash> <title>` to paste into a request.
