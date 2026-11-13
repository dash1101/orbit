//! The install engine: runs a recipe's steps one after another in the background and reports progress.
//!
//! Each step gets a `Session` with everything it may do (download a release file, check its SHA-256, unpack it,
//! run a command, ask for the administrator password), and every one of those goes through here, so that:
//! * dry-run is automatic: nothing is downloaded, written or run, but the steps (and their progress bars) play out
//!   the same way, with the would-be commands in the log;
//! * only release files from the app's own GitHub repository can be downloaded, and they are checked against the
//!   checksums published with the release before anything is unpacked or run;
//! * any step can be cancelled, and a failure stops the install with a plain-language reason.
use crate::host::Host;
use crate::net;
use crate::recipe::Recipe;
use crate::util::{self, bad, failed, fmt_size, q, strip_ansi, Error, Res};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const MAX_DOWNLOAD: u64 = 300 << 20;

// ---- where releases come from ------------------------------------------------------------------
/// GitHub releases. `allow` lists the URL prefixes downloads may come from (tests point both at a stand-in).
/// The token is only ever sent to the API, never to the file host a download is redirected to.
pub struct Sources {
    pub api: String,
    pub allow: Vec<String>,
    pub timeout: f64,
    token: Option<Arc<dyn Fn() -> String + Send + Sync>>,
    cache: Mutex<HashMap<String, (Instant, Vec<Value>)>>, // repo -> its recent releases, newest first
}

/// Which releases a recipe can use (Orbit's own: one with a file for this computer).
pub type Wants<'a> = &'a dyn Fn(&Value) -> bool;

impl Sources {
    pub const TTL: Duration = Duration::from_secs(600);

    pub fn new(api: &str, allow: Vec<String>, token: Option<Arc<dyn Fn() -> String + Send + Sync>>) -> Sources {
        Sources { api: api.trim_end_matches('/').into(), allow, timeout: 8.0, token, cache: Mutex::new(HashMap::new()) }
    }

    pub fn github() -> Sources {
        Sources::new("https://api.github.com", vec!["https://github.com/".into()], None)
    }

    /// GitHub, or (for Orbit's own build checks only) a stand-in named by ORBIT_TEST_GITHUB_API.
    pub fn from_env() -> Sources {
        let stand_in = std::env::var("ORBIT_TEST_GITHUB_API").unwrap_or_default().trim_end_matches('/').to_string();
        if stand_in.starts_with("http://127.0.0.1:") {
            return Sources::new(&stand_in, vec![format!("{stand_in}/")], Some(Arc::new(String::new)));
        }
        Sources::github()
    }

    pub fn token(&self) -> String {
        match &self.token {
            Some(f) => f(),
            None => net::github_token(),
        }
    }

    fn api_headers(&self, accept: &str) -> Vec<(&'static str, String)> {
        let mut h = vec![("Accept", accept.to_string()), ("X-GitHub-Api-Version", "2022-11-28".to_string())];
        let tok = self.token();
        if !tok.is_empty() {
            h.push(("Authorization", format!("Bearer {tok}")));
        }
        h
    }

    /// Newest non-draft release (pre-releases count): {"tag", "published", "url", "prerelease",
    /// "assets": {name: {"url", "api", "size"}}}.
    pub fn latest(&self, repo: &str, force: bool) -> Res<Value> {
        self.latest_with(repo, force, &|_| true)
    }

    /// The newest release `wants` accepts, among the recent ones (a release can skip a system: Orbit builds every
    /// system only at milestones).
    pub fn latest_with(&self, repo: &str, force: bool, wants: Wants) -> Res<Value> {
        let list = self.releases(repo, force)?;
        list.into_iter().find(|r| wants(r)).ok_or_else(|| failed(format!("{repo} has no release for this computer yet."), ""))
    }

    pub fn cached(&self, repo: &str) -> Option<Value> {
        self.cached_with(repo, &|_| true)
    }

    pub fn cached_with(&self, repo: &str, wants: Wants) -> Option<Value> {
        self.cache.lock().unwrap().get(repo).and_then(|(_, l)| l.iter().find(|r| wants(r)).cloned())
    }

