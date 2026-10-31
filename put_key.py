#!/usr/bin/env python3
"""Put an API key into an agent's .env without anyone reading it: not the agent, not the chat.

The person saves the key on their laptop as a plain text file named <NAME>.txt in their bridge folder (it syncs to
~/bridge_folder here). This script takes it from there, writes NAME=<key> into each agent folder's .env (mode 600,
replacing an older value), makes sure git keeps .env out, deletes the key file (so it also disappears from the laptop)
and prints only the name and how many characters the key has. It never prints the key.

  put_key.py NAME <agent folder>...           take ~/bridge_folder/NAME.txt (waits up to a minute for it to arrive)
  put_key.py NAME <folder>... --from <file>   take the key from another file (a path, or a name in the bridge folder)
  put_key.py NAME <folder>... --keep          leave the key file where it is afterwards
  put_key.py --check NAME <folder>...         is NAME set in each folder's .env? (yes/no and length, never the key)

Only folders in your own home, owned by you, and never a shared agent. Exit: 0 done, 1 refused (the reason is
printed), 2 wrong arguments.
"""
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import time

HOME = pathlib.Path(os.path.expanduser("~")).resolve()
BRIDGE = HOME / "bridge_folder"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SAFE_RE = re.compile(r"^[A-Za-z0-9_\-.:/+=@,]+$")   # written unquoted; anything else goes in single quotes
MAX_KEY = 4096
WAIT_SECONDS = 60


class Refused(Exception):
    pass


def line_re(name):
    return re.compile(r"^\s*(export\s+)?" + re.escape(name) + r"\s*=")


def say(text):
    print(text, flush=True)


def show(path):
    """A path as the person knows it: ~/... inside their home."""
    try:
        return "~/" + str(pathlib.Path(path).relative_to(HOME))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- the key file

def find_source(name, given, wait):
    """The key file: --from, else NAME.txt (or NAME) in the bridge folder, waiting for Syncthing to bring it."""
    if given:
        p = pathlib.Path(os.path.expanduser(given))
        if not p.is_absolute():
            p = BRIDGE / p
        candidates = [p]
    else:
        if not BRIDGE.is_dir():
            raise Refused("There is no bridge folder (~/bridge_folder) on this computer. Save the key in a file and "
                          "run this again with --from <that file>.")
        candidates = [BRIDGE / f"{name}.txt", BRIDGE / name]
        for rich in (BRIDGE / f"{name}.rtf", BRIDGE / f"{name}.rtfd", BRIDGE / f"{name}.docx"):
            if rich.exists():
                raise Refused(f"{show(rich)} is a formatted document, not plain text. Save the key again as plain "
                              f"text named {name}.txt (TextEdit: Format > Make Plain Text before saving), and delete "
                              f"{rich.name}.")
    deadline = time.monotonic() + wait
    told = False
    while True:
        for c in candidates:
            if c.is_symlink() or c.exists():
                return c
        if time.monotonic() >= deadline:
            if given:
                raise Refused(f"There is no file at {show(candidates[0])}.")
            raise Refused(f"The key file is not in the bridge folder yet. On the laptop it goes into the bridge "
                          f"folder (in Documents, its name ends in _bridge_folder), named exactly {name}.txt. "
                          f"Then run this again.")
        if not told:
            say(f"Waiting up to {wait} seconds for {name}.txt to arrive in the bridge folder...")
            told = True
        time.sleep(2)


