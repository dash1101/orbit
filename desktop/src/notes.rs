//! Short patch notes: what's new in an app's releases, from their GitHub release text, a few lines per version.
//!
//! Release notes are written for people, in Markdown, and every app writes them a little differently. This keeps
//! the top-level list items of the part about that version ("What's new", "New", "Fixed"…, or "New in <version>"
//! when one text covers several versions), each cut to its bold title or its first sentence.
use crate::net;
use crate::util::version_of;
use serde_json::{json, Value};

/// At most this many lines per version, and this many versions.
pub const PER_VERSION: usize = 5;
pub const VERSIONS: usize = 3;
const LINE: usize = 140;

/// A heading (### …, or a line that is all bold) that isn't about what changed ends the list.
fn heading(line: &str) -> Option<String> {
    let t = line.trim();
    let text = if let Some(h) = t.strip_prefix('#') {
        h.trim_start_matches('#').trim().to_string()
    } else if t.len() > 4 && t.starts_with("**") && t.ends_with("**") && !t[2..t.len() - 2].contains("**") {
        t[2..t.len() - 2].trim().trim_end_matches(':').to_string()
    } else {
        return None;
    };
    Some(text)
}

fn about_changes(h: &str) -> bool {
    let l = h.to_lowercase();
    ["new", "fix", "change", "improve", "add", "highlight", "update", "what"].iter().any(|w| l.contains(w))
        && !l.contains("updating from") && !l.contains("install") && !l.contains("download")
}

/// "New in 0.8.2" / "What's new in v1.2" → the version it names.
fn names_version(h: &str) -> Option<String> {
    let l = h.to_lowercase();
    let i = l.find(" in ")?;
    let v = h[i + 4..].trim().trim_start_matches(['v', 'V']).split([' ', ',', ':', '(']).next()?.to_string();
    if net::version_tuple(&v).is_empty() { None } else { Some(v) }
}

/// Markdown to plain text: links keep their words, emphasis and code marks go.
fn plain(s: &str) -> String {
    let link = regex::Regex::new(r"!?\[([^\]]*)\]\([^)]*\)").unwrap();
    let s = link.replace_all(s, "$1");
    s.replace("**", "").replace("__", "").replace('`', "").replace(" *", " ").replace("* ", " ")
        .split_whitespace().collect::<Vec<_>>().join(" ")
}

/// One list item, short: its bold title when it starts with one, else its first sentence, at most LINE characters.
fn shorten(item: &str) -> String {
    let t = item.trim();
    let text = if let Some(rest) = t.strip_prefix("**") {
        match rest.find("**") {
            Some(end) if end > 3 => rest[..end].trim().trim_end_matches([':', '.']).to_string(),
            _ => plain(t),
        }
    } else {
        let p = plain(t);
        let cut = p.char_indices().find(|&(i, c)| {
            matches!(c, '.' | '!' | '?') && p[i + 1..].starts_with(' ') && i > 20
        }).map(|(i, _)| i);
        match cut { Some(i) => p[..i].to_string(), None => p }
    };
    let mut text = plain(&text);
    if text.chars().count() > 90 {
        // a long sentence: up to its colon, when that leaves a whole thought
        if let Some(i) = text.find(": ").filter(|&i| i > 30) {
            text.truncate(i);
        }
    }
    if text.chars().count() <= LINE {
        return text;
    }
    let cut: String = text.chars().take(LINE).collect();
    let cut = cut.rsplit_once(' ').map(|(a, _)| a.to_string()).unwrap_or(cut);
    format!("{}…", cut.trim_end_matches([',', ';', ':']))
}

/// The short notes of one release: (lines, how many more there were).
pub fn short(body: &str, version: &str) -> (Vec<String>, usize) {
    let want = version_of(version);
    let mut items: Vec<String> = vec![];
    let mut cur: Option<String> = None;
    let mut on = true; // in a part about what changed
    let mut seen_version_heading = false;
    let push = |cur: &mut Option<String>, items: &mut Vec<String>| {
        if let Some(c) = cur.take() {
            let s = shorten(&c);
            if !s.is_empty() {
                items.push(s);
            }
        }
    };
    for raw in body.replace("\r\n", "\n").lines() {
        if raw.trim_start().starts_with('|') {
            push(&mut cur, &mut items);
            break; // a table (downloads): the notes are over
        }
        if let Some(h) = heading(raw) {
            push(&mut cur, &mut items);
            if let Some(v) = names_version(&h) {
                if version_of(&v) != want {
                    if seen_version_heading || !items.is_empty() {
                        break; // the next version's notes
                    }
                    on = false; // notes of another version first: skip them
                    continue;
                }
                seen_version_heading = true;
                on = true;
                continue;
            }
            on = about_changes(&h) && (on || !seen_version_heading);
            continue;
        }
        if !on {
            continue;
        }
        let indent = raw.len() - raw.trim_start().len();
        let t = raw.trim_start();
        let bullet = (t.starts_with("- ") || t.starts_with("* ") || t.starts_with("+ ")) && indent < 2;
        if bullet {
            push(&mut cur, &mut items);
            cur = Some(t[2..].to_string());
        } else if t.is_empty() {
            push(&mut cur, &mut items);
        } else if let Some(c) = cur.as_mut() {
            let sub = t.starts_with("- ") || t.starts_with("* ");
            if !sub {
                c.push(' ');
                c.push_str(t);
            }
        }
    }
    push(&mut cur, &mut items);
    let more = items.len().saturating_sub(PER_VERSION);
    items.truncate(PER_VERSION);
    (items, more)
}