    /// The recent releases, newest version first.
    fn releases(&self, repo: &str, force: bool) -> Res<Vec<Value>> {
        if let Some((at, v)) = self.cache.lock().unwrap().get(repo) {
            if !force && at.elapsed() < Self::TTL {
                return Ok(v.clone());
            }
        }
        let items = net::get_json(&format!("{}/repos/{repo}/releases?per_page=30", self.api),
                                  &net::Req { timeout: self.timeout, headers: self.api_headers("application/vnd.github+json"), insecure: false })
            .map_err(|e| match e.status {
                404 => failed(format!("Couldn't find {repo} on GitHub."),
                              "If it's a private repository, sign in with GitHub in Orbit's settings (or run gh auth login), then try again."),
                401 | 403 if e.limit_resets_in.is_some() => failed("GitHub's limit for requests is used up for now.",
                    format!("Without a sign-in GitHub answers 60 requests an hour per network. Try again in about {} min, or sign \
                             in with GitHub in Orbit's settings.", e.limit_resets_in.unwrap_or(1))),
                401 | 403 => failed("GitHub turned the request down for now.",
                                    "Without a sign-in GitHub answers about 60 requests an hour per network. Wait a few minutes and try \
                                     again. If you signed in, your sign-in may have expired: sign in again in Orbit's settings."),
                _ => failed("Couldn't reach GitHub to find the latest release.", "Check your internet connection and try again."),
            })?;
        let mut rels: Vec<Value> = items.as_array().cloned().unwrap_or_default().into_iter()
            .filter(|r| r.is_object() && !r["draft"].as_bool().unwrap_or(false)).collect();
        if rels.is_empty() {
            return Err(failed(format!("{repo} has no releases to install yet."), ""));
        }
        let key = |r: &Value| (net::version_tuple(r["tag_name"].as_str().unwrap_or("")), r["published_at"].as_str().unwrap_or("").to_string());
        rels.sort_by(|a, b| key(b).cmp(&key(a))); // GitHub's list isn't reliably newest-first
        let list: Vec<Value> = rels.iter().map(|rel| {
            let mut assets = Map::new();
            for a in rel["assets"].as_array().cloned().unwrap_or_default() {
                if let Some(name) = a["name"].as_str().filter(|n| !n.is_empty()) {
                    assets.insert(name.into(), json!({"url": a["browser_download_url"].as_str().unwrap_or(""),
                                                      "api": a["url"].as_str().unwrap_or(""),
                                                      "size": a["size"].as_u64().unwrap_or(0)}));
                }
            }
            json!({"tag": rel["tag_name"].as_str().unwrap_or(""), "published": rel["published_at"].as_str().unwrap_or(""),
                   "url": rel["html_url"].as_str().unwrap_or(""), "prerelease": rel["prerelease"].as_bool().unwrap_or(false),
                   "assets": assets})
        }).collect();
        self.cache.lock().unwrap().insert(repo.into(), (Instant::now(), list.clone()));
        Ok(list)
    }

    pub fn allowed(&self, url: &str, repo: &str) -> bool {
        if self.allow == ["https://github.com/"] {
            return url.starts_with(&format!("https://github.com/{repo}/releases/download/"));
        }
        self.allow.iter().any(|a| url.starts_with(a))
    }

    /// Open a release file: through the API (with the token) when there is one, else the public link.
    /// GitHub's redirect to its file host is followed without the token. Returns (reader, length if known).
    pub fn open_asset(&self, asset: &Value, repo: &str) -> Result<(Box<dyn Read + Send>, Option<u64>), String> {
        let api = asset["api"].as_str().unwrap_or("");
        let plain = net::agent(Duration::from_secs(30), false);
        let resp = if !self.token().is_empty() && api.starts_with(&format!("{}/repos/{repo}/releases/assets/", self.api)) {
            let no_redirect = ureq::AgentBuilder::new().timeout(Duration::from_secs(30)).user_agent(net::UA).redirects(0).build();
            let mut r = no_redirect.get(api);
            for (k, v) in self.api_headers("application/octet-stream") {
                r = r.set(k, &v);
            }
            match r.call() {
                Ok(resp) if (300..400).contains(&resp.status()) => {
                    let to = resp.header("Location").unwrap_or("").to_string();
                    plain.get(&to).call()
                }
                other => other,
            }
        } else {
            plain.get(asset["url"].as_str().unwrap_or("")).call()
        };
        match resp {
            Ok(r) => {
                let len = r.header("Content-Length").and_then(|v| v.parse().ok());
                Ok((Box::new(r.into_reader()), len))
            }
            Err(ureq::Error::Status(code, r)) => Err(format!("{code} {}", r.status_text())),
            Err(e) => Err(e.to_string()),
        }
    }
}

// ---- jobs ------------------------------------------------------------------------------------------
pub struct JobInner {
    pub steps: Vec<Map<String, Value>>,
    pub state: String,
    pub error: String,
    pub hint: String,
    pub result: Value,
    pub ended: Option<f64>,
    log: Vec<String>,
    log_base: usize,
    cur: i64,
}

pub struct Job {
    pub id: String,
    pub app_id: String,
    pub app_name: String,
    pub op: String,
    pub title: String,
    pub dry_run: bool,
    pub started: f64,
    pub cancel: AtomicBool,
    pub inner: Mutex<JobInner>,
}

const LOG_MAX: usize = 3000;

impl Job {
    fn new(app_id: &str, app_name: &str, op: &str, title: &str, steps: &[crate::recipe::StepDef], dry_run: bool) -> Job {
        let steps = steps.iter().map(|s| {
            let v = json!({"id": s.id, "label": s.label, "state": "pending", "detail": "", "progress": null, "weight": s.weight});
            v.as_object().unwrap().clone()
        }).collect();
        Job {
            id: util::new_id(), app_id: app_id.into(), app_name: app_name.into(), op: op.into(), title: title.into(),
            dry_run, started: net::now(), cancel: AtomicBool::new(false),
            inner: Mutex::new(JobInner { steps, state: "running".into(), error: String::new(), hint: String::new(),
                                         result: json!({}), ended: None, log: vec![], log_base: 0, cur: -1 }),
        }
    }

    pub fn state(&self) -> String {
        self.inner.lock().unwrap().state.clone()
    }

    pub fn result(&self) -> Value {
        self.inner.lock().unwrap().result.clone()
    }

    pub fn add_log(&self, text: &str) {
        let mut j = self.inner.lock().unwrap();
        let t = util::clock();
        if text.is_empty() {
            j.log.push(format!("{t}  "));
        }
        for line in text.lines() {
            j.log.push(format!("{t}  {line}"));
        }
        if j.log.len() > LOG_MAX {
            let cut = j.log.len() - LOG_MAX;
            j.log.drain(..cut);
            j.log_base += cut;
        }
    }

