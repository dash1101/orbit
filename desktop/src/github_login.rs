//! Sign in with GitHub, so Orbit can read releases from private repositories.
//!
//! GitHub's device flow: Orbit asks GitHub for a short code, you enter it at github.com/login/device, and Orbit
//! receives a token. No password passes through Orbit, and no client secret is needed. The token is kept in
//! Orbit's settings file (readable by you only) and only sent to api.github.com.
use crate::config::Config;
use crate::net;
use serde_json::{json, Map, Value};
use std::io::Read;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// The NovaLabs Orbit GitHub App (public identifier, not a secret). Empty: signing in is unavailable.
pub const CLIENT_ID: &str = "";
const DEVICE_URL: &str = "https://github.com/login/device/code";
const TOKEN_URL: &str = "https://github.com/login/oauth/access_token";
const MIN_INTERVAL: u64 = 5; // GitHub's own minimum between polls

#[derive(Debug)]
pub struct LoginError(pub String);

fn post(url: &str, fields: &[(&str, &str)]) -> Result<Value, LoginError> {
    let a = net::agent(Duration::from_secs(15), false);
    let res = a.post(url).set("Accept", "application/json").send_form(fields);
    let read = |r: ureq::Response| -> Value {
        let mut buf = Vec::new();
        let _ = r.into_reader().take(64 << 10).read_to_end(&mut buf);
        serde_json::from_slice(&buf).unwrap_or(json!({}))
    };
    match res {
        Ok(r) => Ok(read(r)),
        Err(ureq::Error::Status(code, r)) => {
            let v = read(r);
            if v.as_object().map(|m| m.is_empty()).unwrap_or(true) {
                Err(LoginError(format!("GitHub answered {code}")))
            } else {
                Ok(v)
            }
        }
        Err(_) => Err(LoginError("Couldn't reach GitHub. Check your internet connection.".into())),
    }
}

#[derive(Default)]
struct State {
    pending: Option<Map<String, Value>>, // user_code, verification_uri, device_code, interval, expires
    error: String,
    cancel: bool,
}

pub struct Login {
    config: Arc<Config>,
    client_id: String,
    state: Arc<Mutex<State>>,
}

impl Login {
    pub fn new(config: Arc<Config>) -> Login {
        Login { config, client_id: CLIENT_ID.into(), state: Arc::new(Mutex::new(State::default())) }
    }

    pub fn available(&self) -> bool {
        !self.client_id.is_empty()
    }

    /// The token other parts of Orbit use.
    pub fn token(&self) -> String {
        self.config.get("github").get("token").and_then(|t| t.as_str()).unwrap_or("").to_string()
    }

    pub fn state(&self) -> Value {
        let gh = self.config.get("github");
        let (mut pending, error) = {
            let s = self.state.lock().unwrap();
            (s.pending.clone(), s.error.clone())
        };
        let (mut source, mut who) = ("", String::new());
        if gh.get("token").and_then(|t| t.as_str()).map(|t| !t.is_empty()).unwrap_or(false) {
            source = "orbit";
            who = gh.get("login").and_then(|l| l.as_str()).unwrap_or("").to_string();
        } else if !net::env_token().is_empty() {
            source = "env";
        } else if !net::gh_cli_token().is_empty() {
            source = "gh";
        }
        if let Some(p) = pending.as_mut() {
            p.remove("device_code");
        }
        json!({"available": self.available(), "signedIn": !source.is_empty(), "source": source, "login": who,
               "pending": pending, "error": error})
    }

    pub fn start(&self) -> Result<Value, LoginError> {
        if !self.available() {
            return Err(LoginError("Signing in with GitHub isn't set up in this build of Orbit.".into()));
        }
        {
            let s = self.state.lock().unwrap();
            if let Some(p) = &s.pending {
                if p["expires"].as_f64().unwrap_or(0.0) > net::now() {
                    drop(s);
                    return Ok(self.state());
                }
            }
        }
        let r = post(DEVICE_URL, &[("client_id", &self.client_id)])?;
        let Some(device_code) = r["device_code"].as_str() else {
            let msg = r["error_description"].as_str().or(r["error"].as_str()).unwrap_or("GitHub didn't start the sign-in.");
            return Err(LoginError(msg.into()));
        };
        {
            let mut s = self.state.lock().unwrap();
            let mut p = Map::new();
            p.insert("user_code".into(), r["user_code"].clone());
            p.insert("verification_uri".into(), json!(r["verification_uri"].as_str().unwrap_or("https://github.com/login/device")));
            p.insert("device_code".into(), json!(device_code));
            p.insert("interval".into(), json!(MIN_INTERVAL.max(r["interval"].as_u64().unwrap_or(5))));
            p.insert("expires".into(), json!(net::now() + r["expires_in"].as_f64().unwrap_or(900.0)));
            s.pending = Some(p);
            s.error.clear();
            s.cancel = false;
        }
        let st = self.state(); // read before polling starts
        let (state, config, client_id) = (self.state.clone(), self.config.clone(), self.client_id.clone());
        std::thread::Builder::new().name("orbit-github-login".into()).spawn(move || poll(state, config, client_id)).ok();
        Ok(st)
    }

