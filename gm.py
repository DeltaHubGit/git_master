#!/usr/bin/env python3
"""git_master's per-user setup: which repos it looks after, and where they are.

Everything personal lives outside the code, in ~/.git_master/ (or $GIT_MASTER_HOME):
  config.json   github_owner, git identity, the repos (name + folder, anywhere on disk), ...
  rules/        <name>.exclude (keep local) and <name>.allow (reviewed OK) per repo
  gitleaks-allow/, reports/, conflicts/, visualize/, NOTES.md (personal notes for the agent)

  gm.py init [--owner NAME]       create ~/.git_master with an empty config (first run)
  gm.py list                      the repos it looks after
  gm.py add <folder> [--name N]   add a folder (it does not touch the folder; setup.sh add does)
  gm.py remove <name>             stop looking after a repo (the folder and GitHub stay)
  gm.py scan [folders...]         folders under watch_dirs (or the given ones) not in the config yet
  gm.py get <key>                 one setting, for the shell scripts
  gm.py set <key> <value>         change a setting: github_owner (your account or an organization you are in, where
                                  new online copies go), git_name, git_email, slack_post (JSON; "" removes it)
  gm.py watch [folders...]        add folders to watch_dirs (where scan looks); without folders: list them
  gm.py unwatch <folders...>      take folders off watch_dirs
  gm.py github <name> <owner/repo>  set a repo's online copy (setup.sh add and move use it)
  gm.py tsv [names...]            "name<TAB>folder" per repo, for the shell scripts
  gm.py slug <name>               its GitHub repo, owner/name (the folder's own, or github_owner/name)
"""
import json
import os
import pathlib
import re
import subprocess
import sys

HOME = pathlib.Path(os.path.expanduser(os.environ.get("GIT_MASTER_HOME", "~/.git_master")))
CONFIG = HOME / "config.json"
LOCAL_ONLY = ["reports/", "conflicts/", "visualize/", "tools/"]


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load():
    if not CONFIG.exists():
        die(f"git_master is not set up on this computer yet ({CONFIG} is missing): run ./setup.sh")
    try:
        cfg = json.loads(CONFIG.read_text())
    except json.JSONDecodeError as e:
        die(f"{CONFIG} is not valid JSON: {e}")
    cfg.setdefault("projects", [])
    return cfg


def save(cfg):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(CONFIG)


def folder(path):
    return pathlib.Path(os.path.expanduser(path)).resolve()


def short(p):
    home = pathlib.Path.home()
    return "~/" + str(p.relative_to(home)) if p.is_relative_to(home) else str(p)


def cmd_init(args):
    owner = args[args.index("--owner") + 1] if "--owner" in args else ""
    for d in ("rules", "gitleaks-allow"):
        (HOME / d).mkdir(parents=True, exist_ok=True)
    gi = HOME / ".gitignore"
    if not gi.exists():
        gi.write_text("# Local only: scan output, pull conflicts, generated pages\n" + "\n".join(LOCAL_ONLY) + "\n")
    if CONFIG.exists():
        print(f"{CONFIG} already exists")
        return
    name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()
    save({"github_owner": owner, "git_name": name, "git_email": email,
          "watch_dirs": [], "projects": []})
    print(f"created {CONFIG}")


def cmd_list(args):
    cfg = load()
    if not cfg["projects"]:
        print("no repos yet: add one with ./setup.sh add <folder>")
    for p in cfg["projects"]:
        f = folder(p["path"])
        state = "ok" if (f / ".git").exists() else ("folder missing" if not f.exists() else "not a git repo yet")
        print(f"{p['name']:24s} {short(f):48s} {state}")


def cmd_add(args):
    if not args:
        die("usage: gm.py add <folder> [--name N]")
    f = folder(args[0])
    name = args[args.index("--name") + 1] if "--name" in args else f.name
    if not f.is_dir():
        die(f"{f} is not a folder")
    if f == pathlib.Path(__file__).resolve().parent:
        die("that is git_master's own code folder; it is not one of the repos it backs up")
    cfg = load()
    for p in cfg["projects"]:
        if p["name"] == name:
            die(f"a repo called {name!r} is already listed ({p['path']}); pick another --name")
        if folder(p["path"]) == f:
            die(f"{short(f)} is already listed as {p['name']!r}")
    entry = {"name": name, "path": short(f)}
    origin = github_of(f)
    if origin:  # already has a GitHub copy (maybe a team's): back up there, not to github_owner
        entry["github"] = origin
    cfg["projects"].append(entry)
    save(cfg)
    print(f"added {name}: {short(f)}" + (f" (GitHub: {origin})" if origin else ""))


