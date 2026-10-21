#!/usr/bin/env python3
"""A commit message that says what changed, for commits nobody is there to describe (the nightly).

Reads `git diff --cached --name-status` on stdin; the prefix is the first argument.
  Sync from laptop 2026-10-21: CLAUDE.md, workspace/outreach (3 files), +2 more

  M CLAUDE.md
  A workspace/outreach/2026-10-21-acme.md
  ...
"""
import sys

MAX_SUBJECT, MAX_LINES = 72, 60
prefix = sys.argv[1] if len(sys.argv) > 1 else "Sync"
changes = []
for line in sys.stdin.read().splitlines():
    parts = line.split("\t")
    if len(parts) >= 2:
        status, paths = parts[0][:1], parts[1:]
        changes.append((status, " -> ".join(paths), paths[-1]))

groups = {}
for _, _, path in changes:
    bits = path.split("/")
    group = "/".join(bits[:2]) if len(bits) > 2 else (bits[0] if len(bits) == 2 else path)
    groups[group] = groups.get(group, 0) + 1
ranked = sorted(groups.items(), key=lambda g: (-g[1], g[0]))

subject, shown = prefix, 0
for group, n in ranked:
    part = group if n == 1 else f"{group} ({n} files)"
    left = len(ranked) - shown - 1
    tail = f", +{left} more" if left else ""
    candidate = f"{subject}{':' if shown == 0 else ','} {part}"
    if shown and len(candidate + tail) > MAX_SUBJECT:
        break
    subject, shown = candidate, shown + 1
if shown < len(ranked):
    subject += f", +{len(ranked) - shown} more"

body = [f"{s} {p}" for s, p, _ in changes[:MAX_LINES]]
if len(changes) > MAX_LINES:
    body.append(f"... and {len(changes) - MAX_LINES} more files")
print(subject + ("\n\n" + "\n".join(body) if body else ""))
