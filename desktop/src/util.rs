//! Small things everything uses: errors the window understands, ids, sizes, checksums, quoting.
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::Read;
use std::path::Path;

/// What can go wrong in a request or an install step.
#[derive(Debug, Clone)]
pub enum Error {
    /// The request itself is wrong (400): a bad option, an unknown action.
    Bad(String),
    /// A step failed in a way the person can understand (and often fix): the hint says how.
    Failed(String, String),
    /// No such app, job or terminal (404).
    NotFound(String),
    /// The person stopped an install.
    Cancelled,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Bad(m) | Error::Failed(m, _) | Error::NotFound(m) => f.write_str(m),
            Error::Cancelled => f.write_str("cancelled"),
        }
    }
}

pub type Res<T> = Result<T, Error>;

pub fn bad(msg: impl Into<String>) -> Error {
    Error::Bad(msg.into())
}

pub fn failed(msg: impl Into<String>, hint: impl Into<String>) -> Error {
    Error::Failed(msg.into(), hint.into())
}

/// 12 random hex characters (job, terminal and request ids).
pub fn new_id() -> String {
    let mut b = [0u8; 6];
    let _ = getrandom::getrandom(&mut b);
    hex::encode(b)
}

pub fn fmt_size(n: u64) -> String {
    let mut x = n as f64;
    for unit in ["B", "KB", "MB", "GB"] {
        if x < 1000.0 || unit == "GB" {
            return if unit == "B" { format!("{x:.0} {unit}") } else { format!("{x:.1} {unit}") };
        }
        x /= 1000.0;
    }
    String::new()
}

/// `<sha256>  <name>` (or `<sha256> *<name>`) lines -> {name: sha256}.
pub fn parse_sums(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for line in text.lines() {
        let line = line.trim();
        let mut it = line.splitn(2, char::is_whitespace);
        let (Some(sum), Some(name)) = (it.next(), it.next()) else { continue };
        if sum.len() == 64 && sum.chars().all(|c| c.is_ascii_hexdigit()) {
            let name = name.trim().trim_start_matches('*').to_string();
            let sum = sum.to_lowercase();
            out.entry(name.replace('~', ".")).or_insert_with(|| sum.clone()); // GitHub stores "~" as "."
            out.insert(name, sum);
        }
    }
    out
}

pub fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex::encode(h.finalize()))
}

pub fn strip_ansi(s: &str) -> String {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"\x1b\[[0-9;?]*[A-Za-z]").unwrap());
    re.replace_all(s, "").replace('\r', "")
}

/// How a command line is shown in a log.
pub fn q(s: &str) -> String {
    if cfg!(windows) {
        if s.contains(' ') { format!("\"{s}\"") } else { s.to_string() }
    } else {
        crate::runner::sh_quote(s)
    }
}

/// "10:42:07" in local time.
pub fn clock() -> String {
    chrono::Local::now().format("%H:%M:%S").to_string()
}

pub fn version_of(tag: &str) -> String {
    tag.trim_start_matches(['v', 'V']).to_string()
}

/// Remove a file or a whole folder, whatever is there.
pub fn remove_any(p: &Path) -> std::io::Result<()> {
    match std::fs::symlink_metadata(p) {
        Ok(m) if m.is_dir() => std::fs::remove_dir_all(p),
        Ok(_) => std::fs::remove_file(p),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e),
    }
}

pub fn exists(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sums_parse_both_formats_and_github_names() {
        let s = parse_sums(&format!("{}  nova-server_0.9.13~alpha_all.deb\n{} *orbit-linux-x86_64\nnot a line\n",
                                    "a".repeat(64), "B".repeat(64)));
        assert_eq!(s["nova-server_0.9.13~alpha_all.deb"], "a".repeat(64));
        assert_eq!(s["nova-server_0.9.13.alpha_all.deb"], "a".repeat(64));
        assert_eq!(s["orbit-linux-x86_64"], "b".repeat(64));
    }

    #[test]
    fn sizes_read_like_before() {
        assert_eq!(fmt_size(0), "0 B");
        assert_eq!(fmt_size(9_914_293), "9.9 MB");
    }
}
