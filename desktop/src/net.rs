//! Small JSON HTTP client, GitHub releases and version comparison. Server-side requests send no Origin header
//! (apps treat them like curl).
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Read;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::{Arc, Mutex, OnceLock, RwLock};
use std::time::{Duration, Instant};

pub const UA: &str = "orbit-hub";

#[derive(Debug, Clone)]
pub struct HttpError {
    pub status: u16,
    pub message: String,
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

// ---- TLS -------------------------------------------------------------------------------------
fn provider() -> Arc<rustls::crypto::CryptoProvider> {
    static P: OnceLock<Arc<rustls::crypto::CryptoProvider>> = OnceLock::new();
    P.get_or_init(|| Arc::new(rustls::crypto::ring::default_provider())).clone()
}

#[derive(Debug)]
struct NoVerify(Arc<rustls::crypto::CryptoProvider>);

impl rustls::client::danger::ServerCertVerifier for NoVerify {
    fn verify_server_cert(
        &self,
        _: &rustls::pki_types::CertificateDer<'_>,
        _: &[rustls::pki_types::CertificateDer<'_>],
        _: &rustls::pki_types::ServerName<'_>,
        _: &[u8],
        _: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        _: &[u8],
        _: &rustls::pki_types::CertificateDer<'_>,
        _: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }
    fn verify_tls13_signature(
        &self,
        _: &[u8],
        _: &rustls::pki_types::CertificateDer<'_>,
        _: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }
    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

/// An HTTP client. `insecure` is only used for apps' own local addresses on a self-signed certificate (Nova's
/// public /ping): nothing secret is sent or trusted.
pub fn agent(timeout: Duration, insecure: bool) -> ureq::Agent {
    let mut b = ureq::AgentBuilder::new().timeout(timeout).user_agent(UA).redirects(5);
    if insecure {
        let cfg = rustls::ClientConfig::builder_with_provider(provider())
            .with_safe_default_protocol_versions()
            .expect("tls versions")
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(NoVerify(provider())))
            .with_no_client_auth();
        b = b.tls_config(Arc::new(cfg));
    }
    b.build()
}

// ---- apps on this computer: know quickly when one isn't running ------------------------------
// Connecting to a local port nothing listens on takes about two seconds on Windows (it retries the refused
// connection), so Orbit checks the port quickly first and remembers the answer for a few seconds.
fn seen() -> &'static Mutex<HashMap<(String, u16), (Instant, bool)>> {
    static S: OnceLock<Mutex<HashMap<(String, u16), (Instant, bool)>>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(HashMap::new()))
}
const UP_FOR: Duration = Duration::from_secs(10);
const DOWN_FOR: Duration = Duration::from_secs(3);

fn local(url: &str) -> Option<(String, u16)> {
    let u = url::Url::parse(url).ok()?;
    let host = u.host_str()?.trim_matches(|c| c == '[' || c == ']').to_string();
    if host == "127.0.0.1" || host == "localhost" || host == "::1" {
        let port = u.port_or_known_default()?;
        return Some((host, port));
    }
    None
}

fn note(key: (String, u16), up: bool) {
    let until = Instant::now() + if up { UP_FOR } else { DOWN_FOR };
    seen().lock().unwrap().insert(key, (until, up));
}

/// Is something listening on this local port? Asked at most every few seconds per port.
pub fn local_listening(host: &str, port: u16) -> bool {
    let key = (host.to_string(), port);
    if let Some((until, up)) = seen().lock().unwrap().get(&key).cloned() {
        if until > Instant::now() {
            return up;
        }
    }
    let up = port_open(host, port, Duration::from_millis(250));
    note(key, up);
    up
}

/// After starting or stopping something: look again instead of trusting what was seen.
pub fn forget_ports() {
    seen().lock().unwrap().clear();
}

pub fn port_open(host: &str, port: u16, timeout: Duration) -> bool {
    let addrs: Vec<SocketAddr> = match (host, port).to_socket_addrs() {
        Ok(a) => a.collect(),
        Err(_) => return false,
    };
    addrs.iter().any(|a| TcpStream::connect_timeout(a, timeout).is_ok())
}

pub struct Req<'a> {
    pub timeout: f64,
    pub headers: Vec<(&'a str, String)>,
    pub insecure: bool,
}

impl Default for Req<'_> {
    fn default() -> Self {
        Req { timeout: 2.0, headers: vec![], insecure: false }
    }
}