/// What's new between the version installed and the newest one: newest first, at most VERSIONS releases, from the
/// release list (Sources' cache: each release with "tag", "url" and "notes"). Empty when nothing is newer.
pub fn between(releases: &[Value], installed: &str, latest: &str) -> Value {
    let mut out = vec![];
    for r in releases {
        let tag = r["tag"].as_str().unwrap_or("");
        let v = version_of(tag);
        if v.is_empty() || net::is_newer(&v, latest) || (!installed.is_empty() && !net::is_newer(&v, installed)) {
            continue;
        }
        let (lines, more) = short(r["notes"].as_str().unwrap_or(""), &v);
        out.push(json!({"version": v, "url": r["url"], "lines": lines, "more": more}));
        if out.len() >= VERSIONS || installed.is_empty() {
            break; // a first install: only the newest one
        }
    }
    json!(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_and_first_sentences() {
        let body = "- **CPU power: run the server cooler when you want to.** Quick panel → CPU power has three\n  controls:\n  - **Power plan:** Full speed\n- **The alert no longer comes and goes.** Once it's hot…\n- Widgets can be moved all the way to the right. In the window they used to stop short.\n\n";
        let (l, more) = short(body, "1.0.0");
        assert_eq!(l, vec!["CPU power: run the server cooler when you want to", "The alert no longer comes and goes",
                           "Widgets can be moved all the way to the right"]);
        assert_eq!(more, 0);
    }

    #[test]
    fn only_this_versions_part() {
        let orbit = "**Orbit** by NovaLabs: one place for apps.\n\n**New in 0.8.2**\n\n- **Orbit has a new license.** From this version on…\n\n**New in 0.8.1**\n\n- **xmas-tui gets a page** once…\n\n| System | Download |\n";
        assert_eq!(short(orbit, "v0.8.2").0, vec!["Orbit has a new license"]);
        assert_eq!(short(orbit, "0.8.1").0, vec!["xmas-tui gets a page"]);
        let starcast = "### What's new in 0.2.10-alpha\n\n- **New license:** YTM Starcast is now…\n- The About screens show who made it.\n\n### Updating from 0.2.0-alpha.10 or older\n\n- **Computer:** update by hand\n\n[Install](https://x) · [Changelog](https://y)\n";
        assert_eq!(short(starcast, "v0.2.10-alpha").0, vec!["New license", "The About screens show who made it."]);
        let ytdl = "### New\n- **Privacy page** at `/privacy`, linked…\n- `privacy_contact` setting (also in [Admin](https://x)): an email address.\n### Fixed\n- A bug.\n";
        assert_eq!(short(ytdl, "2.5.0").0, vec!["Privacy page", "privacy_contact setting (also in Admin): an email address.", "A bug."]);
    }

    #[test]
    fn long_lines_are_cut_and_many_counted() {
        let long = format!("- {}\n", "word ".repeat(60));
        let l = short(&long, "1").0;
        assert!(l[0].chars().count() <= LINE + 1 && l[0].ends_with('…'));
        let many: String = (1..=8).map(|i| format!("- Change number {i} here\n")).collect();
        let (l, more) = short(&many, "1");
        assert_eq!((l.len(), more), (PER_VERSION, 3));
    }

    #[test]
    fn versions_between_installed_and_latest() {
        let rels: Vec<Value> = ["1.4.0", "1.3.0", "1.2.0", "1.1.0", "1.0.0"].iter()
            .map(|v| json!({"tag": format!("v{v}"), "url": "", "notes": format!("- In {v}.\n")})).collect();
        let b = between(&rels, "1.1.0", "1.3.0");
        let vs: Vec<&str> = b.as_array().unwrap().iter().map(|x| x["version"].as_str().unwrap()).collect();
        assert_eq!(vs, vec!["1.3.0", "1.2.0"]);
        assert_eq!(between(&rels, "1.0.0", "1.4.0").as_array().unwrap().len(), VERSIONS);
        assert_eq!(between(&rels, "", "1.4.0").as_array().unwrap().len(), 1);
        assert!(between(&rels, "1.4.0", "1.4.0").as_array().unwrap().is_empty());
    }
}
