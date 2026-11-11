//! What computer Orbit is running on: system, CPU, Linux flavour, and where per-user programs live.
//! Everything an install recipe needs to know about the machine goes through a `Host`, so tests can pretend
//! to be Windows or a Mac.
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Clone)]
pub struct Host {
    pub system: String, // linux | darwin | windows
    pub arch: String,   // amd64 | arm64 | …
    pub home: PathBuf,
    pub env: HashMap<String, String>,
    pub which_map: Option<HashMap<String, Option<String>>>, // tests: {name: path}
    pub systemd: Option<bool>,
    pub distro_name: Option<String>,
}

pub fn system_name(system: &str) -> String {
    match system {
        "linux" => "Linux",
        "darwin" => "macOS",
        "windows" => "Windows",
        s => s,
    }
    .to_string()
}

impl Host {
    pub fn real() -> Host {
        let system = if cfg!(windows) { "windows" } else if cfg!(target_os = "macos") { "darwin" } else { "linux" };
        let arch = match std::env::consts::ARCH {
            "x86_64" => "amd64",
            "aarch64" => "arm64",
            a => a,
        };
        Host {
            system: system.into(),
            arch: arch.into(),
            home: crate::config::home(),
            env: std::env::vars().collect(),
            which_map: None,
            systemd: None,
            distro_name: None,
        }
    }

    pub fn which(&self, name: &str) -> Option<String> {
        if let Some(m) = &self.which_map {
            return m.get(name).cloned().flatten();
        }
        crate::runner::which(name).map(|p| p.to_string_lossy().into_owned())
    }

    /// Full path of a program when it's on the PATH, else its bare name (the error then says what's missing).
    pub fn tool(&self, name: &str) -> String {
        self.which(name).unwrap_or_else(|| name.to_string())
    }

    /// Run a command that only looks at things (a version check).
    pub fn probe(&self, argv: &[String]) -> (i32, String) {
        crate::runner::run_capture(argv, Duration::from_secs(8), true)
    }

    pub fn has_display(&self) -> bool {
        if self.system != "linux" {
            return true;
        }
        self.env.get("DISPLAY").map(|s| !s.is_empty()).unwrap_or(false)
            || self.env.get("WAYLAND_DISPLAY").map(|s| !s.is_empty()).unwrap_or(false)
    }

    pub fn uid(&self) -> u32 {
        #[cfg(unix)]
        return unsafe { libc::getuid() };
        #[cfg(not(unix))]
        0
    }

    pub fn has_systemd(&self) -> bool {
        if let Some(s) = self.systemd {
            return s;
        }
        self.system == "linux" && std::path::Path::new("/run/systemd/system").is_dir()
    }

    /// apt + dpkg: install .deb packages so the system's updates keep track of them.
    pub fn debian_like(&self) -> bool {
        self.system == "linux" && self.which("apt-get").is_some() && self.which("dpkg").is_some()
    }

    pub fn distro(&self) -> String {
        if let Some(d) = &self.distro_name {
            return d.clone();
        }
        if self.system != "linux" {
            return String::new();
        }
        if let Ok(text) = std::fs::read_to_string("/etc/os-release") {
            for line in text.lines() {
                if let Some(v) = line.strip_prefix("PRETTY_NAME=") {
                    return v.trim().trim_matches('"').to_string();
                }
            }
        }
        "Linux".into()
    }

    pub fn label(&self) -> String {
        let d = self.distro();
        let name = if self.system == "linux" && !d.is_empty() { d } else { system_name(&self.system) };
        let cpu = match self.arch.as_str() {
            "amd64" => "x86-64",
            "arm64" => "ARM 64-bit",
            a => a,
        };
        format!("{name} · {cpu}")
    }

    fn env_path(&self, key: &str) -> Option<PathBuf> {
        self.env.get(key).filter(|v| !v.is_empty()).map(PathBuf::from)
    }

    /// Per-user program folder root: %LOCALAPPDATA%, ~/Library/Application Support, ~/.local/share.
    pub fn local_app_data(&self) -> PathBuf {
        match self.system.as_str() {
            "windows" => self.env_path("LOCALAPPDATA").unwrap_or_else(|| self.home.join("AppData").join("Local")),
            "darwin" => self.home.join("Library").join("Application Support"),
            _ => self.env_path("XDG_DATA_HOME").unwrap_or_else(|| self.home.join(".local").join("share")),
        }
    }

    pub fn config_home(&self) -> PathBuf {
        match self.system.as_str() {
            "windows" => self.env_path("APPDATA").unwrap_or_else(|| self.home.join("AppData").join("Roaming")),
            "darwin" => self.home.join("Library").join("Application Support"),
            _ => self.env_path("XDG_CONFIG_HOME").unwrap_or_else(|| self.home.join(".config")),
        }
    }

    pub fn bin_dir(&self) -> PathBuf {
        self.home.join(".local").join("bin")
    }

    pub fn start_menu(&self) -> PathBuf {
        self.config_home().join("Microsoft").join("Windows").join("Start Menu").join("Programs")
    }

    pub fn program_files(&self) -> PathBuf {
        self.env_path("ProgramFiles").unwrap_or_else(|| PathBuf::from("C:\\Program Files"))
    }

    pub fn program_data(&self) -> PathBuf {
        self.env_path("ProgramData").unwrap_or_else(|| PathBuf::from("C:\\ProgramData"))
    }

    pub fn describe(&self) -> Value {
        json!({"system": self.system, "systemName": system_name(&self.system), "arch": self.arch, "label": self.label()})
    }
}