    pub fn set_step(&self, kv: &[(&str, Value)]) {
        let mut j = self.inner.lock().unwrap();
        let cur = j.cur;
        if cur >= 0 && (cur as usize) < j.steps.len() {
            let s = &mut j.steps[cur as usize];
            for (k, v) in kv {
                s.insert(k.to_string(), v.clone());
            }
        }
    }

    fn begin(&self, i: usize) {
        let mut j = self.inner.lock().unwrap();
        j.cur = i as i64;
        j.steps[i].insert("state".into(), json!("active"));
        j.steps[i].insert("started".into(), json!(net::now()));
    }

    fn end_step(&self, i: usize, state: &str) {
        let mut j = self.inner.lock().unwrap();
        let s = &mut j.steps[i];
        s.insert("state".into(), json!(state));
        s.insert("ended".into(), json!(net::now()));
        if state == "done" {
            s.insert("progress".into(), json!(1.0));
        }
    }

    fn overall(steps: &[Map<String, Value>]) -> f64 {
        let w = |s: &Map<String, Value>| s["weight"].as_f64().unwrap_or(1.0);
        let total: f64 = steps.iter().map(w).sum::<f64>().max(f64::MIN_POSITIVE);
        let mut got = 0.0;
        for s in steps {
            match s["state"].as_str().unwrap_or("") {
                "done" | "skipped" => got += w(s),
                "active" => got += w(s) * s.get("progress").and_then(|p| p.as_f64()).unwrap_or(0.0),
                _ => {}
            }
        }
        got / total
    }

    pub fn snapshot(&self, log_from: usize) -> Value {
        let j = self.inner.lock().unwrap();
        let start = log_from.saturating_sub(j.log_base).min(j.log.len());
        json!({"id": self.id, "app": self.app_id, "appName": self.app_name, "op": self.op, "title": self.title,
               "state": j.state, "dryRun": self.dry_run, "steps": j.steps, "current": j.cur,
               "progress": (Self::overall(&j.steps) * 10000.0).round() / 10000.0,
               "error": j.error, "hint": j.hint, "result": j.result, "started": self.started, "ended": j.ended,
               "log": j.log[start..], "logNext": j.log_base + j.log.len()})
    }
}

// ---- what a step can do ------------------------------------------------------------------------------
pub type Spawn = Arc<dyn Fn(&[String]) -> bool + Send + Sync>;

pub struct Session {
    pub job: Arc<Job>,
    pub host: Host,
    pub sources: Arc<Sources>,
    pub dry_run: bool,
    pub pace: f64,
    pub vars: Map<String, Value>,
    pub sha: HashMap<PathBuf, String>,
    tmp: Option<PathBuf>,
    spawn: Spawn,
}

impl Session {
    pub fn log(&self, text: &str) {
        self.job.add_log(text);
    }

    pub fn detail(&self, text: &str) {
        self.job.set_step(&[("detail", json!(text))]);
    }

    pub fn progress(&self, frac: Option<f64>) {
        self.job.set_step(&[("progress", frac.map(|f| json!(f.clamp(0.0, 1.0))).unwrap_or(Value::Null))]);
    }

    pub fn check(&self) -> Res<()> {
        if self.job.cancel.load(Ordering::Relaxed) { Err(Error::Cancelled) } else { Ok(()) }
    }

    pub fn sleep(&self, seconds: f64) -> Res<()> {
        let end = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
        while Instant::now() < end {
            self.check()?;
            std::thread::sleep((end - Instant::now()).min(Duration::from_millis(100)));
        }
        Ok(())
    }

    /// A dry run's stand-in for real work, so its steps (and their animation) can be followed.
    pub fn pause(&self, seconds: f64) -> Res<()> {
        self.sleep(seconds * self.pace)
    }

