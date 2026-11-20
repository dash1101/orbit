#!/usr/bin/env python3
"""Orbit's languages: the English text in Orbit's source, and the translation files in web/i18n.

Orbit is written in English. Each other language has web/i18n/<code>.json: {"English text": "translation"}.
Text that has parts that change (a version, a name) is a pattern: "Download {} from GitHub" -> "{} von GitHub laden";
the {} take the parts in order, or {1} {2} … to put them in another order. Orbit translates text as it is shown, in the
window (web/index.html) and in the Android app (Txt and TuiButton), so this tool only has to find the text.

  python3 tools/i18n.py keys              every English text found, one per line
  python3 tools/i18n.py missing <code>    numbered list (N<TAB>text) of what <code> lacks
  python3 tools/i18n.py merge <code> <list> <f>  add translations (N<TAB>translation) for a list from `missing`
  python3 tools/i18n.py check             each language has every text and nothing stale; Android's copies match
  python3 tools/i18n.py sync              copy the files to the Android app's assets
"""
import html.parser
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "web" / "i18n"
ANDROID = ROOT / "android" / "app" / "src" / "main" / "assets" / "i18n"
KT = ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "novalabs" / "orbit"

# Source files with no text of their own to show: their strings are what they look for in other text (the release-note
# shortener looks for words like "new" and "updating from" in an app's release notes).
NO_TEXT = {"notes.rs", "Notes.kt"}

# code, name in that language (English first; it has no file)
LANGUAGES = [("en", "English"), ("es", "Español"), ("fr", "Français"), ("de", "Deutsch"), ("pt-BR", "Português (Brasil)"),
             ("it", "Italiano"), ("ru", "Русский"), ("ja", "日本語"), ("zh-CN", "简体中文"), ("ko", "한국어")]

# ---------------------------------------------------------------------------------------------- reading literals
ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'", "`": "`", "$": "$"}


def _unescape(body, rust=False):
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        n = body[i + 1] if i + 1 < len(body) else ""
        if rust and n == "\n":  # a line continuation: the newline and the next line's leading space go
            i += 2
            while i < len(body) and body[i] in " \t\n\r":
                i += 1
            continue
        if n == "u":
            m = re.match(r"u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})", body[i + 1:])
            if m:
                out.append(chr(int(m.group(1) or m.group(2), 16)))
                i += 1 + m.end()
                continue
        if n == "x":
            m = re.match(r"x([0-9a-fA-F]{2})", body[i + 1:])
            if m:
                out.append(chr(int(m.group(1), 16)))
                i += 1 + m.end()
                continue
        if n == "\n":  # JS line continuation
            i += 2
            continue
        out.append(ESC.get(n, n))
        i += 2
    return "".join(out)


def literals(src, lang):
    """(text, start, end, kind) for each string literal; kind "t" for JS template literals and Kotlin strings with $."""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if lang == "rs" and c == "r" and re.match(r'r#*"', src[i:]) and not (i and (src[i - 1].isalnum() or src[i - 1] == "_")):
            m = re.match(r'r(#*)"', src[i:])
            close = '"' + m.group(1)
            j = src.find(close, i + m.end())
            i = n if j < 0 else j + len(close)
            continue
        if c == "'" and lang in ("rs", "kt"):
            m = re.match(r"'(\\.|\\u\{[0-9a-fA-F]+\}|[^\\'])'", src[i:])
            if m:
                i += m.end()
                continue
            i += 1  # a Rust lifetime
            continue
        if lang == "kt" and src.startswith('"""', i):
            j = src.find('"""', i + 3)
            i = n if j < 0 else j + 3
            continue
        if c in "\"'`" and (c == '"' or lang == "js"):
            j, depth, buf = i + 1, 0, []
            while j < n:
                d = src[j]
                if d == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if c == "`" and src.startswith("${", j):  # ${…} → {}
                    k, depth = j + 2, 1
                    while k < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[k], 0)
                        k += 1
                    buf.append("\x00")
                    j = k
                    continue
                if lang == "kt" and d == "$" and j + 1 < n and (src[j + 1] == "{" or src[j + 1].isalpha()):
                    if src[j + 1] == "{":
                        k, depth = j + 2, 1
                        while k < n and depth:
                            depth += {"{": 1, "}": -1}.get(src[k], 0)
                            k += 1
                    else:
                        k = j + 1
                        while k < n and (src[k].isalnum() or src[k] == "_"):
                            k += 1
                    buf.append("\x00")
                    j = k
                    continue
                if d == c:
                    break
                if d == "\n" and c != "`" and lang != "rs":
                    break
                buf.append(d)
                j += 1
            body = "".join(buf)
            text = _unescape(body, rust=lang == "rs")
            yield text, i, j + 1, ("t" if "\x00" in text else "s")
            i = j + 1
            continue
        if lang == "js" and c == "/" and re.match(r"[=(,:!&|?{};\[]\s*$", src[max(0, i - 20):i].rstrip()[-1:] or "("):
            m = re.match(r"/(\\.|\[(\\.|[^\]\\])*\]|[^/\\\n\[])+/[a-z]*", src[i:])
            if m:
                i += m.end()
                continue
        i += 1


