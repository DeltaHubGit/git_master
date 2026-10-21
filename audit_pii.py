#!/usr/bin/env python3
"""Look for personal/financial identifiers in a folder, including inside PDF, Word and Excel files.

gitleaks finds API keys and tokens; this finds what it doesn't: IBANs, card numbers,
Slovenian EMSO personal ID numbers, and bank/identity keywords. Values are masked.

Usage: audit_pii.py <folder> [<folder> ...]
       audit_pii.py --list <folder>             (only the flagged paths, one per line; for nightly.sh)
       audit_pii.py --valid-ibans < candidates   (prints only checksum-valid IBANs)
"""
import os
import re
import subprocess
import sys
import zipfile

TEXT_EXT = {".md", ".txt", ".csv", ".tsv", ".json", ".html", ".htm", ".yaml", ".yml",
            ".py", ".sh", ".js", ".ts", ".toml", ".ini", ".cfg", ".xml", ".eml"}
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}

IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
CARD = re.compile(r"(?<![+\d])\b(?:\d[ -]?){12,18}\d\b")
EMSO = re.compile(r"\b(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])(?:9\d\d|0[0-2]\d)\d{6}\b")
KEYWORDS = re.compile(
    r"routing (?:no\b|number)|account (?:no\b|number|holder)|sort code|\bswift\b|\bbic\b|"
    r"passport (?:no\b|number)|date of birth|\bdob\b|social security|\bssn\b|emšo|\bemso\b|"
    r"driver'?s licen[cs]e|osebna izkaznica|davčna številka|tax (?:id|number)", re.I)

IBAN_LEN = {"SI": 19, "DE": 22, "AT": 20, "HR": 21, "IT": 27, "FR": 27, "GB": 22, "NL": 18,
            "BE": 16, "ES": 24, "CH": 21, "LT": 20, "IE": 22, "PL": 28, "CZ": 24, "SK": 24,
            "HU": 28, "LU": 20, "PT": 25, "SE": 24, "DK": 18, "FI": 18, "NO": 15, "EE": 20}


def iban_valid(s):
    s = s.replace(" ", "")
    if IBAN_LEN.get(s[:2]) != len(s):
        return False
    n = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(n) % 97 == 1


def luhn(s):
    d = [int(c) for c in re.sub(r"\D", "", s)][::-1]
    return len(d) >= 13 and sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2)
                                for i, x in enumerate(d)) % 10 == 0


def mask(s):
    s = s.strip()
    return s[:4] + "…" + s[-2:] if len(s) > 6 else "…"


def extract(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXT:
            return open(path, encoding="utf-8", errors="ignore").read()
        if ext == ".pdf":
            import pypdf
            return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages)
        if ext == ".docx":
            xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "ignore")
            return re.sub(r"<[^>]+>", " ", re.sub(r"</w:p>", "\n", xml))
        if ext in (".doc", ".rtf", ".odt"):  # macOS textutil only
            return subprocess.run(["textutil", "-convert", "txt", "-stdout", path],
                                  capture_output=True, text=True).stdout
        if ext in (".xlsx", ".pptx"):
            z = zipfile.ZipFile(path)
            parts = [n for n in z.namelist() if n.endswith(".xml") and
                     (n.startswith("xl/sharedStrings") or n.startswith("xl/worksheets/") or
                      n.startswith("ppt/slides/"))]
            return " ".join(re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "ignore")) for n in parts)
    except Exception as e:  # unreadable file: report it rather than silently pass
        return f"__UNREADABLE__ {e}"
    return None


LIST_ONLY = False  # --list: print flagged paths only


def audit(root):
    hits = 0
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in files:
            path = os.path.join(dirpath, f)
            text = extract(path)
            if text is None:
                continue
            rel = os.path.relpath(path, root)
            if text.startswith("__UNREADABLE__"):
                print(f"  UNREADABLE  {rel}: {text[15:80]}", file=sys.stderr if LIST_ONLY else sys.stdout)
                continue
            found = []
            ibans = {m for m in IBAN.findall(text) if iban_valid(m)}
            if ibans:
                found.append(f"valid IBAN x{len(ibans)} ({', '.join(mask(i) for i in sorted(ibans)[:3])})")
            # Grouped digits only (1234 5678 ...), so plain IDs like Shopify order numbers don't match
            cards = {m for m in CARD.findall(text) if re.search(r"[ -]", m) and luhn(m)}
            if cards:
                found.append(f"card-like x{len(cards)}")
            emso = set(EMSO.findall(text)) if re.search(r"emšo|emso", text, re.I) else set()
            if emso:
                found.append(f"EMŠO x{len(emso)} ({', '.join(mask(e) for e in sorted(emso)[:3])})")
            kws = sorted({k.lower() for k in KEYWORDS.findall(text)})
            if found or kws:
                hits += 1
                if LIST_ONLY:
                    print(rel)
                else:
                    print(f"  {rel}\n      {'; '.join(found) or '-'} | keywords: {', '.join(kws) or '-'}")
    return hits


if __name__ == "__main__":
    if sys.argv[1:] == ["--valid-ibans"]:  # filter mode for lib.sh: stdin candidates -> valid ones
        for line in sys.stdin:
            if iban_valid(line.strip()):
                print(line.strip())
        sys.exit(0)
    LIST_ONLY = "--list" in sys.argv[1:]
    for root in [a for a in sys.argv[1:] if a != "--list"]:
        if LIST_ONLY:
            audit(root)
            continue
        print(f"=== {root}")
        n = audit(root)
        print(f"  ({n} file(s) flagged)")
