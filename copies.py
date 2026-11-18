#!/usr/bin/env python3
"""Back up folders that cannot be git repos themselves: another program's data, such as the agent board's skills,
their History and Recently deleted. git_master keeps a copy of them in a git repo of its own (a "copy repo") and backs
that up like every other repo, through the same gates; git's history holds every night's state. Nothing in the copied
folders is ever written by a backup.

A copy repo is an entry in config.json with "copies" (set with gm.py copy / copy-links):
  {"name": "board_skills", "path": "~/.local/share/git_master/board_skills",
   "copies": [{"from": "~/.local/share/agent-board/skills", "to": "skills"},
              {"from": "~/.local/state/agent_board/skills", "to": "board-records", "skip": ["/drafts/"]}],
   "links_in": ["~/.claude/skills", "~/projects/*/*/.claude/skills"]}

  copies.py refresh <name>   bring the copy up to date (sync.sh status and up do it first; other repos: nothing):
                             each "from" folder mirrored into <path>/<to> (nested .git folders and "skip" patterns
                             left out; a file gone there is removed here too, git keeps its older versions), and
                             links.tsv: the links in the "links_in" folders that point into a copied folder (the
                             board's skill links, kept out of the agents' own repos on purpose)
  copies.py restore <name> [--at <commit>] [--only <path in the copy>]...
                             put back what is missing: files into the "from" folders and the links from links.tsv.
                             Never overwrites or deletes anything there; prints what it put back and what it left
                             because something else is in its place. --at takes an earlier night's commit.
"""
import glob
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

import gm

HOME = pathlib.Path.home()
LINKS = "links.tsv"
README = "README.md"   # what the copy holds and how it is restored, for whoever finds it on GitHub
SKIP_ALWAYS = [".git"]   # a skill that is its own clone: its files are copied, its .git would make git skip them


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def expand(p):
    return pathlib.Path(os.path.expanduser(p))


def tilde(p):
    p = str(p)
    return "~" + p[len(str(HOME)):] if p == str(HOME) or p.startswith(str(HOME) + "/") else p


def entry(name):
    for p in gm.load()["projects"]:
        if p["name"] == name:
            return p
    die(f"not in the config: {name} (./gm.py list shows the names)")


def check_to(to):
    """A copy's place in the repo: a plain relative folder, never the repo's top or its .git."""
    parts = pathlib.PurePosixPath(to).parts
    if not to or to.startswith("/") or not parts or any(x in ("", ".", "..", ".git") for x in parts) \
            or to in (LINKS, README):
        die(f"bad place in the copy: {to!r} (a folder name like skills or board/records)")
    return to


def under(path, roots):
    path = os.path.realpath(path)
    return any(path == r or path.startswith(r + os.sep) for r in roots)


def refresh(name):
    e = entry(name)
    copies = e.get("copies") or []
    if not copies:
        return
    repo = gm.folder(e["path"])
    if not (repo / ".git").is_dir():
        die(f"{name}: {tilde(repo)} is not a git repo yet (./setup.sh add {tilde(repo)} {name})")
    if not any(expand(c["from"]).is_dir() for c in copies):
        die(f"{name}: none of the folders to copy is on this computer; nothing changed")
    for c in copies:
        src, dest = expand(c["from"]), repo / check_to(c["to"])
        if not src.is_dir():
            # not made yet (the board makes Recently deleted on the first delete), or emptied: mirrored as empty
            if dest.exists():
                shutil.rmtree(dest)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        # files, links as links, permissions (a program keeps its x), times; no devices, owners or groups
        args = ["rsync", "-rlpt", "--delete", "--delete-excluded"]
        for pat in SKIP_ALWAYS + list(c.get("skip") or []):
            args.append(f"--exclude={pat}")
        r = subprocess.run(args + [f"{src}/", f"{dest}/"], capture_output=True, text=True)
        if r.returncode:
            die(f"{name}: copying {tilde(src)} failed: {(r.stderr.strip().splitlines() or ['?'])[-1]}")
        writable(dest)
    write_links(e, repo)
    write_readme(e, repo)


def writable(top):
    """A read-only folder or file in a skill stays writable in the copy, so the next refresh and git can change it."""
    for root, dirs, files in os.walk(top):
        for n, need in [(d, stat.S_IRWXU) for d in dirs] + [(f, stat.S_IRUSR | stat.S_IWUSR) for f in files]:
            p = os.path.join(root, n)
            if not os.path.islink(p):
                mode = os.lstat(p).st_mode
                if mode & need != need:
                    os.chmod(p, stat.S_IMODE(mode) | need)


def write_readme(e, repo):
    rows = "".join(f"- `{c['to']}/`: `{c['from']}`" + (f" (without {', '.join(c['skip'])})" if c.get("skip") else "")
                   + "\n" for c in e.get("copies") or [])
    (repo / README).write_text(
        f"# {e['name']}\n\nA backup made by git_master: before every backup these folders are copied in "
        f"(copies.py), and each night's state stays in this repo's history.\n\n{rows}\n"
        + (f"`{LINKS}`: the links that point into them (an agent's skill), which are in no repo on purpose.\n\n"
           if e.get("links_in") else "")
        + f"Restore with git_master: `./copies.py restore {e['name']}` puts back what is missing (never overwrites); "
          f"`--at <commit> --only <path>` takes one thing from an earlier night. See git_master's CLAUDE.md.\n")