GLUE = re.compile(r"\s*\+\s*([A-Za-z_$][\w$.]*(\([^()\"'`]*\))?(\s*\|\|\s*\"[^\"]*\")?|\([^()\"'`]*\))\s*(\+\s*|(?=[,)\];}]))")


def joined(src, lang):
    """Literals, with "a" + "b" (and Rust's "a" "b" in concat!) joined into one text."""
    items = list(literals(src, lang))
    k = 0
    while k < len(items):
        text, s, e, kind = items[k]
        while True:
            nxt = items[k + 1] if k + 1 < len(items) else None
            if nxt and re.fullmatch(r"\s*\+\s*", src[e:nxt[1]]):
                k += 1
                text, e = text + nxt[0], nxt[2]
                kind = "t" if "t" in (kind, nxt[3]) else "s"
                continue
            g = GLUE.match(src, e) if lang == "js" else None
            if g and (g.group(4).strip() == "" or (nxt and g.end() == nxt[1])):
                text += "\x00"
                e = g.end()
                kind = "t"
                if nxt and g.end() == nxt[1]:
                    k += 1
                    text, e = text + nxt[0], nxt[2]
                    continue
            break
        yield text, s, e, kind
        k += 1


FORMAT_MACRO = re.compile(r"\b(format|println|eprintln|print|panic|write|writeln|bail|anyhow)!\s*\(\s*(\w+\s*,\s*)?$")


def rust_template(text):
    """A format! string → its pattern: {} for each placeholder, {{ }} as braces (None when it has real braces)."""
    t = text.replace("{{", "\x01").replace("}}", "\x02")
    t = re.sub(r"\{[^{}]*\}", "\x00", t)
    if "\x01" in t or "\x02" in t or "{" in t or "}" in t:
        return None
    return t


# ---------------------------------------------------------------------------------------------- what is text
CODE_START = ("http", "/", "-", "$", "%", "~", "#", "<", "[", "{", ".", "\\", "*", "+", "@", "&", "|", "!", "^", ":", ",", ";",
              "\x00/", "org.", "com.", "app.", "io.")
CODE_BITS = ("&&", "||", "$(", "::", "=>", "()", "\\\\", "[Unit]", "[Desktop", "</", "/>", "\n\n\n", "%s", "%d", ".exe ",
             "Get-", "New-Object", "WScript", "Start-Process", "-Command", "ExecStart", "{{", "\x00\x00")
COMMANDS = re.compile(r"^(sudo|systemctl|apt|dnf|pacman|brew|xdg-open|kitty|bash|sh|powershell|pwsh|cmd|reg|tar|curl|wget|chmod|"
                      r"loginctl|journalctl|notify-send|gsettings|flatpak|snap|python3?|py|pip|npm|node|deno|cargo|git|gh|rundll32|schtasks|taskkill|icacls|Remove-|Set-|Get-|New-)\b")


NAMES = {n for _, n in LANGUAGES}
WORDS = ROOT / "tools" / "i18n-words.txt"   # one-word labels Orbit shows (found by looking at its screens)


def is_text(t, words=False):
    s = t.replace("\x00", "{}").strip()
    if len(s) < 2 or not re.search(r"[A-Za-z]{2}", s):
        return False
    if s.replace("{}", "X").startswith(CODE_START) or any(b in t for b in CODE_BITS) or re.search(r"\w=\S|\S=\w", s):
        return False
    # a command line, not a sentence that starts with a program's name ("kitty 0.36 or newer (…)")
    if COMMANDS.match(s) and not re.search(r"\b(or|and|the|its|it|is|for|to|uses|needs|newer)\b", s):
        return False
    if re.fullmatch(r"[a-z0-9_.:/-]+", s) and re.search(r"[0-9_.:/-]", s):  # identifiers, keys, file names, CSS classes
        return False
    if re.search(r"\.(svg|png|json|js|css|html|exe|sh|desktop|service|plist|deb|dmg|apk|lnk|txt|md|ini|toml)\b", s) and " " not in s:
        return False
    if " " not in s:  # one word: only from the page's HTML or WORDS (in code most single words are names of things)
        return words and bool(re.fullmatch(r"[A-Za-z][a-z]+[.!?…:]?", s))
    if s in NAMES:
        return False
    if re.search(r"[;{}]\s*$", t) or re.match(r"^[a-z-]+\s*:\s*[a-z0-9#-]", s):  # CSS
        return False
    return True