def github_of(f):
    """owner/name of a folder's GitHub origin, if it has one."""
    if not (f / ".git").exists():
        return ""
    # the address as configured (get-url would apply url.*.insteadOf rewrites)
    url = subprocess.run(["git", "-C", str(f), "config", "--get", "remote.origin.url"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def cmd_slug(args):
    cfg = load()
    for p in cfg["projects"]:
        if args and p["name"] == args[0]:
            owner = cfg.get("github_owner", "")
            print(p.get("github") or (f"{owner}/{p['name']}" if owner else ""))
            return
    die(f"no repo called {args[0] if args else ''!r}")


def cmd_remove(args):
    cfg = load()
    keep = [p for p in cfg["projects"] if p["name"] not in args]
    if len(keep) == len(cfg["projects"]):
        die(f"no repo called {' '.join(args)!r}")
    cfg["projects"] = keep
    save(cfg)
    print(f"removed {' '.join(args)} (the folder and its GitHub repo are untouched)")


def cmd_scan(args):
    cfg = load()
    roots = [folder(a) for a in args] or [folder(d) for d in cfg.get("watch_dirs", [])]
    if not roots:
        die("no folders to look in: give some (gm.py scan ~/projects) or set watch_dirs in the config")
    listed = {folder(p["path"]) for p in cfg["projects"]}
    code = pathlib.Path(__file__).resolve().parent
    for root in roots:
        for d in sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith(".")) if root.is_dir() else []:
            if d in listed or d == code:
                continue
            origin = ""
            if (d / ".git").exists():
                origin = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"],
                                        capture_output=True, text=True).stdout.strip() or "git repo, no GitHub copy"
            print(f"{short(d):48s} {origin or 'not a git repo'}")


def cmd_get(args):
    value = load().get(args[0] if args else "", "")
    print(value if isinstance(value, str) else json.dumps(value))


SETTINGS = {"github_owner": "your GitHub account, or an organization you are in",
            "git_name": "the name on new commits", "git_email": "the email on new commits",
            "slack_post": "the nightly summary's Slack poster, as JSON"}


def cmd_set(args):
    if len(args) != 2 or args[0] not in SETTINGS:
        die("usage: gm.py set <key> <value>, key one of: " + ", ".join(f"{k} ({v})" for k, v in SETTINGS.items()))
    key, value = args
    cfg = load()
    if key == "slack_post":
        if not value:
            cfg.pop(key, None)
            save(cfg)
            print("slack_post removed: the nightly sends no summary")
            return
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            die(f"slack_post must be JSON, like the one in config.example.json: {e}")
    elif key == "github_owner":
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", value):
            die(f"{value!r} is not a GitHub account or organization name")
        # Repos already listed keep the online copy they have (a repo without "github" follows github_owner, and its
        # pushes would stop: "origin is not ..."). To move one too: setup.sh move.
        kept = []
        for p in cfg["projects"]:
            if not p.get("github"):
                slug = github_of(folder(p["path"])) or (f"{cfg['github_owner']}/{p['name']}" if cfg.get("github_owner") else "")
                if slug and slug.split("/")[0].lower() != value.lower():
                    p["github"] = slug
                    kept.append(f"{p['name']} ({slug})")
        if kept:
            print("these stay where they are (./setup.sh move <name> " + value + " to move one): " + ", ".join(kept))
    cfg[key] = value
    save(cfg)
    print(f"{key} = {json.dumps(value, ensure_ascii=False)}")


def cmd_watch(args):
    cfg = load()
    dirs = cfg.setdefault("watch_dirs", [])
    for a in args:
        f = folder(a)
        if not f.is_dir():
            die(f"{f} is not a folder")
        if short(f) not in [short(folder(d)) for d in dirs]:
            dirs.append(short(f))
    if args:
        save(cfg)
    print("watch_dirs: " + (", ".join(dirs) if dirs else "none (add some: gm.py watch ~/projects)"))


def cmd_unwatch(args):
    cfg = load()
    gone = {short(folder(a)) for a in args}
    before = cfg.get("watch_dirs", [])
    cfg["watch_dirs"] = [d for d in before if short(folder(d)) not in gone]
    if len(cfg["watch_dirs"]) == len(before):
        die(f"not in watch_dirs: {' '.join(args)}")
    save(cfg)
    print("watch_dirs: " + (", ".join(cfg["watch_dirs"]) or "none"))


def cmd_github(args):
    if len(args) != 2 or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", args[1]):
        die("usage: gm.py github <name> <owner/repo>")
    cfg = load()
    for p in cfg["projects"]:
        if p["name"] == args[0]:
            p["github"] = args[1]
            save(cfg)
            print(f"{args[0]}: online copy {args[1]}")
            return
    die(f"no repo called {args[0]!r}")


def cmd_tsv(args):
    cfg, want = load(), set(args)
    unknown = want - {p["name"] for p in cfg["projects"]}
    if unknown:
        die(f"not in the config: {' '.join(sorted(unknown))} (./gm.py list shows the names)")
    for p in cfg["projects"]:
        if not want or p["name"] in want:
            print(f"{p['name']}\t{folder(p['path'])}")


if __name__ == "__main__":
    cmds = {"init": cmd_init, "list": cmd_list, "add": cmd_add, "remove": cmd_remove,
            "scan": cmd_scan, "get": cmd_get, "tsv": cmd_tsv, "slug": cmd_slug, "set": cmd_set,
            "watch": cmd_watch, "unwatch": cmd_unwatch, "github": cmd_github}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    cmds[sys.argv[1]](sys.argv[2:])
