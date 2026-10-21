# git_master

An agent that backs up your project folders to private GitHub repos, checks for passwords, bank
details and keys before anything leaves your computer, never forces or overwrites anything, and
shows your history as a Sourcetree-style graph.

## Start

1. Ask Claude: "Clone https://github.com/deltahubTadej/git_master into my projects folder".
2. Open the new `git_master` folder in Claude Code and say **"set me up"**. It installs what it
   needs, logs you in to GitHub in the browser, and asks which folders to back up.
3. From then on: **"back up my work"**, **"get the latest changes"**, **"visualize my project"**.

On a Mac, the first git command may ask to install Apple's developer tools: click Install.
Needs git and Python 3 (both come with macOS and most Linux systems).

## What it does and never does

- Your own setup (which folders, your decisions) lives in `~/.git_master/`, never in this folder,
  so this repo stays the same for everyone.
- Every repo it creates is private. It pushes only to that repo's own GitHub copy.
- Before a push it runs three checks: sensitive-looking file names, IBANs in text, and
  [gitleaks](https://github.com/gitleaks/gitleaks) for keys and passwords. If something looks
  sensitive, it stops and asks you.
- It commits before every pull, merges instead of rebasing, and on a real conflict keeps both
  versions and asks which one is right. It never force-pushes, resets or deletes your work.

The full instructions the agent follows are in [CLAUDE.md](CLAUDE.md).