def keep(out, t, words=False):
    t = t.strip()
    if is_text(t, words):
        out.add(t.replace("\x00", "{}"))


class _Html(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.skip = set(), 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "kbd", "code", "pre"):
            self.skip += 1
        for k, v in attrs:
            if k in ("title", "placeholder", "aria-label", "alt") and v:
                keep(self.found, v)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "kbd", "code", "pre"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            keep(self.found, " ".join(data.split()), words=True)


def keys():
    out = set()
    # the desktop window
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    p = _Html()
    p.feed(re.sub(r"<script>.*?</script>|<style>.*?</style>", "", page, flags=re.S))
    out |= p.found
    for script in re.findall(r"<script>(.*?)</script>", page, flags=re.S):
        for text, s, e, kind in joined(script, "js"):
            keep(out, text)
    # Orbit for desktop (Rust): every text a recipe, app or the server can show
    for f in sorted((ROOT / "desktop" / "src").rglob("*.rs")):
        if f.name in NO_TEXT:
            continue
        src = f.read_text(encoding="utf-8").split("#[cfg(test)]")[0]
        for text, s, e, kind in joined(src, "rs"):
            if FORMAT_MACRO.search(src[max(0, s - 80):s]):
                text = rust_template(text)
                if text is None:
                    continue
            keep(out, text)
    # Orbit for Android
    for f in sorted(KT.glob("*.kt")):
        if f.name in NO_TEXT:
            continue
        src = f.read_text(encoding="utf-8")
        for text, s, e, kind in joined(src, "kt"):
            keep(out, text)
    if WORDS.exists():
        out |= {w.strip() for w in WORDS.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")}
    # the apps' descriptions and notes from Orbit's catalog
    cat = json.loads((ROOT / "packaging" / "catalog.json").read_text(encoding="utf-8"))
    for a in cat["apps"]:
        for t in (a.get("blurb"), (a.get("android") or {}).get("note"), (a.get("android") or {}).get("extra")):
            if t:
                keep(out, t)
    return sorted(out)


# ---------------------------------------------------------------------------------------------- files
def load(code):
    f = I18N / f"{code}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save(code, d):
    I18N.mkdir(parents=True, exist_ok=True)
    k = keys()
    order = {t: i for i, t in enumerate(k)}
    d = {t: d[t] for t in sorted(d, key=lambda t: order.get(t, len(order)))}
    (I18N / f"{code}.json").write_text(json.dumps(d, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


def placeholders(t):
    return len(re.findall(r"\{\d*\}", t))


def problems(code, d, k):
    out = []
    ks = set(k)
    for t in k:
        if t not in d:
            out.append(f"{code}: missing {t!r}")
        elif placeholders(t) != placeholders(d[t]):
            out.append(f"{code}: {t!r} has {placeholders(t)} {{}} but its translation has {placeholders(d[t])}")
    for t in d:
        if t not in ks:
            out.append(f"{code}: no longer in Orbit: {t!r}")
    return out


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "keys":
        print("\n".join(t.replace("\n", "\\n") for t in keys()))
    elif cmd == "missing":
        d, k = load(argv[2]), keys()
        for i, t in enumerate(k):
            if t not in d:
                print(f"{i}\t{t}".replace("\n", "\\n"))
    elif cmd == "merge":
        code, k = argv[2], keys()
        d = load(code)
        src = {}
        for line in Path(argv[3]).read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                n, t = line.split("\t", 1)
                src[n.strip()] = t.replace("\\n", "\n")
        for line in Path(argv[4]).read_text(encoding="utf-8").splitlines():
            if "\t" not in line:
                continue
            n, tr = line.split("\t", 1)
            if n.strip() in src:
                d[src[n.strip()]] = tr.replace("\\n", "\n")
        d = {t: v for t, v in d.items() if t in set(k)}
        save(code, d)
        print(f"{code}: {len(d)} of {len(k)}")
    elif cmd == "sync":
        ANDROID.mkdir(parents=True, exist_ok=True)
        for code, _ in LANGUAGES[1:]:
            (ANDROID / f"{code}.json").write_bytes((I18N / f"{code}.json").read_bytes())
    elif cmd == "check":
        k, errs = keys(), []
        for code, _ in LANGUAGES[1:]:
            errs += problems(code, load(code), k)
            a = ANDROID / f"{code}.json"
            if not a.exists() or a.read_bytes() != (I18N / f"{code}.json").read_bytes():
                errs.append(f"{code}: the Android app's copy differs (run tools/i18n.py sync)")
        print("\n".join(errs[:60]) + (f"\n… {len(errs) - 60} more" if len(errs) > 60 else "") if errs else f"ok: {len(k)} texts")
        return 1 if errs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