def read_key(src, name):
    """The key from the file, checked; never shown, also not in an error."""
    if src.is_symlink() or not src.is_file():
        raise Refused(f"{show(src)} is not a plain file (a link or a folder); save the key as a normal file.")
    real = src.resolve()
    if HOME not in real.parents:
        raise Refused(f"{show(src)} is outside your home folder; put it in the bridge folder.")
    st = src.stat()
    if st.st_uid != os.getuid():
        raise Refused(f"{show(src)} belongs to someone else.")
    if st.st_size > 16384:
        raise Refused(f"{show(src)} is too big to be one key ({st.st_size} bytes).")
    raw = src.read_bytes()
    if raw.startswith(b"{\\rtf"):
        raise Refused(f"{show(src)} is rich text, not plain text. Save it again as plain text (TextEdit: Format > "
                      f"Make Plain Text before saving).")
    if raw.startswith(b"PK\x03\x04"):
        raise Refused(f"{show(src)} is a document (Word or similar), not plain text.")
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused(f"{show(src)} is not plain text (it could not be read as text).") from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise Refused(f"{show(src)} is empty. Copy the key again and save it into the file.")
    if len(lines) > 1:
        raise Refused(f"{show(src)} has {len(lines)} lines; an .env takes a key of one line. If it is a whole "
                      f"credentials file (a .json or a certificate), it does not go into .env: ask the admin.")
    value = lines[0]
    m = re.match(r"^(export\s+)?" + re.escape(name) + r"\s*=\s*(.*)$", value)
    if m:                                   # they saved the whole line NAME=... : keep only the key
        value = m.group(2).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    if not value:
        raise Refused(f"{show(src)} holds no key.")
    if len(value) > MAX_KEY:
        raise Refused(f"The key in {show(src)} is longer than {MAX_KEY} characters; that is not one key.")
    if any(ord(ch) > 126 for ch in value):
        raise Refused(f"The key in {show(src)} has characters a key never has (curly quotes or similar from a word "
                      f"processor?). Copy it again from the service's page into a plain text file.")
    if any(ord(ch) < 32 for ch in value):
        raise Refused(f"The key in {show(src)} has tabs or other invisible characters; copy it again.")
    if not SAFE_RE.match(value) and "'" in value:
        raise Refused(f"The key in {show(src)} has both a ' and other special characters, which .env files cannot "
                      f"hold reliably. Ask the admin.")
    return value


# ---------------------------------------------------------------- the agent folder

def check_folder(arg):
    p = pathlib.Path(os.path.expanduser(arg))
    if not p.is_absolute():
        p = pathlib.Path.cwd() / p
    real = p.resolve()
    if not real.is_dir():
        raise Refused(f"{arg}: no such folder.")
    if real == HOME or HOME not in real.parents:
        raise Refused(f"{arg}: only an agent folder inside your own home (e.g. ~/projects/work/<agent>).")
    for no in (HOME / "projects" / "shared", BRIDGE, HOME / ".git_master"):
        if real == no or no in real.parents:
            raise Refused(f"{arg}: not in {show(no)}; only your own agents' folders.")
    if real.stat().st_uid != os.getuid():
        raise Refused(f"{arg}: this folder is not yours.")
    if not os.access(real, os.W_OK):
        raise Refused(f"{arg}: you cannot write in this folder (a shared agent?).")
    return real


def git(folder, *args):
    try:
        return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def keep_out_of_git(folder, env):
    """Refuse when .env is in the repo already; make sure git ignores it otherwise. Returns a note or ''."""
    top = git(folder, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0:
        return ""                           # not a git repo (git_master's own rules cover it once it is added)
    rel = os.path.relpath(env, top.stdout.strip())
    tracked = git(folder, "ls-files", "--error-unmatch", "--", rel)
    if tracked is not None and tracked.returncode == 0:
        raise Refused(f"{show(env)} is in the agent's git repo, so a key in it would go to GitHub. Take it out of "
                      f"the repo first (git_master: ./ignore.sh --untrack <repo> .env), then run this again.")
    ignored = git(folder, "check-ignore", "-q", "--", rel)
    if ignored is not None and ignored.returncode == 0:
        return ""
    excl = git(folder, "rev-parse", "--git-path", "info/exclude")
    if excl is None or excl.returncode != 0:
        raise Refused(f"{show(folder)}: could not make git ignore .env; nothing written.")
    path = pathlib.Path(excl.stdout.strip())
    if not path.is_absolute():
        path = folder / path
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text() if path.exists() else ""
    path.write_text(old + ("" if old.endswith("\n") or not old else "\n") + ".env\n.env.*\n!.env.example\n")
    return " (git told to ignore .env)"


def env_line(name, value, export):
    v = value if SAFE_RE.match(value) else f"'{value}'"
    return f"{'export ' if export else ''}{name}={v}\n"


def parse_value(line):
    v = line.split("=", 1)[1].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def read_env(env):
    if env.is_symlink():
        raise Refused(f"{show(env)} is a link; this only writes a real .env file in the agent's folder.")
    if env.exists():
        if not env.is_file():
            raise Refused(f"{show(env)} is not a file.")
        if env.stat().st_uid != os.getuid():
            raise Refused(f"{show(env)} belongs to someone else (a company key file?); ask the admin.")
        return env.read_text(errors="surrogateescape").splitlines(keepends=True)
    return []


def write_key(folder, name, value):
    env = folder / ".env"
    lines = read_env(env)
    note = keep_out_of_git(folder, env)
    pat = line_re(name)
    out, replaced, dropped = [], False, 0
    for ln in lines:
        m = pat.match(ln)
        if not m:
            out.append(ln)
        elif not replaced:
            out.append(env_line(name, value, bool(m.group(1))))
            replaced = True
        else:
            dropped += 1                    # a later duplicate would win over the new value
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(env_line(name, value, False))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".env.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", errors="surrogateescape") as f:
            f.writelines(out)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, env)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(env, 0o600)
    # read it back: the line must give exactly the key
    got = [parse_value(ln) for ln in read_env(env) if pat.match(ln)]
    if got != [value]:
        raise Refused(f"{show(env)}: the key did not read back correctly; check the file's {name} line (without "
                      f"showing it) or ask the admin.")
    how = "replaced the old value" if replaced else "added"
    if dropped:
        how += f", removed {dropped} older duplicate line{'s' if dropped > 1 else ''}"
    say(f"{name} -> {show(env)}: {how} ({len(value)} characters){note}")