    /// The temporary folder (removed when the job ends).
    pub fn tmp(&mut self) -> PathBuf {
        if self.tmp.is_none() {
            let d = std::env::temp_dir().join(format!("orbit-install-{}", util::new_id()));
            let _ = std::fs::create_dir_all(&d);
            #[cfg(unix)]
            {
                // world-readable so tools that drop privileges (apt's sandbox user) can read a package from it
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o755));
            }
            self.tmp = Some(d);
        }
        self.tmp.clone().unwrap()
    }

    fn cleanup(&mut self) {
        if let Some(d) = self.tmp.take() {
            let _ = std::fs::remove_dir_all(d);
        }
    }

    pub fn var_str(&self, key: &str) -> String {
        self.vars.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string()
    }

    pub fn opt_bool(&self, key: &str, default: bool) -> bool {
        self.vars.get("opts").and_then(|o| o.get(key)).and_then(|v| v.as_bool()).unwrap_or(default)
    }

    // ---- releases
    pub fn release(&self, repo: &str) -> Res<Value> {
        self.release_with(repo, &|_| true)
    }

    /// The newest release the recipe can use (see Sources::latest_with).
    pub fn release_with(&self, repo: &str, wants: Wants) -> Res<Value> {
        self.detail("asking GitHub for the latest release");
        let rel = self.sources.latest_with(repo, true, wants)?;
        self.log(&format!("Latest release of {repo}: {}", rel["tag"].as_str().unwrap_or("")));
        Ok(rel)
    }

    pub fn asset(&self, rel: &Value, name: &str, repo: &str) -> Res<Value> {
        let a = rel["assets"].get(name).cloned();
        let Some(a) = a else {
            return Err(failed(format!("The {} release doesn't include {name}.", rel["tag"].as_str().unwrap_or("")),
                              "It may still be uploading; try again in a few minutes."));
        };
        if !self.sources.allowed(a["url"].as_str().unwrap_or(""), repo) {
            return Err(failed(format!("{name} isn't hosted where Orbit expects, so it won't be downloaded."), ""));
        }
        Ok(a)
    }

    /// Download one release file into the temporary folder, with progress. Returns its path.
    pub fn download(&mut self, rel: &Value, name: &str, repo: &str, dest_name: Option<&str>) -> Res<PathBuf> {
        let a = self.asset(rel, name, repo)?;
        let dest = self.tmp().join(dest_name.unwrap_or(name));
        let size = a["size"].as_u64().unwrap_or(0);
        if size > MAX_DOWNLOAD {
            return Err(failed(format!("{name} is unexpectedly large ({} MB); not downloading it.", size >> 20), ""));
        }
        self.detail(&if size > 0 { format!("{name} · {}", fmt_size(size)) } else { name.to_string() });
        self.log(&format!("Downloading {}", a["url"].as_str().unwrap_or("")));
        if self.dry_run {
            let steps = 12;
            for i in 0..=steps {
                self.pause(0.07)?;
                self.progress(Some(i as f64 / steps as f64));
                self.job.set_step(&[("bytes", json!(size * i / steps)), ("total", json!(size))]);
            }
            self.log(&format!("dry run: would save {name} ({})", fmt_size(size)));
            return Ok(dest);
        }
        let stopped = |e: String| failed(format!("The download of {name} stopped: {e}"), "Check your internet connection and try again.");
        let (mut r, len) = self.sources.open_asset(&a, repo).map_err(stopped)?;
        let total = len.unwrap_or(size);
        let mut f = std::fs::File::create(&dest).map_err(|e| stopped(e.to_string()))?;
        let mut h = Sha256::new();
        let mut got: u64 = 0;
        let mut buf = vec![0u8; 64 << 10];
        loop {
            self.check()?;
            let n = r.read(&mut buf).map_err(|e| stopped(e.to_string()))?;
            if n == 0 {
                break;
            }
            got += n as u64;
            if got > MAX_DOWNLOAD {
                return Err(failed(format!("{name} is larger than expected; stopped downloading it."), ""));
            }
            f.write_all(&buf[..n]).map_err(|e| stopped(e.to_string()))?;
            h.update(&buf[..n]);
            let p = if total > 0 { json!(got as f64 / total as f64) } else { Value::Null };
            self.job.set_step(&[("bytes", json!(got)), ("total", json!(total)), ("progress", p)]);
        }
        self.sha.insert(dest.clone(), hex::encode(h.finalize()));
        self.log(&format!("Saved {name} ({})", fmt_size(got)));
        Ok(dest)
    }

    /// A small release file (checksums, signature) as text.
    pub fn fetch_text(&self, rel: &Value, name: &str, repo: &str) -> Res<String> {
        let a = self.asset(rel, name, repo)?;
        if self.dry_run {
            self.log(&format!("dry run: would read {name}"));
            return Ok(String::new());
        }
        let fail = |e: String| failed(format!("Couldn't download {name}: {e}"), "Check your internet connection and try again.");
        let (r, _) = self.sources.open_asset(&a, repo).map_err(fail)?;
        let mut buf = Vec::new();
        r.take(1 << 20).read_to_end(&mut buf).map_err(|e| fail(e.to_string()))?;
        Ok(String::from_utf8_lossy(&buf).into_owned())
    }

    /// Check a downloaded file against a `sha256  name` list (sha256sum format).
    pub fn verify(&self, path: &Path, sums_text: &str, name: &str) -> Res<()> {
        self.detail(&format!("SHA-256 of {name}"));
        if self.dry_run {
            self.pause(0.4)?;
            self.log(&format!("dry run: would check {name} against the release's checksums"));
            return Ok(());
        }
        let sums = util::parse_sums(sums_text);
        let Some(expected) = sums.get(name) else {
            return Err(failed(format!("The release doesn't list a checksum for {name}, so it won't be installed."), ""));
        };
        let actual = match self.sha.get(path) {
            Some(s) => s.clone(),
            None => util::sha256_file(path).map_err(|e| failed(format!("Couldn't read {name}: {e}"), ""))?,
        };
        if &actual != expected {
            return Err(failed(format!("{name} doesn't match its published checksum, so it won't be installed."),
                              "The download may be damaged. Try again; if it keeps happening, tell the app's author."));
        }
        self.log(&format!("Checksum OK: {name} {}…", &actual[..16]));
        Ok(())
    }

    /// Unpack a .tar.gz or .zip into the temporary folder (refusing any path that would land outside it).
    pub fn extract(&mut self, archive: &Path) -> Res<PathBuf> {
        let fname = archive.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let dest = self.tmp().join(format!("{fname}.d"));
        self.detail(&fname);
        if self.dry_run {
            self.pause(0.3)?;
            self.log(&format!("dry run: would unpack {fname}"));
            return Ok(dest);
        }
        std::fs::create_dir_all(&dest).map_err(|e| failed(format!("Couldn't unpack {fname}: {e}"), ""))?;
        safe_extract(archive, &dest).map_err(|e| failed(format!("Couldn't unpack {fname}: {e}"), ""))?;
        Ok(dest)
    }

    /// The first file called `filename` anywhere under `folder`.
    pub fn find(&self, folder: &Path, filename: &str) -> Res<PathBuf> {
        if self.dry_run {
            return Ok(folder.join(filename));
        }
        fn walk(d: &Path, name: &str) -> Option<PathBuf> {
            let mut dirs = vec![];
            for e in std::fs::read_dir(d).ok()?.flatten() {
                let p = e.path();
                if p.is_dir() {
                    dirs.push(p);
                } else if e.file_name().to_string_lossy() == name {
                    return Some(p);
                }
            }
            dirs.into_iter().find_map(|x| walk(&x, name))
        }
        walk(folder, filename).ok_or_else(|| failed(format!("The download doesn't contain {filename}."), ""))
    }

    // ---- changing things
    /// Run a command, streaming its output into the log. Returns (exit code, output).
    pub fn run(&self, argv: &[String], ok: &[i32], quiet_log: bool, fail: Option<&str>, hint: &str) -> Res<(i32, String)> {
        let shown = argv.iter().map(|a| q(a)).collect::<Vec<_>>().join(" ");
        if self.dry_run {
            self.log(&format!("dry run: would run {shown}"));
            self.pause(0.25)?;
            return Ok((0, String::new()));
        }
        if !quiet_log {
            self.log(&format!("$ {shown}"));
        }
        let name = Path::new(&argv[0]).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let mut cmd = crate::runner::quiet(&argv[0]);
        cmd.args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .env_clear().envs(&self.host.env);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0); // no terminal: installers that would ask a question don't hang
        }
        let mut child = cmd.spawn().map_err(|e| failed(fail.map(String::from).unwrap_or(format!("Couldn't start {name}: {e}")), hint))?;
        let out = Arc::new(Mutex::new(String::new()));
        let mut readers = vec![];
        for stream in [child.stdout.take().map(|s| Box::new(s) as Box<dyn Read + Send>),
                       child.stderr.take().map(|s| Box::new(s) as Box<dyn Read + Send>)].into_iter().flatten() {
            let (out, job) = (out.clone(), self.job.clone());
            readers.push(std::thread::spawn(move || {
                for line in BufReader::new(stream).split(b'\n').map_while(Result::ok) {
                    let text = String::from_utf8_lossy(&line).into_owned();
                    out.lock().unwrap().push_str(&format!("{text}\n"));
                    if !quiet_log {
                        job.add_log(&strip_ansi(text.trim_end_matches('\r')));
                    }
                }
            }));
        }
        let start = Instant::now();
        let code = loop {
            if let Some(st) = child.try_wait().map_err(|e| failed(e.to_string(), ""))? {
                break st.code().unwrap_or(-1);
            }
            if self.job.cancel.load(Ordering::Relaxed) {
                let _ = child.kill();
                let _ = child.wait();
                return Err(Error::Cancelled);
            }
            if start.elapsed() > Duration::from_secs(900) {
                let _ = child.kill();
                return Err(failed(format!("{name} took too long (over 15 minutes) and was stopped."), ""));
            }
            std::thread::sleep(Duration::from_millis(100));
        };
        for r in readers {
            let _ = r.join();
        }
        let text = out.lock().unwrap().clone();
        if !ok.contains(&code) {
            let last = text.trim().lines().filter(|l| !l.trim().is_empty()).last().map(|l| l.trim().chars().take(200).collect::<String>());
            let msg = fail.map(String::from).unwrap_or_else(|| {
                format!("{name} stopped with an error (code {code})") + &last.map(|l| format!(": {l}")).unwrap_or_else(|| ".".into())
            });
            return Err(failed(msg, hint));
        }
        Ok((code, text))
    }

    /// Run a command as the administrator (Linux): the desktop's own password window via pkexec. Without
    /// pkexec, the command is opened in a terminal with sudo and the step waits until `wait_for()` says it worked.
    pub fn admin(&self, argv: &[String], what: &str, wait_for: Option<&dyn Fn() -> bool>, hint: &str) -> Res<(i32, String)> {
        if self.host.which("pkexec").is_some() && self.host.has_display() {
            self.detail("waiting for your password in the system's window");
            self.log(&format!("Asking for permission to {what}"));
            let mut full = vec![self.host.tool("pkexec")];
            full.extend_from_slice(argv);
            return self.run(&full, &[0], false, None, hint).map_err(|e| match e {
                Error::Failed(m, _) if m.contains("code 126") || m.contains("code 127") =>
                    failed("The password window was closed, so nothing was changed.", "Try again and enter your password when the system asks."),
                other => other,
            });
        }
        // allowed without a password here (sudo set up that way): no window or terminal needed
        if self.host.which("sudo").is_some()
            && crate::runner::run_capture(&["sudo".into(), "-n".into(), "true".into()], Duration::from_secs(5), true).0 == 0 {
            self.log(&format!("Running as the administrator (sudo) to {what}"));
            let mut full = vec!["sudo".to_string(), "-n".to_string()];
            full.extend_from_slice(argv);
            return self.run(&full, &[0], false, None, hint);
        }
        let mut sudo = vec!["sudo".to_string()];
        sudo.extend_from_slice(argv);
        let shown = argv.iter().map(|a| q(a)).collect::<Vec<_>>().join(" ");
        let term = crate::runner::terminal_argv(&sudo, "Orbit");
        let (Some(term), Some(wait_for)) = (term, wait_for) else {
            return Err(failed(format!("Orbit needs administrator rights to {what}, and can't ask for them here."),
                              format!("Run this in a terminal:  sudo {shown}")));
        };
        self.detail("finish in the terminal window (it asks for your password)");
        self.log(&format!("Opened a terminal to {what}: sudo {shown}"));
        if self.dry_run {
            self.pause(0.4)?;
            return Ok((0, String::new()));
        }
        if !(self.spawn)(&term) {
            return Err(failed("Couldn't open a terminal window.", ""));
        }
        let deadline = Instant::now() + Duration::from_secs(30 * 60);
        while Instant::now() < deadline {
            self.sleep(2.0)?;
            if wait_for() {
                return Ok((0, String::new()));
            }
        }
        Err(failed("Gave up waiting for the terminal window.", "Run it again when you're ready."))
    }

    /// Run a PowerShell script as the administrator (Windows): Windows asks for permission once (the UAC prompt),
    /// the script runs without a window of its own, and what it prints lands in this job's log. The elevated
    /// PowerShell writes to a file in the temporary folder that is read as it grows; its exit code comes back
    /// through Start-Process.
    pub fn admin_windows(&mut self, script: &Path, args: &[String], what: &str, hint: &str) -> Res<(i32, String)> {
        let shown = std::iter::once(q(&script.to_string_lossy())).chain(args.iter().cloned()).collect::<Vec<_>>().join(" ");
        self.detail("waiting for you to allow it in Windows' prompt");
        self.log(&format!("Asking Windows for permission to {what}: {shown}"));
        if self.dry_run {
            self.pause(0.6)?;
            self.log(&format!("dry run: would run, as the administrator, powershell -File {shown}"));
            return Ok((0, String::new()));
        }
        let tmp = self.tmp();
        let log = tmp.join("admin-output.log");
        let run = tmp.join("admin-run.ps1");
        let lit = |s: &str| format!("'{}'", s.replace('\'', "''"));
        // UTF-8 with a byte-order mark: Windows PowerShell 5 misreads UTF-8 without one (user names can have accents).
        let body = format!(
            "$ErrorActionPreference = 'Continue'\r\n& powershell.exe -NoProfile -ExecutionPolicy Bypass -File {} {} *>&1 | Out-File -FilePath {} -Encoding utf8\r\nexit $LASTEXITCODE\r\n",
            lit(&script.to_string_lossy()), args.iter().map(|a| lit(a)).collect::<Vec<_>>().join(" "), lit(&log.to_string_lossy()));
        let mut bytes = vec![0xEF, 0xBB, 0xBF];
        bytes.extend(body.as_bytes());
        std::fs::write(&run, bytes).map_err(|e| failed(format!("Couldn't prepare the installer: {e}"), ""))?;
        let outer = format!(
            "$ErrorActionPreference = 'Stop'; try {{ $p = Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru \
             -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '\"{}\"'); exit $p.ExitCode }} catch {{ Write-Output $_.Exception.Message; \
             $e = $_.Exception; while ($e) {{ if ($e.NativeErrorCode -eq 1223) {{ exit 1223 }}; $e = $e.InnerException }}; exit 1 }}",
            run.to_string_lossy().replace('\'', "''"));
        let ps = self.host.which("powershell").unwrap_or_else(|| "powershell.exe".into());
        let mut child = crate::runner::quiet(&ps)
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &outer])
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .spawn().map_err(|e| failed(format!("Couldn't start PowerShell: {e}"), hint))?;
        let mut stdout = child.stdout.take().unwrap();
        let said_reader = std::thread::spawn(move || {
            let mut s = Vec::new();
            let _ = stdout.read_to_end(&mut s);
            String::from_utf8_lossy(&s).into_owned()
        });
        let mut pos: u64 = 0;
        let mut pending: Vec<u8> = vec![];
        let mut drain = |sess: &Session| {
            use std::io::Seek;
            let Ok(mut f) = std::fs::File::open(&log) else { return };
            if f.seek(std::io::SeekFrom::Start(pos)).is_err() {
                return;
            }
            let mut data = vec![];
            let _ = f.read_to_end(&mut data);
            pos += data.len() as u64;
            pending.extend(data);
            while let Some(i) = pending.iter().position(|&b| b == b'\n') {
                let line: Vec<u8> = pending.drain(..=i).collect();
                let text = strip_ansi(String::from_utf8_lossy(&line).trim_start_matches('\u{feff}').trim_end_matches(['\r', '\n']));
                if !text.trim().is_empty() {
                    sess.log(&text);
                    sess.detail(&text.trim().chars().take(90).collect::<String>());
                }
            }
        };
        let code = loop {
            if let Ok(Some(st)) = child.try_wait() {
                break st.code().unwrap_or(-1);
            }
            if self.job.cancel.load(Ordering::Relaxed) {
                // Only the waiting PowerShell can be stopped from here; the elevated script finishes by itself.
                let _ = child.kill();
                return Err(Error::Cancelled);
            }
            drain(self);
            std::thread::sleep(Duration::from_millis(300));
        };
        drain(self);
        let said = said_reader.join().unwrap_or_default().trim().to_string();
        let low = said.to_lowercase();
        if code == 1223 || low.contains("canceled by the user") || low.contains("cancelled by the user") {
            return Err(failed("Windows' permission prompt was declined, so nothing was changed.", "Try again and choose Yes when Windows asks."));
        }
        if code != 0 {
            if !said.is_empty() {
                self.log(&said);
            }
            return Err(failed(format!("Couldn't {what} (code {code})."),
                              if hint.is_empty() { "The log below shows what it said." } else { hint }));
        }
        Ok((0, String::new()))
    }

    pub fn write_file(&self, path: &Path, text: &str, mode: Option<u32>) -> Res<()> {
        if self.dry_run {
            self.log(&format!("dry run: would write {}", path.display()));
            return Ok(());
        }
        let err = |e: std::io::Error| failed(format!("Couldn't write {}: {e}", path.display()), "");
        if let Some(d) = path.parent() {
            std::fs::create_dir_all(d).map_err(err)?;
        }
        let tmp = path.with_file_name(format!("{}.orbit-tmp", path.file_name().unwrap_or_default().to_string_lossy()));
        std::fs::write(&tmp, text).map_err(err)?;
        #[cfg(unix)]
        if let Some(m) = mode {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(m)).map_err(err)?;
        }
        #[cfg(not(unix))]
        let _ = mode;
        std::fs::rename(&tmp, path).map_err(err)?;
        self.log(&format!("Wrote {}", path.display()));
        Ok(())
    }

    pub fn remove(&self, paths: &[PathBuf]) -> Res<()> {
        for p in paths {
            if !util::exists(p) {
                continue;
            }
            if self.dry_run {
                self.log(&format!("dry run: would remove {}", p.display()));
                continue;
            }
            util::remove_any(p).map_err(|e| failed(format!("Couldn't remove {}: {e}", p.display()), ""))?;
            self.log(&format!("Removed {}", p.display()));
        }
        Ok(())
    }

    pub fn copy_tree(&self, src: &Path, dest: &Path) -> Res<()> {
        if self.dry_run {
            self.log(&format!("dry run: would copy the program into {}", dest.display()));
            return Ok(());
        }
        fn copy(src: &Path, dest: &Path) -> std::io::Result<()> {
            std::fs::create_dir_all(dest)?;
            for e in std::fs::read_dir(src)?.flatten() {
                let (from, to) = (e.path(), dest.join(e.file_name()));
                if from.is_dir() { copy(&from, &to)? } else { std::fs::copy(&from, &to).map(|_| ())? }
            }
            Ok(())
        }
        copy(src, dest).map_err(|e| failed(format!("Couldn't copy the program into {}: {e}", dest.display()), ""))?;
        self.log(&format!("Copied the program into {}", dest.display()));
        Ok(())
    }

    pub fn symlink(&self, target: &Path, link: &Path) -> Res<()> {
        if self.dry_run {
            self.log(&format!("dry run: would link {} → {}", link.display(), target.display()));
            return Ok(());
        }
        let err = |e: std::io::Error| failed(format!("Couldn't link {}: {e}", link.display()), "");
        if let Some(d) = link.parent() {
            std::fs::create_dir_all(d).map_err(err)?;
        }
        if util::exists(link) {
            std::fs::remove_file(link).map_err(err)?;
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, link).map_err(err)?;
        #[cfg(not(unix))]
        std::fs::copy(target, link).map(|_| ()).map_err(err)?;
        Ok(())
    }

    /// Wait until something answers at `url`. Returns true when it did.
    pub fn wait_http(&self, url: &str, seconds: f64, insecure: bool) -> Res<bool> {
        self.detail(&format!("waiting for it to answer · {}", url.split('/').nth(2).unwrap_or(url)));
        if self.dry_run {
            self.pause(0.5)?;
            return Ok(true);
        }
        let end = Instant::now() + Duration::from_secs_f64(seconds);
        while Instant::now() < end {
            self.check()?;
            match net::request_text("GET", url, None, &net::Req { timeout: 1.5, headers: vec![], insecure }) {
                Ok(_) => return Ok(true),
                Err(e) if e.status != 0 => return Ok(true), // it answered, even if with an error page
                Err(_) => {}
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        Ok(false)
    }
}

// ---- the installer ------------------------------------------------------------------------------------
pub type OnChange = Arc<dyn Fn(&str) + Send + Sync>;

/// Starts install jobs (one at a time per app) and keeps the recent ones for the window to follow.
pub struct Installer {
    pub host: Host,
    pub sources: Arc<Sources>,
    pub recipes: Vec<Arc<dyn Recipe>>,
    pub dry_run: bool,
    pub pace: f64,
    spawn: Spawn,
    on_change: Option<OnChange>,
    jobs: Mutex<HashMap<String, Arc<Job>>>,
}

impl Installer {
    pub fn new(host: Host, sources: Arc<Sources>, recipes: Vec<Arc<dyn Recipe>>, dry_run: bool, spawn: Spawn,
               on_change: Option<OnChange>) -> Installer {
        Installer { host, sources, recipes, dry_run, pace: 1.0, spawn, on_change, jobs: Mutex::new(HashMap::new()) }
    }

    pub fn recipe(&self, app_id: &str) -> Res<Arc<dyn Recipe>> {
        self.recipes.iter().find(|r| r.id() == app_id).cloned().ok_or_else(|| Error::NotFound(app_id.into()))
    }

    pub fn get(&self, app_id: &str) -> Option<Arc<dyn Recipe>> {
        self.recipes.iter().find(|r| r.id() == app_id).cloned()
    }

    pub fn busy(&self, app_id: &str) -> Option<String> {
        self.jobs.lock().unwrap().values().find(|j| j.app_id == app_id && j.state() == "running").map(|j| j.id.clone())
    }

    pub fn start(&self, app_id: &str, op: &str, options: &Value) -> Res<Arc<Job>> {
        let r = self.recipe(app_id)?;
        if self.busy(app_id).is_some() {
            return Err(failed(format!("{} is already being changed; wait for that to finish.", r.name()), ""));
        }
        let plan = crate::recipe::plan(&r, op, options)?; // validates op + options, fails when blocked
        let job = Arc::new(Job::new(app_id, r.name(), op, &plan.title, &plan.steps, self.dry_run));
        {
            let mut jobs = self.jobs.lock().unwrap();
            jobs.insert(job.id.clone(), job.clone());
            let mut done: Vec<_> = jobs.values().filter(|j| j.state() != "running").map(|j| (j.started, j.id.clone())).collect();
            done.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
            while jobs.len() > 12 && !done.is_empty() {
                jobs.remove(&done.remove(0).1);
            }
        }
        let mut sess = Session { job: job.clone(), host: self.host.clone(), sources: self.sources.clone(), dry_run: self.dry_run,
                                 pace: self.pace, vars: plan.vars.clone(), sha: HashMap::new(), tmp: None, spawn: self.spawn.clone() };
        let (j, on_change, label, dry) = (job.clone(), self.on_change.clone(), self.host.label(), self.dry_run);
        std::thread::Builder::new().name(format!("orbit-install-{app_id}")).spawn(move || {
            j.add_log(&format!("{} on {label}{}", j.title, if dry { "  (dry run: nothing will be changed)" } else { "" }));
            let outcome = (|| -> Res<Value> {
                for (i, step) in plan.steps.iter().enumerate() {
                    sess.check()?;
                    j.begin(i);
                    match (step.f)(&mut sess) {
                        Ok(done) => j.end_step(i, if done { "done" } else { "skipped" }),
                        Err(e) => {
                            j.end_step(i, "failed");
                            return Err(e);
                        }
                    }
                }
                Ok((plan.finish)(&sess))
            })();
            {
                let mut inner = j.inner.lock().unwrap();
                match &outcome {
                    Ok(result) => {
                        inner.result = result.clone();
                        inner.state = "done".into();
                    }
                    Err(Error::Cancelled) => {
                        inner.state = "cancelled".into();
                        inner.error = "Stopped. Anything already changed stays as it is; run the installer again to finish.".into();
                    }
                    Err(Error::Failed(m, h)) => {
                        inner.state = "failed".into();
                        inner.error = m.clone();
                        inner.hint = h.clone();
                    }
                    Err(Error::Bad(m)) | Err(Error::NotFound(m)) => {
                        inner.state = "failed".into();
                        inner.error = format!("Something unexpected went wrong: {m}");
                    }
                }
            }
            match &outcome {
                Ok(_) => j.add_log("Finished."),
                Err(Error::Cancelled) => j.add_log("Cancelled."),
                Err(e) => j.add_log(&format!("Failed: {e}")),
            }
            sess.cleanup();
            j.inner.lock().unwrap().ended = Some(net::now());
            plan.recipe.forget();
            if let Some(f) = on_change {
                f(&j.app_id);
            }
        }).map_err(|e| failed(format!("Couldn't start the installer: {e}"), ""))?;
        Ok(job)
    }

    pub fn job(&self, jid: &str) -> Option<Arc<Job>> {
        self.jobs.lock().unwrap().get(jid).cloned()
    }

    pub fn cancel(&self, jid: &str) -> Res<Arc<Job>> {
        let j = self.job(jid).ok_or_else(|| Error::NotFound(jid.into()))?;
        if j.state() == "running" {
            j.cancel.store(true, Ordering::Relaxed);
        }
        Ok(j)
    }
}

// ---- unpacking ----------------------------------------------------------------------------------------
fn inside(base: &Path, name: &str) -> Result<PathBuf, String> {
    let n = name.replace('\\', "/");
    let parts: Vec<&str> = n.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    if n.starts_with('/') || parts.iter().any(|p| *p == "..") || parts.first().map(|p| p.contains(':')).unwrap_or(false) {
        return Err(format!("unsafe path in archive: {name}"));
    }
    Ok(parts.iter().fold(base.to_path_buf(), |acc, p| acc.join(p)))
}

pub fn safe_extract(archive: &Path, dest: &Path) -> Result<(), String> {
    let name = archive.to_string_lossy().to_string();
    let file = std::fs::File::open(archive).map_err(|e| e.to_string())?;
    if name.ends_with(".zip") {
        let mut z = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
        for i in 0..z.len() {
            let mut entry = z.by_index(i).map_err(|e| e.to_string())?;
            let target = inside(dest, entry.name())?;
            if entry.is_dir() {
                std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;
                continue;
            }
            if let Some(d) = target.parent() {
                std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
            }
            let mut out = std::fs::File::create(&target).map_err(|e| e.to_string())?;
            std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
            #[cfg(unix)]
            if let Some(mode) = entry.unix_mode() {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode((mode & 0o777) | 0o600));
            }
        }
        return Ok(());
    }
    let mut t = tar::Archive::new(flate2::read::GzDecoder::new(file));
    for entry in t.entries().map_err(|e| e.to_string())? {
        let mut entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path().map_err(|e| e.to_string())?.to_string_lossy().into_owned();
        let target = inside(dest, &path)?;
        let kind = entry.header().entry_type();
        if kind.is_dir() {
            std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;
        } else if kind.is_file() {
            if let Some(d) = target.parent() {
                std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
            }
            let mut out = std::fs::File::create(&target).map_err(|e| e.to_string())?;
            std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let mode = entry.header().mode().unwrap_or(0o644);
                let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode((mode & 0o755) | 0o600));
            }
        }
        // links, devices and the like are skipped: release archives here don't need them
    }
    Ok(())
}

/// An unknown op (anything but install/uninstall).
pub fn check_op(op: &str) -> Res<()> {
    if op == "install" || op == "uninstall" { Ok(()) } else { Err(bad("op is install or uninstall")) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn archives_cant_write_outside_their_folder() {
        let base = Path::new("/tmp/x");
        assert!(inside(base, "../etc/passwd").is_err());
        assert!(inside(base, "/etc/passwd").is_err());
        assert!(inside(base, "C:/Windows/x").is_err());
        assert_eq!(inside(base, "Nova/windows/install.ps1").unwrap(), base.join("Nova").join("windows").join("install.ps1"));
    }
}