/// One request; the reply as text (`raw`) or JSON.
pub fn request_text(method: &str, url: &str, body: Option<&Value>, opt: &Req) -> Result<String, HttpError> {
    let loc = local(url);
    if let Some((h, p)) = &loc {
        if !local_listening(h, *p) {
            return Err(HttpError { status: 0, message: "not running".into() });
        }
    }
    let a = agent(Duration::from_secs_f64(opt.timeout), opt.insecure);
    let mut r = a.request(method, url).set("Accept", "application/json");
    for (k, v) in &opt.headers {
        r = r.set(k, v);
    }
    let res = match body {
        Some(b) => r.set("Content-Type", "application/json").send_string(&b.to_string()),
        None => r.call(),
    };
    match res {
        Ok(resp) => {
            let mut buf = Vec::new();
            resp.into_reader().take(4 << 20).read_to_end(&mut buf).map_err(|e| HttpError { status: 0, message: e.to_string() })?;
            Ok(String::from_utf8_lossy(&buf).into_owned())
        }
        Err(ureq::Error::Status(code, resp)) => {
            let reason = resp.status_text().to_string();
            let mut buf = Vec::new();
            let _ = resp.into_reader().take(64 << 10).read_to_end(&mut buf);
            let msg = serde_json::from_slice::<Value>(&buf)
                .ok()
                .and_then(|v| v.get("error").and_then(|e| e.as_str()).map(String::from))
                .filter(|s| !s.is_empty())
                .unwrap_or(reason);
            Err(HttpError { status: code, message: msg })
        }
        Err(ureq::Error::Transport(t)) => {
            if let Some(k) = loc {
                note(k, false);
            }
            Err(HttpError { status: 0, message: t.message().map(String::from).unwrap_or_else(|| t.kind().to_string()) })
        }
    }
}

pub fn request(method: &str, url: &str, body: Option<&Value>, opt: &Req) -> Result<Value, HttpError> {
    let text = request_text(method, url, body, opt)?;
    if text.trim().is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(&text).map_err(|_| HttpError { status: 0, message: "unexpected reply".into() })
}

pub fn get_json(url: &str, opt: &Req) -> Result<Value, HttpError> {
    request("GET", url, None, opt)
}

pub fn post_json(url: &str, body: &Value, opt: &Req) -> Result<Value, HttpError> {
    request("POST", url, Some(body), opt)
}

// ---- GitHub ------------------------------------------------------------------------------------
type TokenFn = Arc<dyn Fn() -> String + Send + Sync>;

fn login_provider() -> &'static RwLock<Option<TokenFn>> {
    static L: OnceLock<RwLock<Option<TokenFn>>> = OnceLock::new();
    L.get_or_init(|| RwLock::new(None))
}