# ---------------------------------------------------------------- main

def check(name, folders):
    pat = line_re(name)
    ok = True
    for arg in folders:
        try:
            folder = check_folder(arg)
            vals = [parse_value(ln) for ln in read_env(folder / ".env") if pat.match(ln)]
        except Refused as e:
            say(f"REFUSED {e}")
            ok = False
            continue
        if not vals:
            say(f"{name} in {show(folder / '.env')}: not set")
            ok = False
        else:
            extra = f" ({len(vals)} lines; the last one counts)" if len(vals) > 1 else ""
            say(f"{name} in {show(folder / '.env')}: set, {len(vals[-1])} characters{extra}")
    return 0 if ok else 1


def main(argv):
    args, opts = [], {"from": None, "keep": False, "check": False, "wait": WAIT_SECONDS}
    it = iter(argv)
    for a in it:
        if a == "--from":
            opts["from"] = next(it, None)
        elif a == "--keep":
            opts["keep"] = True
        elif a == "--check":
            opts["check"] = True
        elif a == "--wait":
            w = next(it, "")
            if not w.isdigit():
                print("--wait takes a number of seconds", file=sys.stderr)
                return 2
            opts["wait"] = int(w)
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif a.startswith("-"):
            print(f"Unknown option {a}\n{__doc__}", file=sys.stderr)
            return 2
        else:
            args.append(a)
    if len(args) < 2 or (opts["from"] is None and "--from" in argv):
        print(__doc__, file=sys.stderr)
        return 2
    name, folders = args[0], args[1:]
    if not NAME_RE.match(name):
        print(f"{name!r} is not a usable name: letters, digits and _, starting with a letter (e.g. OPENAI_API_KEY).",
              file=sys.stderr)
        return 2
    os.umask(0o077)
    if opts["check"]:
        return check(name, folders)
    try:
        checked = [check_folder(f) for f in folders]
        src = find_source(name, opts["from"], opts["wait"])
        value = read_key(src, name)
    except Refused as e:
        say(f"REFUSED {e}")
        return 1
    failed = 0
    for folder in checked:
        try:
            write_key(folder, name, value)
        except Refused as e:
            say(f"REFUSED {e}")
            failed += 1
        except OSError as e:                # never the key in the message: only what failed and why
            say(f"REFUSED {show(folder / '.env')}: could not write it ({e.strerror or type(e).__name__}).")
            failed += 1
    if failed:
        say(f"The key file is still at {show(src)}. Fix the above and run this again; it is deleted after a run "
            f"that works (or delete it yourself).")
        return 1
    if opts["keep"]:
        say(f"Kept the key file at {show(src)} (--keep). Delete it when you are done.")
    else:
        src.unlink()
        where = " (and from the laptop, at its next sync)" if BRIDGE in src.parents else ""
        say(f"Deleted the key file {show(src)}{where}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:                  # no traceback text: only the kind of error, never a value
        print(f"REFUSED unexpected error ({type(e).__name__}); nothing more is shown on purpose.", file=sys.stderr)
        sys.exit(1)
