#!/usr/bin/env python3
"""Show a conflict or a diff the way an IDE's merge window does (Rider, IntelliJ): panes side by side, the
changed lines coloured, changed words marked, long unchanged stretches folded.

  diffview.py merge <project> [theirs] [--ours REF] [--theirs-first] [--all]
      Three panes: this computer (ours) | the result | theirs. Default ours = HEAD; theirs = the merge in progress
      (MERGE_HEAD), else GitHub's copy of the branch (origin/<branch>), which is what `sync.sh down` stopped on.
      Shows the files that conflict and the files changed on both sides; --all also the ones changed on one side.
      Where both sides only added lines at the same place, the middle proposes keeping both (ours first;
      --theirs-first puts theirs first). Everything else that conflicts is left undecided in the middle.
  diffview.py diff <project> [from] [to]
      Two panes, before | after. No refs: uncommitted changes (HEAD against the folder). One ref: that commit
      against the folder. Two refs: between them.

<project> is a name from ~/.git_master/config.json (./gm.py list) or a path. Read-only on the repo. Writes
~/.git_master/visualize/<project>-conflict.html or -diff.html (local) and prints its path and a summary. The page
shows file contents, so key files are never shown, and a page in which gitleaks finds a secret is not written.
"""
import argparse
import datetime
import difflib
import html
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
CTX = 3            # unchanged lines kept around each change; longer unchanged stretches fold
KEY_FILE = re.compile(r"(^|/)(\.env(\..*)?|[^/]*\.env|[^/]*\.pem|[^/]*\.key|id_[a-z0-9]+)$|(^|/)credentials/")


def git(repo, *args, ok=(0,)):
    out = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], capture_output=True)
    if out.returncode not in ok:
        raise SystemExit(f"git {' '.join(args[:2])} failed in {repo}: {out.stderr.decode(errors='replace').strip()}")
    return out


def text(repo, *args, ok=(0,)):
    return git(repo, *args, ok=ok).stdout.decode(errors="replace")