    pub fn cancel(&self) {
        let mut s = self.state.lock().unwrap();
        s.cancel = true;
        s.pending = None;
    }

    pub fn sign_out(&self) -> Value {
        self.cancel();
        let mut m = Map::new();
        m.insert("github".into(), json!({}));
        self.config.update(m);
        net::forget_token();
        self.state()
    }

    /// {repo: "public" | "private" | "no access" | "offline"} with the token Orbit would use.
    pub fn access(&self, repos: &[String]) -> Value {
        let mut out = Map::new();
        for repo in repos {
            let v = match net::get_json(&format!("https://api.github.com/repos/{repo}"),
                                        &net::Req { timeout: 6.0, headers: net::github_headers(), insecure: false }) {
                Ok(r) => if r["private"].as_bool().unwrap_or(false) { "private" } else { "public" },
                Err(e) if matches!(e.status, 401 | 403 | 404) => "no access",
                Err(_) => "offline",
            };
            out.insert(repo.clone(), json!(v));
        }
        Value::Object(out)
    }
}

fn fail(state: &Mutex<State>, msg: &str) {
    let mut s = state.lock().unwrap();
    s.pending = None;
    s.error = msg.into();
}

fn poll(state: Arc<Mutex<State>>, config: Arc<Config>, client_id: String) {
    loop {
        let (interval, device_code, expires) = {
            let s = state.lock().unwrap();
            match &s.pending {
                Some(p) if !s.cancel => (p["interval"].as_u64().unwrap_or(5), p["device_code"].as_str().unwrap_or("").to_string(),
                                         p["expires"].as_f64().unwrap_or(0.0)),
                _ => return,
            }
        };
        std::thread::sleep(Duration::from_secs(interval));
        if net::now() > expires {
            return fail(&state, "The code expired before it was entered. Start again.");
        }
        let r = match post(TOKEN_URL, &[("client_id", &client_id), ("device_code", &device_code),
                                        ("grant_type", "urn:ietf:params:oauth:grant-type:device_code")]) {
            Ok(r) => r,
            Err(_) => continue, // a network blip: keep waiting
        };
        match r["error"].as_str() {
            Some("authorization_pending") => continue,
            Some("slow_down") => {
                let mut s = state.lock().unwrap();
                if let Some(p) = s.pending.as_mut() {
                    let new = r["interval"].as_u64().unwrap_or(interval + 5);
                    p.insert("interval".into(), json!(MIN_INTERVAL.max(new)));
                }
                continue;
            }
            Some(err) => {
                let msg = match err {
                    "expired_token" => "The code expired before it was entered. Start again.".to_string(),
                    "access_denied" => "The sign-in was cancelled on GitHub.".to_string(),
                    _ => r["error_description"].as_str().unwrap_or(err).to_string(),
                };
                return fail(&state, &msg);
            }
            None => {}
        }
        let Some(tok) = r["access_token"].as_str().map(String::from) else {
            return fail(&state, "GitHub didn't send a token.");
        };
        let login = net::get_json("https://api.github.com/user", &net::Req {
            timeout: 8.0,
            headers: vec![("Authorization", format!("Bearer {tok}")), ("Accept", "application/vnd.github+json".into())],
            insecure: false,
        }).ok().and_then(|u| u["login"].as_str().map(String::from)).unwrap_or_default();
        {
            let mut s = state.lock().unwrap();
            if s.cancel {
                return;
            }
            s.pending = None;
        }
        let mut m = Map::new();
        m.insert("github".into(), json!({"token": tok, "login": login, "since": net::now() as i64}));
        config.update(m);
        net::forget_token();
        return;
    }
}