def write_links(e, repo):
    """links.tsv: every link in the links_in folders that points into a copied folder (place, then where it points)."""
    roots = [os.path.realpath(expand(c["from"])) for c in e.get("copies") or []]
    lines = []
    for pattern in e.get("links_in") or []:
        for d in sorted(glob.glob(os.path.expanduser(pattern))):
            if not os.path.isdir(d):
                continue
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if os.path.islink(p):
                    target = os.readlink(p)
                    if under(os.path.join(d, target), roots):
                        lines.append(f"{tilde(p)}\t{tilde(target)}\n")
    f = repo / LINKS
    if lines or f.exists():
        f.write_text("# Links into the copied folders (an agent's or every agent's skill), with where they point.\n"
                     "# Not in the agents' own repos on purpose; copies.py restore makes them again.\n" + "".join(lines))


def repo_top(path):
    """The git repo a path is in (its top folder), or None."""
    for d in [path.parent, *path.parent.parents]:
        if (d / ".git").is_dir():
            return d
        if d == HOME:
            return None
    return None


def keep_out_of_git(link):
    """A link made again inside a repo gets its line in that repo's .git/info/exclude, as the board does it."""
    top = repo_top(link)
    if not top:
        return
    f = top / ".git" / "info" / "exclude"
    line = "/" + str(link.relative_to(top))
    lines = f.read_text().splitlines() if f.exists() else []
    if line not in lines:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines + [line]) + "\n")


def restore(name, args):
    e = entry(name)
    copies = e.get("copies") or []
    if not copies:
        die(f"{name} is not a copy repo: restore it with git (./setup.sh clone {name})")
    repo = gm.folder(e["path"])
    if not (repo / ".git").is_dir():
        die(f"{name}: no copy here yet: ./setup.sh clone {name}")
    at = args[args.index("--at") + 1] if "--at" in args else None
    only = [args[i + 1].strip("/") for i, a in enumerate(args) if a == "--only"]
    tmp = None
    base = repo
    if at:   # an earlier night: that commit's files, unpacked aside (the copy itself is not changed)
        tmp = tempfile.mkdtemp(prefix=f"restore-{name}-")
        arch = subprocess.run(["git", "-C", str(repo), "archive", at], capture_output=True)
        if arch.returncode:
            die(f"no such commit in {name}: {at}")
        subprocess.run(["tar", "-x", "-C", tmp], input=arch.stdout, check=True)
        base = pathlib.Path(tmp)
    try:
        put, left = 0, []
        for c in copies:
            to = check_to(c["to"])
            src, dest = base / to, expand(c["from"])
            if not src.is_dir():
                continue
            for root, dirs, files in os.walk(src):
                rel_root = os.path.relpath(root, src)
                for f in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
                    rel = os.path.normpath(os.path.join(rel_root, f))
                    in_copy = f"{to}/{rel}"
                    if only and not any(in_copy == o or in_copy.startswith(o + "/") for o in only):
                        continue
                    s, t = os.path.join(root, f), dest / rel
                    if os.path.lexists(t):
                        if not os.path.islink(s) and os.path.isfile(t) and not same(s, t):
                            left.append(tilde(t))
                        continue
                    t.parent.mkdir(parents=True, exist_ok=True)
                    if os.path.islink(s):
                        os.symlink(os.readlink(s), t)
                    else:
                        shutil.copy2(s, t)
                    put += 1
        print(f"put back {put} file(s)" + (f"; left {len(left)} that differ (something else is there now):" if left else ""))
        for t in left:
            print(f"  {t}")
        if (base / LINKS).exists():
            restore_links(base / LINKS, None if not only or LINKS in only else live_paths(only, copies))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def same(a, b):
    try:
        return pathlib.Path(a).read_bytes() == pathlib.Path(b).read_bytes()
    except OSError:
        return False


def live_paths(only, copies):
    """The folders on this computer that --only names in the copy (skills/<name> -> the store's <name>)."""
    out = []
    for o in only:
        for c in copies:
            to = c["to"].strip("/")
            if o == to or o.startswith(to + "/"):
                out.append(os.path.normpath(os.path.join(os.path.expanduser(c["from"]), o[len(to):].lstrip("/"))))
    return out


def restore_links(links, wanted):
    """Make the links in links.tsv again where nothing is in their place (wanted: only those pointing in there)."""
    made, taken = 0, []
    for line in links.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        place, target = line.split("\t", 1)
        target = os.path.expanduser(target)
        t = os.path.normpath(target)
        if wanted is not None and not any(t == w or t.startswith(w + "/") for w in wanted):
            continue
        p = expand(place)
        if os.path.lexists(p):
            if not (os.path.islink(p) and os.readlink(p) == target):
                taken.append(place)
            continue
        owner = str(p).split("/.claude/")[0] if "/.claude/" in str(p) else str(p.parent)
        if not os.path.isdir(owner):
            taken.append(f"{place} (its folder is not on this computer)")
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p)
        keep_out_of_git(p)
        made += 1
    print(f"made {made} link(s) again" + (f"; left {len(taken)}:" if taken else ""))
    for t in taken:
        print(f"  {t}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("refresh", "restore"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    if sys.argv[1] == "refresh":
        refresh(sys.argv[2])
    else:
        restore(sys.argv[2], sys.argv[3:])