fn token_cache() -> &'static Mutex<Option<(Instant, String, &'static str)>> {
    static T: OnceLock<Mutex<Option<(Instant, String, &'static str)>>> = OnceLock::new();
    T.get_or_init(|| Mutex::new(None))
}

pub fn set_login_provider(f: TokenFn) {
    *login_provider().write().unwrap() = Some(f);
    forget_token();
}

pub fn forget_token() {
    *token_cache().lock().unwrap() = None;
}

pub fn env_token() -> String {
    std::env::var("GITHUB_TOKEN").ok().filter(|s| !s.is_empty())
        .or_else(|| std::env::var("GH_TOKEN").ok())
        .unwrap_or_default()
}

/// The GitHub command line's sign-in, when it's installed and signed in (cached with the rest).
pub fn gh_cli_token() -> String {
    static GH: Mutex<Option<(Instant, String)>> = Mutex::new(None);
    if let Some((at, tok)) = GH.lock().unwrap().clone() {
        if at.elapsed() <= Duration::from_secs(600) {
            return tok; // signed in or not, asked at most every 10 minutes (starting `gh` is slow on Windows)
        }
    }
    let tok = if crate::runner::which("gh").is_none() {
        String::new()
    } else {
        let (code, out) = crate::runner::run_capture(&["gh".into(), "auth".into(), "token".into()], Duration::from_secs(5), true);
        if code == 0 { out.trim().to_string() } else { String::new() }
    };
    *GH.lock().unwrap() = Some((Instant::now(), tok.clone()));
    tok
}

/// The GitHub token Orbit uses: its own sign-in, else GITHUB_TOKEN/GH_TOKEN, else the `gh` command's.
/// Only ever sent to api.github.com.
pub fn github_token() -> String {
    if let Some(f) = login_provider().read().unwrap().clone() {
        let own = f();
        if !own.is_empty() {
            return own;
        }
    }
    let cached = token_cache().lock().unwrap().clone();
    if let Some((at, tok, _)) = &cached {
        if at.elapsed() <= Duration::from_secs(600) {
            return tok.clone();
        }
    }
    let (mut tok, mut src) = (env_token(), "env");
    if tok.is_empty() {
        tok = gh_cli_token();
        src = "gh";
    }
    *token_cache().lock().unwrap() = Some((Instant::now(), tok.clone(), src));
    tok
}

/// api.github.com headers, signed in when this computer is, so private repositories' releases show too.
pub fn github_headers() -> Vec<(&'static str, String)> {
    let mut h = vec![("Accept", "application/vnd.github+json".to_string())];
    let tok = github_token();
    if !tok.is_empty() {
        h.push(("Authorization", format!("Bearer {tok}")));
    }
    h
}

/// Newest GitHub release per repo, cached for 12 hours. Pre-releases count.
pub struct Releases {
    cache: Mutex<HashMap<String, (Instant, Value)>>,
    fetch: Box<dyn Fn(&str) -> Result<Value, HttpError> + Send + Sync>,
}

impl Releases {
    pub const TTL: Duration = Duration::from_secs(12 * 3600);

    pub fn new() -> Releases {
        Releases { cache: Mutex::new(HashMap::new()), fetch: Box::new(github_latest) }
    }

    pub fn latest(&self, repo: &str, force: bool) -> Value {
        if let Some((at, v)) = self.cache.lock().unwrap().get(repo) {
            if !force && at.elapsed() < Self::TTL {
                return v.clone();
            }
        }
        let mut info = match (self.fetch)(repo) {
            Ok(rel) => json!({"ok": true, "release": rel}),
            Err(e) => json!({"ok": false, "error": if e.status == 404 {
                "can't see it: if it's private, sign in with GitHub in Orbit's settings".to_string()
            } else if e.message.is_empty() { "offline".to_string() } else { e.message }}),
        };
        info["checked"] = json!(now());
        self.cache.lock().unwrap().insert(repo.to_string(), (Instant::now(), info.clone()));
        info
    }

    pub fn cached(&self, repo: &str) -> Option<Value> {
        self.cache.lock().unwrap().get(repo).map(|(_, v)| v.clone())
    }
}

fn github_latest(repo: &str) -> Result<Value, HttpError> {
    let items = get_json(
        &format!("https://api.github.com/repos/{repo}/releases?per_page=10"),
        &Req { timeout: 6.0, headers: github_headers(), insecure: false },
    )?;
    Ok(match newest_release(&items) {
        Some(r) => json!({"tag": r["tag_name"].as_str().unwrap_or(""), "url": r["html_url"].as_str().unwrap_or(""),
                          "published": r["published_at"].as_str().unwrap_or(""),
                          "prerelease": r["prerelease"].as_bool().unwrap_or(false)}),
        None => Value::Null,
    })
}

/// The highest-versioned non-draft release. (GitHub's list isn't reliably newest-first.)
pub fn newest_release(items: &Value) -> Option<Value> {
    let rels: Vec<&Value> = items.as_array()?.iter()
        .filter(|r| r.is_object() && !r["draft"].as_bool().unwrap_or(false))
        .collect();
    rels.into_iter()
        .max_by(|a, b| {
            let ka = (version_tuple(a["tag_name"].as_str().unwrap_or("")), a["published_at"].as_str().unwrap_or("").to_string());
            let kb = (version_tuple(b["tag_name"].as_str().unwrap_or("")), b["published_at"].as_str().unwrap_or("").to_string());
            ka.cmp(&kb)
        })
        .cloned()
}

/// 'v0.1.4' / '0.9.13~alpha' / '0.1.0a9' / 'v0.1.0-alpha.9' -> comparable list (empty when it isn't a version).
pub fn version_tuple(v: &str) -> Vec<i64> {
    static NUM: OnceLock<regex::Regex> = OnceLock::new();
    static PRE: OnceLock<regex::Regex> = OnceLock::new();
    let num = NUM.get_or_init(|| regex::Regex::new(r"^(\d+(?:\.\d+)*)(.*)$").unwrap());
    let pre = PRE.get_or_init(|| regex::Regex::new(r"(alpha|beta|rc|a|b)[.\-]?(\d*)").unwrap());
    let s = v.trim().trim_start_matches(['v', 'V']);
    let Some(m) = num.captures(s) else { return vec![] };
    let mut nums: Vec<i64> = m[1].split('.').map(|x| x.parse().unwrap_or(0)).collect();
    nums.resize(4, 0);
    nums.truncate(4);
    let rest = m[2].to_lowercase();
    if let Some(p) = pre.captures(&rest) {
        let rank = match &p[1] { "alpha" | "a" => 0, "beta" | "b" => 1, _ => 2 };
        nums.extend([0, rank, p[2].parse().unwrap_or(0)]);
        return nums;
    }
    if !rest.trim_matches(['~', '-', '.']).is_empty() {
        nums.extend([0, 0, 0]); // an unrecognised suffix counts as a pre-release
        return nums;
    }
    nums.extend([1, 0, 0]);
    nums
}

pub fn is_newer(latest: &str, current: &str) -> bool {
    let (a, b) = (version_tuple(latest), version_tuple(current));
    !a.is_empty() && !b.is_empty() && a > b
}

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_like_orbit_always_has() {
        assert!(is_newer("v0.3.5", "0.3.4"));
        assert!(is_newer("0.1.0", "0.1.0-alpha.9"));
        assert!(is_newer("v0.1.0-alpha.10", "0.1.0a9"));
        assert!(is_newer("0.9.13", "0.9.13~alpha"));
        assert!(!is_newer("0.3.4", "0.3.4"));
        assert!(!is_newer("nonsense", "0.1"));
        assert_eq!(version_tuple("v1.2"), vec![1, 2, 0, 0, 1, 0, 0]);
    }

    #[test]
    fn newest_release_skips_drafts_and_ignores_list_order() {
        let items = json!([{"tag_name": "v0.2.0", "draft": false}, {"tag_name": "v0.10.0", "draft": true},
                           {"tag_name": "v0.3.0", "draft": false}]);
        assert_eq!(newest_release(&items).unwrap()["tag_name"], "v0.3.0");
    }
}
