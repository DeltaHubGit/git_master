#!/usr/bin/env python3
"""Draw a repo's history as a Sourcetree-style page: a lane graph with one dot per commit,
branch pills, ahead/behind badges, and a details pane with the files each commit changed.

Hollow dot = committed but only on this computer; full dot = on GitHub (any remote).
Usage: python3 visualize.py [project name | path] [--limit N]   (default: git_master itself)
Writes ~/.git_master/visualize/<name>.html (local only) and prints its path. Read-only on the repo.
"""
import argparse
import datetime
import html
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
US, RS = "\x1f", "\x1e"
TRAILER = re.compile(r"^([A-Za-z][A-Za-z-]*):\s+(.+)$")


def split_trailers(body):
    """The message text, and the git trailer lines at its end (Co-Authored-By, Claude-Session...)."""
    lines, trailers = body.strip().split("\n"), []
    while lines and TRAILER.match(lines[-1].strip()):
        trailers.insert(0, list(TRAILER.match(lines.pop().strip()).groups()))
    return "\n".join(lines).strip(), trailers


def git(repo, *args):
    out = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                         capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:2])} failed in {repo}: {out.stderr.strip()}")
    return out.stdout


def find_repo(target):
    """A repo name from ~/.git_master/config.json, or a path; no target = git_master's own code."""
    if not target:
        return "git_master", ROOT
    p = pathlib.Path(os.path.expanduser(target))
    if p.is_dir():
        return p.resolve().name, p.resolve()
    out = subprocess.run([sys.executable, str(ROOT / "gm.py"), "tsv", target], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(out.stderr.strip() or f"no repo called {target!r}: ./gm.py list shows the names")
    name, path = out.stdout.strip().split("\t", 1)
    return name, pathlib.Path(path)


def collect(repo, limit):
    fmt = US.join(["%H", "%P", "%an", "%aI", "%s", "%b"]) + RS
    raw = git(repo, "log", "--all", "--date-order", f"--max-count={limit}", f"--format={fmt}")
    commits = []
    for rec in raw.split(RS):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, parents, author, date, subject, body = (rec.split(US) + [""] * 6)[:6]
        text, trailers = split_trailers(body)
        commits.append({"hash": h, "parents": parents.split(), "author": author, "date": date,
                        "subject": subject, "body": text, "trailers": trailers})
    total = int(git(repo, "rev-list", "--all", "--count").strip() or 0)

    # Files per commit (merges show none, as in git log)
    files, current = {}, None
    for line in git(repo, "log", "--all", "--date-order", f"--max-count={limit}", "--numstat",
                    f"--format={RS}%H").split("\n"):  # not splitlines(): it breaks on RS too
        if line.startswith(RS):
            current = line[1:]
            files[current] = []
        elif line.strip() and current:
            add, delete, path = (line.split("\t", 2) + ["", ""])[:3]
            files[current].append([path, None if add == "-" else int(add), None if delete == "-" else int(delete)])
    for c in commits:
        f = files.get(c["hash"], [])
        c["files"], c["more"] = f[:200], max(0, len(f) - 200)

    # Branches, remotes, tags; HEAD; ahead/behind per local branch
    head = git(repo, "rev-parse", "-q", "--verify", "HEAD").strip() if commits else ""
    branch = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "--short", "-q", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    labels = {}
    for line in git(repo, "for-each-ref", "--format=%(objectname)%09%(refname)%09%(upstream:track,nobracket)",
                    "refs/heads", "refs/remotes", "refs/tags").splitlines():
        obj, ref, track = (line.split("\t") + ["", ""])[:3]
        if ref.endswith("/HEAD"):
            continue
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            item = {"name": name, "kind": "head" if name == branch else "local"}
            for word, key in (("ahead", "ahead"), ("behind", "behind")):
                m = re.search(rf"{word} (\d+)", track)
                if m:
                    item[key] = int(m.group(1))
        elif ref.startswith("refs/remotes/"):
            item = {"name": ref[len("refs/remotes/"):], "kind": "remote"}
        else:
            item = {"name": ref[len("refs/tags/"):], "kind": "tag"}
        labels.setdefault(obj, []).append(item)
    order = {"head": 0, "local": 1, "remote": 2, "tag": 3}
    for c in commits:
        c["labels"] = sorted(labels.get(c["hash"], []), key=lambda l: order[l["kind"]])

    remotes = git(repo, "remote").split()
    online = set(git(repo, "rev-list", "--remotes").split()) if remotes else set()
    for c in commits:
        c["pushed"] = c["hash"] in online
    return commits, total, head, branch, remotes, working_changes(repo, bool(head))


def working_changes(repo, has_head):
    """Uncommitted files like Sourcetree's working copy: status, staged or not, +/- lines."""
    lines = {}
    if has_head:
        for line in git(repo, "diff", "--numstat", "HEAD").split("\n"):
            parts = line.split("\t")
            if len(parts) == 3:
                lines[parts[2]] = [None if parts[0] == "-" else int(parts[0]), None if parts[1] == "-" else int(parts[1])]
    entries = git(repo, "status", "--porcelain=v1", "-z", "-uall").split("\0")
    out, i = [], 0
    while i < len(entries):
        e = entries[i]; i += 1
        if len(e) < 4:
            continue
        x, y, path = e[0], e[1], e[3:]
        old = None
        if x in "RC":  # the original path follows as its own entry
            old, i = entries[i], i + 1
        if x == "?":
            kind, add, dele = "new", None, 0
            f = repo / path
            try:
                if f.is_file() and f.stat().st_size < 2_000_000:
                    add = f.read_text(encoding="utf-8").count("\n")
            except (UnicodeDecodeError, OSError):
                add = None
        else:
            code = x if x not in " ?" else y
            kind = {"M": "changed", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "U": "conflict"}.get(code, "changed")
            add, dele = lines.get(path, [None, None])
        out.append({"path": path, "old": old, "kind": kind, "staged": x not in " ?", "add": add, "del": dele})
    return out


def layout(commits):
    """Sourcetree-style lanes: each commit takes the lane that was waiting for it; its first
    parent continues that lane, further parents get (or join) another lane."""
    index = {c["hash"]: i for i, c in enumerate(commits)}
    lanes, pending, edges = [], {}, []

    def free_lane(avoid=None):
        for k, h in enumerate(lanes):
            if h is None and k != avoid:
                return k
        lanes.append(None)
        return len(lanes) - 1

    for i, c in enumerate(commits):
        waiting = [k for k, h in enumerate(lanes) if h == c["hash"]]
        lane = waiting[0] if waiting else free_lane()
        for k in waiting:
            lanes[k] = None
        for fr, fl, el in pending.pop(c["hash"], []):
            edges.append([fr, fl, i, lane, el])
        c["lane"] = lane
        for n, p in enumerate(c["parents"]):
            if n == 0:
                el = lane
            elif p in lanes:
                el = lanes.index(p)
            else:
                el = free_lane(avoid=lane)
            lanes[el] = p
            pending.setdefault(p, []).append((i, lane, el))
        while lanes and lanes[-1] is None:
            lanes.pop()
    for p, waits in pending.items():  # parents older than the shown history: run off the bottom
        for fr, fl, el in waits:
            edges.append([fr, fl, None if p not in index else index[p], el, el])
    width = max([c["lane"] for c in commits] + [e[4] for e in edges] + [0]) + 1
    return edges, width


PAGE = r"""<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Jersey+15&family=Atkinson+Hyperlegible:wght@400;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* Layout: Sourcetree's history view. A commit table with the lane graph in its first column,
   a details pane beside it (below as a sheet on phones). Dark first, with a light theme. */
:root {
  --bg: #241f2d; --panel: #2e2839; --row: #29233a; --line: #3d3550; --sel: #463b62;
  --fg: #efe9f6; --muted: #a59cb8; --accent: #e8823a; --good: #5fb882;
  --pill: #f3eee4; --pill-fg: #2a2335;
  --display: "Jersey 15", "Courier New", monospace; --body: "Atkinson Hyperlegible", Verdana, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
  color-scheme: dark;
}
@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) {
  --bg: #fbfaf8; --panel: #ffffff; --row: #f5f3f8; --line: #e3deea; --sel: #e9e2fb;
  --fg: #241f2c; --muted: #6a6377; --accent: #c8641e; --good: #2f8a55; --pill: #f1edf7; --pill-fg: #241f2c; color-scheme: light } }
:root[data-theme="light"] {
  --bg: #fbfaf8; --panel: #ffffff; --row: #f5f3f8; --line: #e3deea; --sel: #e9e2fb;
  --fg: #241f2c; --muted: #6a6377; --accent: #c8641e; --good: #2f8a55; --pill: #f1edf7; --pill-fg: #241f2c; color-scheme: light }
html, body { background: var(--bg); color: var(--fg); }
body { font: 15px/1.4 var(--body); padding-inline: 16px; padding-block: 20px 40px; }
.wrap { max-width: 1500px; margin-inline: auto; display: grid; gap: 16px; }
header { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 12px 24px; }
h1 { font: 400 44px/1 var(--display); margin: 0; letter-spacing: .02em; }
.status { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; color: var(--muted); }
.status b { color: var(--fg); font-weight: 700; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; color: var(--muted); font-size: 13px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.main { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; align-items: start; }
.table { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; min-width: 0; }
.scroll { overflow-x: auto; }
.grid { min-width: 720px; }
.thead, .r { display: grid; grid-template-columns: var(--gw) minmax(0, 1fr) 150px 150px 84px; align-items: center; }
.thead { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); border-bottom: 1px solid var(--line); height: 34px; }
.thead div { padding-inline: 10px; }
.rows { position: relative; }
.r { height: var(--rh); cursor: pointer; }
.r:nth-child(even) { background: var(--row); }
.r.sel { background: var(--sel); }
.r:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.r > div { padding-inline: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.desc { display: flex; align-items: center; gap: 6px; }
.desc .s { overflow: hidden; text-overflow: ellipsis; }
.when, .who { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.hash { font: 12px var(--mono); color: var(--muted); }
.unc .s { font-style: italic; color: var(--muted); }
svg.graph { position: absolute; left: 0; top: 0; pointer-events: none; overflow: visible; }
.pill { flex: none; font: 400 15px/1 var(--display); letter-spacing: .03em; padding: 3px 7px 2px; border-radius: 5px;
  border: 1.5px solid var(--c, var(--muted)); background: var(--pill); color: var(--pill-fg); display: inline-flex; align-items: center; gap: 5px; }
.pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--c, var(--muted)); }
.pill.head { font-weight: 400; box-shadow: inset 0 0 0 1px var(--c); }
.pill.remote { border-style: dashed; }
.pill.tag::before { border-radius: 1px; transform: rotate(45deg); }
.badge { flex: none; font: 400 15px/1 var(--display); padding: 3px 7px 2px; border-radius: 5px; background: var(--accent); color: #2a2335; }
.details { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; display: grid; gap: 12px;
  position: sticky; top: calc(env(safe-area-inset-top, 0px) + 12px); max-height: calc(100vh - 40px); overflow: auto; min-width: 0; }
.details h2 { font: 700 18px/1.3 var(--body); margin: 0; text-wrap: balance; overflow-wrap: anywhere; }
.msg { display: grid; gap: 6px; padding: 12px 14px; border-radius: 8px; background: var(--row); border: 1px solid var(--line); }
.lbl { font-size: 11px; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); }
.msg .body { color: var(--fg); }
.meta a { color: var(--accent); }
.meta { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 12px; font-size: 13px; }
.meta dt { color: var(--muted); } .meta dd { margin: 0; overflow-wrap: anywhere; }
.meta .mono { font: 12px var(--mono); }
.where { display: inline-flex; align-items: center; gap: 6px; font-weight: 700; }
.body { white-space: pre-wrap; font-size: 14px; color: var(--muted); overflow-wrap: anywhere; }
.files { display: grid; gap: 2px; font-size: 13px; }
.files h3 { font: 400 20px/1 var(--display); margin: 4px 0 6px; letter-spacing: .03em; }
.f { display: grid; grid-template-columns: minmax(0, 1fr) auto 60px; gap: 8px; align-items: center; padding: 3px 0; border-bottom: 1px solid var(--line); }
.f .p { font: 12px var(--mono); overflow-wrap: anywhere; }
.f .n { font: 12px var(--mono); color: var(--muted); white-space: nowrap; }
.f .n .a { color: var(--good); } .f .n .d { color: var(--accent); }
.bar { height: 6px; border-radius: 3px; background: var(--line); display: flex; overflow: hidden; }
.bar i { display: block; height: 100%; } .bar .a { background: var(--good); } .bar .d { background: var(--accent); }
.copy { justify-self: start; font: 400 18px/1 var(--display); letter-spacing: .03em; padding: 8px 14px 7px; border-radius: 7px;
  border: 1.5px solid var(--accent); background: transparent; color: var(--fg); cursor: pointer; }
.copy:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.copied { color: var(--good); font-size: 13px; }
.details[hidden] { display: none; }
.k { font: 400 14px/1 var(--display); letter-spacing: .03em; padding: 3px 6px 2px; border-radius: 4px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
.k.new, .k.added { color: var(--good); border-color: var(--good); } .k.deleted, .k.conflict { color: var(--accent); border-color: var(--accent); }
.k.staged { background: var(--good); border-color: var(--good); color: #1f2a22; }
.fu { display: grid; grid-template-columns: auto minmax(0, 1fr) auto 60px; gap: 8px; align-items: center; padding: 4px 0; border-bottom: 1px solid var(--line); }
.fu .p { font: 12px var(--mono); overflow-wrap: anywhere; } .fu .p small { display: block; color: var(--muted); }
.main.closed { grid-template-columns: minmax(0, 1fr); }
.details h2 { padding-right: 40px; }
.close { position: absolute; top: 10px; right: 10px; width: 34px; height: 34px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--panel); color: var(--fg); font: 22px/1 var(--body); cursor: pointer; display: grid; place-items: center; }
.close:hover { background: var(--row); }
.close:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.foot { color: var(--muted); font-size: 13px; }
@media (max-width: 900px) {
  .main { grid-template-columns: minmax(0, 1fr); }
  .grid { min-width: 0; }
  .thead, .r { grid-template-columns: var(--gw) minmax(0, 1fr) 0 0 0; }
  .thead div:nth-child(n+3), .r > div:nth-child(n+3) { display: none; }
  .details { position: fixed; left: 0; right: 0; bottom: 0; top: auto; max-height: 46vh; border-radius: 14px 14px 0 0;
    padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px)); box-shadow: 0 -8px 30px rgba(0,0,0,.35); z-index: 2; }
  body.open { padding-bottom: 50vh; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
<div class="wrap">
  <header>
    <div style="display:grid;gap:8px;min-width:0">
      <h1 id="name"></h1>
      <div class="status" id="status"></div>
    </div>
    <div class="legend">
      <span><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="var(--muted)"/></svg>on GitHub</span>
      <span><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="var(--bg)" stroke="var(--muted)" stroke-width="2"/></svg>only on this computer</span>
      <span><svg width="14" height="14" aria-hidden="true"><path d="M7 2 A5 5 0 0 0 7 12 Z" fill="#8f84d6"/><path d="M7 2 A5 5 0 0 1 7 12 Z" fill="#e8823a"/></svg>merge</span>
    </div>
  </header>
  <div class="main">
    <div class="table"><div class="scroll"><div class="grid" id="grid">
      <div class="thead"><div>Graph</div><div>Description</div><div>Date</div><div>Author</div><div>Commit</div></div>
      <div class="rows" id="rows" role="listbox" aria-label="Commits, newest first"></div>
    </div></div></div>
    <aside class="details" id="details" aria-live="polite"></aside>
  </div>
  <p class="foot" id="foot"></p>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
(() => {
  const D = JSON.parse(document.getElementById('data').textContent);
  const LANES = ['#8f84d6', '#e8823a', '#3fa06a', '#d4588f', '#3a9fb5', '#c9a227', '#9b6fd0', '#7b8f3a'];
  const col = k => LANES[k % LANES.length];
  const RH = 30, LW = 16, X0 = 14, R = 5;
  const rows = D.commits.slice();
  let off = 0;
  if (D.dirty) { rows.unshift({ unc: true, lane: D.headLane ?? 0 }); off = 1; }
  const gw = Math.max(70, X0 * 2 + (D.width - 1) * LW);
  const grid = document.getElementById('grid');
  grid.style.setProperty('--gw', gw + 'px'); grid.style.setProperty('--rh', RH + 'px');
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const when = iso => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }); };

  document.getElementById('name').textContent = D.name;
  const head = D.commits.find(c => c.hash === D.head);
  const hl = head && head.labels.find(l => l.kind === 'head');
  const st = [];
  st.push(D.branch ? `on <b>${esc(D.branch)}</b>` : 'no branch checked out');
  if (hl && hl.ahead) st.push(`<b>${hl.ahead}</b> commit${hl.ahead > 1 ? 's' : ''} not on GitHub yet`);
  if (hl && hl.behind) st.push(`GitHub has <b>${hl.behind}</b> new`);
  if (D.remotes.length && hl && !hl.ahead && !hl.behind) st.push('in step with GitHub');
  if (!D.remotes.length) st.push('no online copy yet');
  if (D.dirty) st.push(`<b>${D.dirty}</b> file${D.dirty > 1 ? 's' : ''} changed, not committed`);
  document.getElementById('status').innerHTML = st.join('<span aria-hidden="true">·</span>');
  document.getElementById('foot').textContent = D.total > D.commits.length
    ? `Showing the newest ${D.commits.length} of ${D.total} commits. Drawn ${when(D.generated)}.`
    : `${D.total} commits. Drawn ${when(D.generated)}.`;

  // rows
  const box = document.getElementById('rows');
  const pills = c => (c.labels || []).map(l => {
    const tint = l.kind === 'remote' ? 'var(--muted)' : col(c.lane);
    let h = `<span class="pill ${l.kind}" style="--c:${tint}">${esc(l.name)}</span>`;
    if (l.ahead) h += `<span class="badge">${l.ahead} ahead</span>`;
    if (l.behind) h += `<span class="badge">${l.behind} behind</span>`;
    return h; }).join('');
  box.innerHTML = rows.map((c, i) => c.unc
    ? `<div class="r unc" role="option" tabindex="0" data-i="${i}"><div></div><div class="desc"><span class="s">Uncommitted changes</span></div><div class="when"></div><div class="who"></div><div class="hash">*</div></div>`
    : `<div class="r" role="option" tabindex="0" data-i="${i}"><div></div><div class="desc">${pills(c)}<span class="s">${esc(c.subject)}</span></div>`
      + `<div class="when">${esc(when(c.date))}</div><div class="who">${esc(c.author)}</div><div class="hash">${c.hash.slice(0, 7)}</div></div>`).join('');

  // graph
  const x = l => X0 + l * LW, y = i => (i + off) * RH + RH / 2, H = rows.length * RH;
  let paths = '', dots = '';
  for (const [fr, fl, to, tl, el] of D.edges) {
    const x1 = x(fl), y1 = y(fr), xe = x(el), c = col(el);
    const y2 = to === null ? H : y(to), x2 = to === null ? xe : x(tl);
    let d = `M${x1} ${y1}`;
    if (to !== null && to === fr + 1 && (xe !== x1 || xe !== x2)) d += ` C${x1} ${y1 + RH * .6} ${x2} ${y2 - RH * .6} ${x2} ${y2}`;
    else {
      let yy = y1;
      if (xe !== x1) { d += ` C${x1} ${y1 + RH * .5} ${xe} ${y1 + RH * .5} ${xe} ${y1 + RH}`; yy = y1 + RH; }
      const yEnd = (to !== null && xe !== x2) ? y2 - RH : y2;
      if (yEnd > yy) d += ` L${xe} ${yEnd}`;
      if (to !== null && xe !== x2) d += ` C${xe} ${y2 - RH * .5} ${x2} ${y2 - RH * .5} ${x2} ${y2}`;
    }
    paths += `<path d="${d}" fill="none" stroke="${c}" stroke-width="2.5" stroke-linecap="round"/>`;
  }
  if (D.dirty && D.headRow !== null) {
    const xl = x(D.headLane);
    paths += `<path d="M${xl} ${RH / 2} L${xl} ${y(D.headRow)}" stroke="var(--muted)" stroke-width="2" stroke-dasharray="2 4" fill="none"/>`;
    dots += `<circle cx="${xl}" cy="${RH / 2}" r="${R}" fill="var(--bg)" stroke="var(--muted)" stroke-width="2" stroke-dasharray="3 2"/>`;
  }
  D.commits.forEach((c, i) => {
    const cx = x(c.lane), cy = y(i), me = col(c.lane);
    const ring = `<circle cx="${cx}" cy="${cy}" r="${R + 2}" fill="var(--panel)"/>`;
    if (c.parents.length > 1) {
      const other = D.edges.find(e => e[0] === i && e[4] !== c.lane), b = other ? col(other[4]) : me;
      dots += ring + (c.pushed
        ? `<path d="M${cx} ${cy - R} A${R} ${R} 0 0 0 ${cx} ${cy + R} Z" fill="${me}"/><path d="M${cx} ${cy - R} A${R} ${R} 0 0 1 ${cx} ${cy + R} Z" fill="${b}"/>`
        : `<path d="M${cx} ${cy - R} A${R} ${R} 0 0 0 ${cx} ${cy + R}" fill="none" stroke="${me}" stroke-width="2.5"/><path d="M${cx} ${cy - R} A${R} ${R} 0 0 1 ${cx} ${cy + R}" fill="none" stroke="${b}" stroke-width="2.5"/>`);
    } else dots += ring + (c.pushed ? `<circle cx="${cx}" cy="${cy}" r="${R}" fill="${me}"/>`
                                  : `<circle cx="${cx}" cy="${cy}" r="${R - .5}" fill="var(--panel)" stroke="${me}" stroke-width="2.5"/>`);
    if (c.hash === D.head) dots += `<circle cx="${cx}" cy="${cy}" r="${R + 4}" fill="none" stroke="var(--fg)" stroke-width="1.5"/>`;
  });
  box.insertAdjacentHTML('beforeend', `<svg class="graph" width="${gw}" height="${H}" viewBox="0 0 ${gw} ${H}" aria-hidden="true">${paths}${dots}</svg>`);

  // details
  const det = document.getElementById('details'), mainEl = document.querySelector('.main');
  let open = false;
  function setOpen(v) {
    open = v; det.hidden = !v; mainEl.classList.toggle('closed', !v); document.body.classList.toggle('open', v);
    if (!v) box.querySelectorAll('.r.sel').forEach(r => { r.classList.remove('sel'); r.setAttribute('aria-selected', 'false'); });
  }
  const closeBtn = '<button class="close" id="close" type="button" aria-label="Close details" title="Close (Esc)">×</button>';
  function bindClose() { document.getElementById('close').addEventListener('click', () => {
    setOpen(false); const b = box.querySelector(`.r[data-i="${cur}"]`); if (b) b.focus({ preventScroll: true }); }); }
  const short = h => h.slice(0, 7);
  function show(i) {
    box.querySelectorAll('.r.sel').forEach(r => { r.classList.remove('sel'); r.setAttribute('aria-selected', 'false'); });
    const btn = box.querySelector(`.r[data-i="${i}"]`); if (!btn) return;
    btn.classList.add('sel'); btn.setAttribute('aria-selected', 'true');
    const c = rows[i];
    if (c.unc) {
      const fs = D.dirtyFiles || [], max = Math.max(1, ...fs.map(f => (f.add || 0) + (f.del || 0)));
      const item = f => `<div class="fu"><span class="k ${f.kind}">${f.kind}</span><span class="p">${esc(f.path)}${f.old ? `<small>was ${esc(f.old)}</small>` : ''}</span>`
        + `<span class="n">${f.add === null ? (f.kind === 'new' ? 'file' : 'binary') : `<span class="a">+${f.add}</span> <span class="d">−${f.del || 0}</span>`}</span>`
        + `<span class="bar">${f.add === null ? '' : `<i class="a" style="width:${f.add / max * 100}%"></i><i class="d" style="width:${(f.del || 0) / max * 100}%"></i>`}</span></div>`;
      const staged = fs.filter(f => f.staged), loose = fs.filter(f => !f.staged);
      const group = (title, list, note) => list.length ? `<div class="files"><h3>${title} (${list.length})</h3>${note ? `<p class="foot">${note}</p>` : ''}${list.map(item).join('')}</div>` : '';
      det.innerHTML = closeBtn + `<span class="where"><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--muted)" stroke-width="2" stroke-dasharray="3 2"/></svg>Not committed yet</span>
        <div class="msg"><span class="lbl">Uncommitted changes</span><h2>${D.dirty} file${D.dirty > 1 ? 's' : ''} changed on this computer</h2>
        <p class="body">Tell your agent “commit my work” to seal them in a box.</p></div>
        ${group('Staged', staged, 'Marked for the next commit.')}${group(staged.length ? 'Not staged' : 'Changed files', loose, '')}
        ${D.dirty > fs.length ? `<p class="foot">and ${D.dirty - fs.length} more</p>` : ''}`;
      bindClose(); return;
    }
    const max = Math.max(1, ...c.files.map(f => (f[1] || 0) + (f[2] || 0)));
    const files = c.files.map(([p, a, d]) => {
      const bin = a === null;
      return `<div class="f"><span class="p">${esc(p)}</span><span class="n">${bin ? 'binary' : `<span class="a">+${a}</span> <span class="d">−${d}</span>`}</span>`
        + `<span class="bar">${bin ? '' : `<i class="a" style="width:${a / max * 100}%"></i><i class="d" style="width:${d / max * 100}%"></i>`}</span></div>`; }).join('');
    const parents = c.parents.map(p => {
      const j = D.commits.findIndex(k => k.hash === p);
      return j < 0 ? `<span class="mono">${short(p)}</span>` : `<a href="#" class="mono" data-go="${j + off}">${short(p)}</a>`; }).join(', ') || 'none (first commit)';
    const withs = (c.trailers || []).filter(([k]) => /co-authored-by/i.test(k)).map(([, v]) => esc(v.replace(/\s*<[^>]*>/, '')));
    const sess = (c.trailers || []).find(([k]) => /claude-session/i.test(k));
    const other = (c.trailers || []).filter(([k]) => !/co-authored-by|claude-session/i.test(k));
    det.innerHTML = closeBtn + `<span class="where">${c.pushed ? `<svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="${col(c.lane)}"/></svg>On GitHub`
        : `<svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="${col(c.lane)}" stroke-width="2"/></svg>Only on this computer`}</span>
      <div class="msg"><span class="lbl">Commit message</span><h2>${esc(c.subject)}</h2>${c.body ? `<p class="body">${esc(c.body)}</p>` : ''}</div>
      <dl class="meta"><dt>Commit</dt><dd class="mono">${c.hash}</dd><dt>Parents</dt><dd>${parents}</dd>
        <dt>Author</dt><dd>${esc(c.author)}</dd><dt>Date</dt><dd>${esc(when(c.date))}</dd>
        ${withs.length ? `<dt>With</dt><dd>${withs.join(', ')}</dd>` : ''}
        ${sess ? `<dt>Session</dt><dd><a href="${esc(sess[1])}" target="_blank" rel="noopener">open the Claude session</a></dd>` : ''}
        ${other.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
      <button class="copy" id="copy" type="button">Copy for your agent</button><span class="copied" id="copied" hidden>Copied: ${short(c.hash)} ${esc(c.subject)}</span>
      <div class="files"><h3>${c.parents.length > 1 ? 'Merge: joins two lines of work' : `${c.files.length + c.more} file${c.files.length + c.more === 1 ? '' : 's'} changed`}</h3>${files}${c.more ? `<p class="foot">and ${c.more} more</p>` : ''}</div>`;
    bindClose();
    det.querySelectorAll('[data-go]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); select(+a.dataset.go, true); }));
    document.getElementById('copy').addEventListener('click', () => {
      const text = `${short(c.hash)} ${c.subject}`, ok = () => { document.getElementById('copied').hidden = false; };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(ok, () => fallback(text, ok)); else fallback(text, ok);
    });
  }
  function fallback(text, ok) {
    const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select();
    try { document.execCommand('copy'); ok(); } catch (e) { } t.remove();
  }
  let cur = 0;
  function select(i, scroll) { cur = Math.max(0, Math.min(rows.length - 1, i)); setOpen(true); show(cur);
    const b = box.querySelector(`.r[data-i="${cur}"]`); if (scroll && b) { b.focus({ preventScroll: true }); b.scrollIntoView({ block: 'nearest' }); } }
  // Clicking the open commit again closes the panel, so the whole graph shows
  box.addEventListener('click', e => { const b = e.target.closest('.r'); if (!b) return;
    const i = +b.dataset.i; if (open && i === cur) setOpen(false); else select(i); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && open) setOpen(false); });
  box.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); select(cur + (e.key === 'ArrowDown' ? 1 : -1), true); } });
  const start = D.dirty ? 0 : Math.max(0, D.headRow ?? 0) + off;
  select(start, false);
})();
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="project name from the conf, or a path (default: git_master)")
    ap.add_argument("--limit", type=int, default=400, help="newest commits to draw (default 400)")
    args = ap.parse_args()
    name, repo = find_repo(args.target)
    if not (repo / ".git").exists():
        raise SystemExit(f"{repo} is not a git repository")
    commits, total, head, branch, remotes, dirty = collect(repo, args.limit)
    edges, width = layout(commits)
    head_row = next((i for i, c in enumerate(commits) if c["hash"] == head), None)
    data = {"name": name, "branch": branch, "head": head, "headRow": head_row,
            "headLane": commits[head_row]["lane"] if head_row is not None else 0,
            "remotes": remotes, "dirty": len(dirty), "dirtyFiles": dirty[:300], "total": total, "width": width, "edges": edges,
            "generated": datetime.datetime.now().astimezone().isoformat(timespec="minutes"),
            "commits": commits}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = PAGE.replace("__TITLE__", html.escape(f"{name} history")).replace("__DATA__", payload)
    out = pathlib.Path(os.path.expanduser(os.environ.get("GIT_MASTER_HOME", "~/.git_master"))) / "visualize" / f"{name}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    ahead = next((l.get("ahead", 0) for c in commits for l in c["labels"] if l["kind"] == "head"), 0)
    print(f"{out}\n{name}: {len(commits)} of {total} commits drawn, {width} lane(s), "
          f"{sum(not c['pushed'] for c in commits)} not on GitHub, branch {branch or '-'} {ahead} ahead, "
          f"{len(dirty)} uncommitted file(s)")


if __name__ == "__main__":
    main()