def find_repo(target):
    p = pathlib.Path(os.path.expanduser(target))
    if p.is_dir():
        return p.resolve().name, p.resolve()
    out = subprocess.run([sys.executable, str(ROOT / "gm.py"), "tsv", target], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        if target == "git_master":
            return "git_master", ROOT
        raise SystemExit(out.stderr.strip() or f"no repo called {target!r}: ./gm.py list shows the names")
    name, path = out.stdout.strip().split("\t", 1)
    return name, pathlib.Path(path)


def ref_info(repo, ref):
    """short hash, date, subject of a commit."""
    h, d, s = text(repo, "log", "-1", "--format=%h%x1f%ci%x1f%s", ref).strip().split("\x1f")
    return {"hash": h, "date": d[:16], "subject": s}


def blob(repo, ref, path):
    """A file's bytes at a ref (None: not there); ref None = the folder as it is now."""
    if ref is None:
        f = repo / path
        return f.read_bytes() if f.is_file() else None
    out = git(repo, "show", f"{ref}:{path}", ok=(0, 128))
    return out.stdout if out.returncode == 0 else None


def lines_of(data):
    """(lines, note): note says why a file is shown without its lines."""
    if data is None:
        return [], "not there"
    if b"\0" in data[:8000]:
        return None, "binary"
    s = data.decode("utf-8", errors="replace")
    ls = s.split("\n")
    if ls and ls[-1] == "":
        ls.pop()
    return ls, None


# ---------- rendering helpers ----------
def esc(s):
    return html.escape(s) if s else ""


def marked(old, new):
    """`new` as HTML with the words that differ from `old` wrapped in <mark>; plain if the lines are too different."""
    if old is None or new is None:
        return esc(new)
    tok = lambda s: re.findall(r"\w+|\s+|[^\w\s]", s)
    a, b = tok(old), tok(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.ratio() < 0.35:
        return esc(new)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        seg = esc("".join(b[j1:j2]))
        out.append(seg if tag == "equal" or not seg.strip() else f"<mark>{seg}</mark>")
    return "".join(out)


def cell(num, html_text, cls):
    if num is None:
        return '<td class="n"></td><td class="t hatch"></td>'
    return f'<td class="n">{num}</td><td class="t {cls}">{html_text if html_text else "&nbsp;"}</td>'


def fold(rows, ncols):
    """rows: (changed, html_tr). Unchanged runs longer than 2*CTX+1 fold into a row that opens them."""
    out, i, n = [], 0, len(rows)
    while i < n:
        if rows[i][0]:
            out.append(rows[i][1]); i += 1; continue
        j = i
        while j < n and not rows[j][0]:
            j += 1
        run = [r[1] for r in rows[i:j]]
        head = CTX if i > 0 else 0
        tail = CTX if j < n else 0
        if len(run) > head + tail + 1:
            out += run[:head]
            hidden = run[head:len(run) - tail]
            out.append(f'<tr class="fold"><td colspan="{ncols}"><button type="button" onclick="unfold(this)">'
                       f'{len(hidden):,} unchanged lines</button></td></tr>'
                       f'</tbody><tbody hidden>{"".join(hidden)}</tbody><tbody>')
            out += run[len(run) - tail:]
        else:
            out += run
        i = j
    return "".join(out)


def label_row(ncols, cls, text_):
    return (True, f'<tr class="hunk {cls}"><td colspan="{ncols}">{text_}</td></tr>')


# ---------- two panes ----------
def two_way(a, b):
    rows, changes = [], 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                t = esc(a[i1 + k])
                rows.append((False, f"<tr>{cell(i1 + k + 1, t, 'same')}{cell(j1 + k + 1, t, 'same')}</tr>"))
            continue
        changes += 1
        n = max(i2 - i1, j2 - j1)
        for k in range(n):
            l = a[i1 + k] if i1 + k < i2 else None
            r = b[j1 + k] if j1 + k < j2 else None
            paired = l is not None and r is not None      # a changed line: both sides, changed words marked
            lc = cell(i1 + k + 1, marked(r, l), "old" if paired else "del") if l is not None else cell(None, "", "")
            rc = cell(j1 + k + 1, marked(l, r), "chg" if paired else "ins") if r is not None else cell(None, "", "")
            rows.append((True, f"<tr>{lc}{rc}</tr>"))
    return fold(rows, 4), changes


# ---------- three panes (diff3) ----------
def side_map(ops):
    """base index -> (first, last) position on the side, for points on change boundaries and in equal stretches."""
    def start(x):
        best = None
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal" and i1 <= x <= i2:
                j = j1 + (x - i1)
            elif x == i1:
                j = j1
            elif x == i2:
                j = j2
            else:
                continue
            best = j if best is None else min(best, j)
        return best

    def end(x):
        best = None
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal" and i1 <= x <= i2:
                j = j1 + (x - i1)
            elif x == i2:      # before i1: lines added at x (i1 == i2) end at j2
                j = j2
            elif x == i1:
                j = j1
            else:
                continue
            best = j if best is None else max(best, j)
        return best
    return start, end


def three_way(base, a, b, theirs_first):
    """Rows for ours | result | theirs, and counts (conflicts, one-sided changes)."""
    oa = difflib.SequenceMatcher(None, base, a, autojunk=False).get_opcodes()
    ob = difflib.SequenceMatcher(None, base, b, autojunk=False).get_opcodes()
    ch = sorted([(i1, i2, "a") for t, i1, i2, _, _ in oa if t != "equal"] +
                [(i1, i2, "b") for t, i1, i2, _, _ in ob if t != "equal"])
    groups = []
    for i1, i2, s in ch:
        if groups and i1 <= groups[-1][1]:   # overlapping or touching changes conflict, as in git
            g = groups[-1]; g[1] = max(g[1], i2); g[2].add(s)
        else:
            groups.append([i1, i2, {s}])
    a_start, a_end = side_map(oa)
    b_start, b_end = side_map(ob)
    rows, conflicts, one_sided = [], 0, 0
    pos = 0            # base position
    la = lb = lm = 0   # lines emitted so far on each pane

    def context(upto):
        nonlocal pos, la, lb, lm
        while pos < upto:
            la += 1; lb += 1; lm += 1
            t = esc(base[pos])
            rows.append((False, f"<tr>{cell(la, t, 'same')}{cell(lm, t, 'same')}{cell(lb, t, 'same')}</tr>"))
            pos += 1

    for gs, ge, sides in groups:
        context(gs)
        A = a[a_start(gs):a_end(ge)]
        B = b[b_start(gs):b_end(ge)]
        O = base[gs:ge]
        if A == B:
            kind, M = "both", A
        elif sides == {"a"} or B == O:
            kind, M = "ours", A
        elif sides == {"b"} or A == O:
            kind, M = "theirs", B
        else:
            kind, M = "conflict", None
        if kind == "conflict":
            conflicts += 1
            if not O:
                M = (B + A) if theirs_first else (A + B)
                first = "theirs" if theirs_first else "this computer's"
                rows.append(label_row(6, "conflict", f"Conflict {conflicts}: both sides added lines here. "
                                                     f"Proposed in the middle: keep both, {first} first."))
                mcls = "prop"
            else:
                M = O
                rows.append(label_row(6, "conflict", f"Conflict {conflicts}: both sides changed these lines. "
                                                     "Not decided: the middle shows the version before either change."))
                mcls = "undecided"
        else:
            one_sided += kind != "both"
            text_ = {"ours": "Changed only on the left: the result takes it",
                     "theirs": "Changed only on the right: the result takes it",
                     "both": "The same change on both sides"}[kind]
            rows.append(label_row(6, "info", text_))
            mcls = "take"
        n = max(len(A), len(B), len(M))
        for k in range(n):
            o = O[k] if k < len(O) else None
            if k < len(A):
                la += 1
                ac = cell(la, marked(o, A[k]), ("conf" if kind == "conflict" else "chg") if A != O else "same")
            else:
                ac = cell(None, "", "")
            if k < len(B):
                lb += 1
                bc = cell(lb, marked(o, B[k]), ("conf" if kind == "conflict" else "chg") if B != O else "same")
            else:
                bc = cell(None, "", "")
            if k < len(M):
                lm += 1
                mc = cell(lm, esc(M[k]), mcls)
            else:
                mc = cell(None, "", "")
            rows.append((True, f"<tr>{ac}{mc}{bc}</tr>"))
        pos = ge
    context(len(base))
    return fold(rows, 6), conflicts, one_sided


# ---------- page ----------
PAGE = r"""<title>__TITLE__</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter+Tight:wght@400;600;700&display=swap">
<style>
/* Layout: an IDE merge window. A header with the sides and a file list, then per file a table of panes
   side by side, line numbers in the gutter, folded unchanged stretches. Light first, dark mirrored. */
:root {
  --bg: #f7f8fa; --panel: #ffffff; --fg: #1e1f22; --muted: #6c707e; --line: #dfe1e5; --gutter: #f2f3f5;
  --ins: #e3f7e6; --ins-b: #4fa25c; --del: #fde7e7; --del-b: #d65a5a; --chg: #e5eefc; --chg-b: #4d7fe0;
  --conf: #fdeedd; --conf-b: #e0802f; --mark: rgba(77,127,224,.28); --hatch: #eceef1;
  --ui: "Inter Tight", system-ui, -apple-system, "Segoe UI", sans-serif; --mono: "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #1e1f22; --panel: #2b2d30; --fg: #dfe1e5; --muted: #8c8f99; --line: #393b40; --gutter: #26282b;
  --ins: #233a29; --ins-b: #5fb36e; --del: #45282a; --del-b: #e06c6c; --chg: #233149; --chg-b: #6c9be8;
  --conf: #47321f; --conf-b: #e8954a; --mark: rgba(108,155,232,.35); --hatch: #2a2c30; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #1e1f22; --panel: #2b2d30; --fg: #dfe1e5; --muted: #8c8f99; --line: #393b40; --gutter: #26282b;
  --ins: #233a29; --ins-b: #5fb36e; --del: #45282a; --del-b: #e06c6c; --chg: #233149; --chg-b: #6c9be8;
  --conf: #47321f; --conf-b: #e8954a; --mark: rgba(108,155,232,.35); --hatch: #2a2c30; color-scheme: dark }
body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.45 var(--ui);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 18px 40px; }
.wrap { max-width: 1800px; margin-inline: auto; display: grid; gap: 18px; }
header { display: grid; gap: 8px; }
h1 { margin: 0; font-size: 22px; font-weight: 700; text-wrap: balance; }
.sub { color: var(--muted); margin: 0; max-width: 75ch; }
.sides { display: flex; flex-wrap: wrap; gap: 8px 22px; font-size: 13px; }
.sides div { min-width: 0; } .sides b { display: block; font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.sides code, .files code { font: 12.5px var(--mono); }
.files { display: flex; flex-wrap: wrap; gap: 6px; padding: 0; margin: 0; list-style: none; }
.files a { display: inline-flex; gap: 6px; align-items: center; padding: 3px 9px; border: 1px solid var(--line); border-radius: 4px;
  background: var(--panel); color: var(--fg); text-decoration: none; font-size: 13px; }
.files a:focus-visible, button:focus-visible { outline: 2px solid var(--chg-b); outline-offset: 2px; }
.chip { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 3px; }
.chip.conflict { background: var(--conf); color: var(--fg); box-shadow: inset 3px 0 var(--conf-b); }
.chip.clean { background: var(--chg); box-shadow: inset 3px 0 var(--chg-b); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; color: var(--muted); font-size: 12.5px; }
.legend span::before { content: ""; display: inline-block; width: 18px; height: 12px; margin-right: 6px; vertical-align: -2px; border-radius: 2px; border: 1px solid var(--line); }
.legend .l-chg::before { background: var(--chg); box-shadow: inset 3px 0 var(--chg-b); }
.legend .l-ins::before { background: var(--ins); box-shadow: inset 3px 0 var(--ins-b); }
.legend .l-del::before { background: var(--del); box-shadow: inset 3px 0 var(--del-b); }
.legend .l-conf::before { background: var(--conf); box-shadow: inset 3px 0 var(--conf-b); }
.legend .l-hatch::before { background: repeating-linear-gradient(45deg, var(--hatch) 0 3px, var(--panel) 3px 6px); border: 1px solid var(--line); }
section { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; min-width: 0; }
section h2 { margin: 0; padding: 10px 12px; font: 600 14px var(--mono); border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
section .note { padding: 12px; color: var(--muted); margin: 0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; font: 12.5px/1.5 var(--mono); }
table.p3 { min-width: 1080px; } table.p2 { min-width: 760px; }
col.n { width: 48px; }
thead th { text-align: left; padding: 7px 10px; background: var(--gutter); border-bottom: 1px solid var(--line); font: 600 12.5px var(--ui); }
thead th small { display: block; font-weight: 400; color: var(--muted); font-family: var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
td { vertical-align: top; }
td.n { text-align: right; padding: 0 8px; color: var(--muted); background: var(--gutter); user-select: none; font-variant-numeric: tabular-nums; }
td.t { white-space: pre-wrap; word-break: break-word; padding: 0 10px; border-right: 1px solid var(--line); }
td.same { color: var(--muted); }
td.ins, td.take { background: var(--ins); box-shadow: inset 3px 0 var(--ins-b); }
td.del, td.old { background: var(--del); box-shadow: inset 3px 0 var(--del-b); }
td.chg { background: var(--chg); box-shadow: inset 3px 0 var(--chg-b); }
td.conf, td.prop { background: var(--conf); box-shadow: inset 3px 0 var(--conf-b); }
td.undecided { background: repeating-linear-gradient(45deg, var(--conf) 0 6px, var(--panel) 6px 12px); color: var(--muted); }
td.hatch { background: repeating-linear-gradient(45deg, var(--hatch) 0 6px, var(--panel) 6px 12px); }
mark { background: var(--mark); color: inherit; border-radius: 2px; }
tr.hunk td { padding: 5px 12px; font: 600 12.5px var(--ui); border-top: 1px solid var(--line); }
tr.hunk.conflict td { color: var(--conf-b); } tr.hunk.info td { color: var(--muted); font-weight: 400; }
tr.fold td { padding: 0; border-block: 1px dashed var(--line); background: var(--gutter); }
tr.fold button { all: unset; cursor: pointer; display: block; width: 100%; padding: 3px 12px; color: var(--muted); font: 12px var(--ui); box-sizing: border-box; }
tr.fold button:hover { color: var(--fg); }
footer { color: var(--muted); font-size: 12.5px; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
<div class="wrap">
__BODY__
</div>
<script>
function unfold(b){ const tr=b.closest('tr'); const tb=tr.parentElement.nextElementSibling; if(tb){ tb.hidden=false; } tr.remove(); }
</script>
"""


def page(title, body):
    return PAGE.replace("__TITLE__", esc(title)).replace("__BODY__", body)


def side_html(label, info):
    ref = f'<code>{esc(info["ref"])}</code> &middot; ' if info.get("ref") else ""
    return (f'<div><b>{esc(label)}</b>{ref}<code>{esc(info["hash"])}</code> '
            f'{esc(info["date"])} &middot; {esc(info["subject"])}</div>')


def guard_and_write(out, doc):
    """Write the page unless gitleaks finds a secret in it."""
    leaks = ROOT / "tools" / "gitleaks"
    leaks = str(leaks) if leaks.is_file() else shutil.which("gitleaks")
    if leaks:
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "view.html").write_text(doc, encoding="utf-8")
            r = subprocess.run([leaks, "dir", d, "--no-banner", "--redact", "-l", "error"], capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit("gitleaks found something that looks like a secret in these lines: the page was not "
                                 "written. Check the changed files with ./sync.sh up before showing them.")
    else:
        print("note: gitleaks not found, the page was not scanned for secrets", file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")


def outfile(name, kind):
    return pathlib.Path(os.path.expanduser(os.environ.get("GIT_MASTER_HOME", "~/.git_master"))) / "visualize" / f"{name}-{kind}.html"


def changed(repo, a, b):
    return [p for p in text(repo, "diff", "--name-only", "--no-renames", a, b).split("\n") if p]


def cmd_merge(args):
    name, repo = find_repo(args.project)
    ours = args.ours or "HEAD"
    branch = text(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    in_progress = git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", ok=(0, 1)).returncode == 0
    if args.theirs:
        theirs, tlabel = args.theirs, f"Right: {args.theirs}"
    elif in_progress:
        theirs = "MERGE_HEAD"
        tlabel = f"Right: {text(repo, 'name-rev', '--name-only', 'MERGE_HEAD').strip() or 'the branch being merged'}"
    else:
        theirs, tlabel = f"origin/{branch}", f"Right: GitHub (origin/{branch})"
        git(repo, "fetch", "-q", "origin", branch, ok=(0, 1, 128))
    if git(repo, "rev-parse", "-q", "--verify", f"{theirs}^{{commit}}", ok=(0, 1, 128)).returncode != 0:
        raise SystemExit(f"{theirs!r} is not a branch or commit in {name}")
    olabel = "Left: this computer" + (f" ({branch})" if ours == "HEAD" else f" ({ours})")
    base = text(repo, "merge-base", ours, theirs, ok=(0, 1)).strip()
    if not base:
        raise SystemExit("the two sides share no history, so there is nothing to merge")
    mt = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs, ok=(0, 1))
    conflicted = [p for p in mt.stdout.decode(errors="replace").split("\n")[1:] if p]
    ca, cb = changed(repo, base, ours), changed(repo, base, theirs)
    both = [p for p in ca if p in cb]
    only_a = [p for p in ca if p not in cb]
    only_b = [p for p in cb if p not in ca]
    show = conflicted + [p for p in both if p not in conflicted]
    if args.all:
        show += only_a + only_b
    sections, file_links, total_conf = [], [], 0
    for i, path in enumerate(show):
        anchor = f"f{i}"
        is_conf = path in conflicted
        chip = '<span class="chip conflict">conflict</span>' if is_conf else '<span class="chip clean">merges cleanly</span>'
        file_links.append(f'<li><a href="#{anchor}"><code>{esc(path)}</code>{chip}</a></li>')
        head = f'<h2 id="{anchor}">{esc(path)} {chip}</h2>'
        if KEY_FILE.search(path):
            sections.append(f'<section>{head}<p class="note">A key file: its lines are never shown.</p></section>'); continue
        O, no = lines_of(blob(repo, base, path))
        A, na = lines_of(blob(repo, ours, path))
        B, nb = lines_of(blob(repo, theirs, path))
        if None in (O, A, B):
            sections.append(f'<section>{head}<p class="note">A binary file (Word, Excel, PDF, an image): it conflicts '
                            'as a whole file, so one version or the other is kept. Open both to compare.</p></section>'); continue
        notes = []
        if na == "not there":
            notes.append("Deleted on the left.")
        if nb == "not there":
            notes.append("Deleted on the right.")
        if no == "not there":
            notes.append("New on both sides.")
        body, nconf, _ = three_way(O, A, B, args.theirs_first)
        total_conf += nconf
        sections.append(f'<section>{head}{"<p class=note>" + " ".join(notes) + "</p>" if notes else ""}<div class="scroll">'
                        f'<table class="p3"><colgroup><col class="n"><col><col class="n"><col><col class="n"><col></colgroup>'
                        f'<thead><tr><th colspan="2">{esc(olabel)}</th><th colspan="2">Result</th><th colspan="2">{esc(tlabel)}</th></tr></thead>'
                        f'<tbody>{body}</tbody></table></div></section>')
    info = lambda ref: {"ref": ref, **ref_info(repo, ref)}
    others = ""
    if not args.all and (only_a or only_b):
        others = (f'<p class="sub">Changed on one side only, merging without a question: {len(only_a)} file(s) on the left, '
                  f'{len(only_b)} on the right (not shown).</p>')
    state = (f"{len(conflicted)} file(s) conflict" if conflicted else "Nothing conflicts: the two sides merge cleanly")
    body = (f'<header><h1>{esc(name)}: {esc(state)}</h1>'
            f'<p class="sub">The left and right panes are the two versions; the middle is what the file would look like '
            f'after merging. Lines both sides kept as they were are grey; long unchanged stretches are folded.</p>'
            f'<div class="sides">{side_html(olabel, info(ours))}{side_html("Where they split", {**info(base), "ref": ""})}'
            f'{side_html(tlabel, info(theirs))}</div>'
            f'<ul class="files">{"".join(file_links)}</ul>{others}'
            f'<div class="legend"><span class="l-conf">conflict</span><span class="l-chg">changed on that side</span>'
            f'<span class="l-ins">taken into the result</span><span class="l-hatch">nothing on that side</span></div></header>'
            + "".join(sections) +
            f'<footer>Made {datetime.datetime.now().astimezone().isoformat(timespec="minutes")} by git_master diffview.py. '
            f'Read-only: nothing was merged.</footer>')
    out = outfile(name, "conflict")
    guard_and_write(out, page(f"{name} conflict", body))
    print(f"{out}\n{name}: {len(conflicted)} file(s) conflict ({total_conf} conflicting place(s)), "
          f"{len(both) - len([p for p in both if p in conflicted])} changed on both sides but merge cleanly, "
          f"{len(only_a)} only on the left, {len(only_b)} only on the right")


def cmd_diff(args):
    name, repo = find_repo(args.project)
    a_ref = args.frm or "HEAD"
    b_ref = args.to  # None = the folder as it is now
    if b_ref:
        paths = changed(repo, a_ref, b_ref)
    else:
        paths = [p for p in text(repo, "diff", "--name-only", "--no-renames", a_ref).split("\n") if p]
        paths += [p for p in text(repo, "ls-files", "--others", "--exclude-standard").split("\n") if p]
    sections, file_links, nchanges = [], [], 0
    llabel = f"Before: {a_ref}"
    rlabel = f"After: {b_ref}" if b_ref else "After: the folder now (not committed)"
    for i, path in enumerate(paths):
        anchor = f"f{i}"
        file_links.append(f'<li><a href="#{anchor}"><code>{esc(path)}</code></a></li>')
        head = f'<h2 id="{anchor}">{esc(path)}</h2>'
        if KEY_FILE.search(path):
            sections.append(f'<section>{head}<p class="note">A key file: its lines are never shown.</p></section>'); continue
        A, na = lines_of(blob(repo, a_ref, path))
        B, nb = lines_of(blob(repo, b_ref, path))
        if A is None or B is None:
            sections.append(f'<section>{head}<p class="note">A binary file (Word, Excel, PDF, an image): '
                            'the versions differ; open them to compare.</p></section>'); continue
        note = "New file." if na == "not there" else "Deleted." if nb == "not there" else ""
        body, n = two_way(A, B)
        nchanges += n
        sections.append(f'<section>{head}{"<p class=note>" + note + "</p>" if note else ""}<div class="scroll">'
                        f'<table class="p2"><colgroup><col class="n"><col><col class="n"><col></colgroup>'
                        f'<thead><tr><th colspan="2">{esc(llabel)}</th><th colspan="2">{esc(rlabel)}</th></tr></thead>'
                        f'<tbody>{body}</tbody></table></div></section>')
    sides = side_html(llabel, {"ref": a_ref, **ref_info(repo, a_ref)})
    if b_ref:
        sides += side_html(rlabel, {"ref": b_ref, **ref_info(repo, b_ref)})
    body = (f'<header><h1>{esc(name)}: {len(paths)} file(s) changed</h1>'
            f'<p class="sub">Before on the left, after on the right; changed words are marked. '
            f'Long unchanged stretches are folded.</p><div class="sides">{sides}</div>'
            f'<ul class="files">{"".join(file_links)}</ul>'
            f'<div class="legend"><span class="l-del">removed or changed (before)</span><span class="l-chg">changed (after)</span>'
            f'<span class="l-ins">added</span><span class="l-hatch">nothing on that side</span></div></header>'
            + ("".join(sections) or '<section><p class="note">No changes.</p></section>') +
            f'<footer>Made {datetime.datetime.now().astimezone().isoformat(timespec="minutes")} by git_master diffview.py. Read-only.</footer>')
    out = outfile(name, "diff")
    guard_and_write(out, page(f"{name} diff", body))
    print(f"{out}\n{name}: {len(paths)} file(s) changed, {nchanges} changed place(s), {a_ref} -> {b_ref or 'the folder now'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="three panes: this computer | result | theirs")
    m.add_argument("project")
    m.add_argument("theirs", nargs="?", help="branch or commit (default: the merge in progress, else origin/<branch>)")
    m.add_argument("--ours", help="left side (default HEAD)")
    m.add_argument("--theirs-first", action="store_true", help="keep both: theirs first in the proposed result")
    m.add_argument("--all", action="store_true", help="also the files changed on one side only")
    d = sub.add_parser("diff", help="two panes: before | after")
    d.add_argument("project")
    d.add_argument("frm", nargs="?", metavar="from")
    d.add_argument("to", nargs="?")
    args = ap.parse_args()
    cmd_merge(args) if args.cmd == "merge" else cmd_diff(args)


if __name__ == "__main__":
    main()
