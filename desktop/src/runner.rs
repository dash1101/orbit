//! Running commands, with a dry-run mode that records changes instead of making them.
//!
//! Read-only commands always run. Anything that changes the system goes through `write`/`spawn`, which only record
//! what they would have done when dry-run is on. On Windows nothing Orbit runs in the background flashes a console.
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(windows)]
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(windows)]
pub const DETACHED_PROCESS: u32 = 0x0000_0008;

/// A command that runs quietly (no console window on Windows).
pub fn quiet(argv0: impl AsRef<std::ffi::OsStr>) -> Command {
    #[allow(unused_mut)]
    let mut c = Command::new(argv0);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(CREATE_NO_WINDOW);
    }
    c
}

pub fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT").unwrap_or_else(|_| ".EXE;.CMD;.BAT".into()).split(';').map(|s| s.to_lowercase()).collect()
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let p = dir.join(format!("{name}{ext}"));
            if is_executable(&p) {
                return Some(p);
            }
        }
        if cfg!(windows) && is_executable(&dir.join(name)) && Path::new(name).extension().is_some() {
            return Some(dir.join(name));
        }
    }
    None
}

fn is_executable(p: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        p.metadata().map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        p.is_file()
    }
}

/// Run a program and collect what it prints, up to `timeout`. (exit code, combined output); 127 = not installed,
/// 124 = took too long.
pub fn run_capture(argv: &[String], timeout: Duration, hidden: bool) -> (i32, String) {
    let mut cmd = if hidden { quiet(&argv[0]) } else { Command::new(&argv[0]) };
    cmd.args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (127, format!("{}: not installed", argv[0])),
        Err(e) => return (126, format!("{}: {e}", argv[0])),
    };
    let mut out = child.stdout.take().unwrap();
    let mut err = child.stderr.take().unwrap();
    let t1 = std::thread::spawn(move || {
        let mut s = Vec::new();
        let _ = out.read_to_end(&mut s);
        s
    });
    let t2 = std::thread::spawn(move || {
        let mut s = Vec::new();
        let _ = err.read_to_end(&mut s);
        s
    });
    let start = Instant::now();
    let code = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st.code().unwrap_or(-1),
            Ok(None) if start.elapsed() > timeout => {
                let _ = child.kill();
                let _ = child.wait();
                return (124, format!("{}: took longer than {} s", argv[0], timeout.as_secs()));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(15)),
            Err(e) => return (126, e.to_string()),
        }
    };
    let mut text = String::from_utf8_lossy(&t1.join().unwrap_or_default()).into_owned();
    text.push_str(&String::from_utf8_lossy(&t2.join().unwrap_or_default()));
    (code, text)
}

/// Start a program on its own and leave it running.
pub fn detached(argv: &[String], env: &[(&str, &str)]) -> std::io::Result<()> {
    let mut c = Command::new(&argv[0]);
    c.args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    for (k, v) in env {
        c.env(k, v);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(DETACHED_PROCESS);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
    }
    c.spawn().map(|_| ())
}

pub struct Runner {
    pub dry_run: bool,
    recorded: Mutex<Vec<(f64, String, Vec<String>)>>,
}

impl Runner {
    pub fn new(dry_run: bool) -> Runner {
        Runner { dry_run, recorded: Mutex::new(vec![]) }
    }

    pub fn record(&self, kind: &str, argv: Vec<String>) {
        let mut r = self.recorded.lock().unwrap();
        r.push((crate::net::now(), kind.into(), argv));
        let n = r.len();
        if n > 200 {
            r.drain(..n - 200);
        }
    }

    pub fn recorded(&self) -> Vec<(f64, String, Vec<String>)> {
        self.recorded.lock().unwrap().clone()
    }

    pub fn available(name: &str) -> bool {
        which(name).is_some()
    }

    /// Run a command that only looks at things.
    pub fn read(&self, argv: &[String], timeout: Duration) -> (i32, String) {
        run_capture(argv, timeout, true)
    }

    /// Run a command that changes something. Dry-run records it and reports success.
    pub fn write(&self, argv: &[String], timeout: Duration) -> (i32, String) {
        if self.dry_run {
            self.record("run", argv.to_vec());
            return (0, format!("dry run: would run {}", argv.join(" ")));
        }
        self.read(argv, timeout)
    }

    /// Start a program that changes something and leave it running. Dry-run records it instead.
    pub fn spawn(&self, argv: &[String]) -> bool {
        if self.dry_run {
            self.record("spawn", argv.to_vec());
            return true;
        }
        self.launch(argv)
    }

    /// Open something for the person to look at (a browser, a window, a terminal). Changes nothing, so it
    /// happens in dry-run too.
    pub fn launch(&self, argv: &[String]) -> bool {
        detached(argv, &[]).is_ok()
    }

    /// Open a web address in the default browser (on Windows through the shell, the way Windows opens links).
    pub fn open_url(&self, url: &str) -> bool {
        open_path(url)
    }

    /// Open a file the way double-clicking it would (a Start-menu shortcut, a folder).
    pub fn open_file(&self, path: &str) -> bool {
        open_path(path)
    }
}

pub fn open_path(target: &str) -> bool {
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::Shell::ShellExecuteW;
        use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
        let w = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        let (op, t) = (w("open"), w(target));
        let r = unsafe { ShellExecuteW(std::ptr::null_mut(), op.as_ptr(), t.as_ptr(), std::ptr::null(), std::ptr::null(), SW_SHOWNORMAL) };
        r as isize > 32
    }
    #[cfg(not(windows))]
    {
        detached(&open_argv(target), &[]).is_ok()
    }
}

/// argv that opens a web address or file the way the system does.
pub fn open_argv(url: &str) -> Vec<String> {
    if cfg!(windows) {
        vec!["rundll32".into(), "url.dll,FileProtocolHandler".into(), url.into()]
    } else if cfg!(target_os = "macos") {
        vec!["open".into(), url.into()]
    } else {
        vec!["xdg-open".into(), url.into()]
    }
}

/// argv that opens a terminal window running `command` and keeps it open afterwards.
pub fn terminal_argv(command: &[String], title: &str) -> Option<Vec<String>> {
    if cfg!(windows) {
        let line = command.iter().map(|a| win_quote(a)).collect::<Vec<_>>().join(" ");
        return Some(vec!["cmd".into(), "/c".into(), "start".into(), title.into(), "cmd".into(), "/k".into(), line]);
    }
    let shell = command.iter().map(|c| sh_quote(c)).collect::<Vec<_>>().join(" ");
    if cfg!(target_os = "macos") {
        let script = shell.replace('\\', "\\\\").replace('"', "\\\"");
        return Some(vec!["osascript".into(), "-e".into(), format!("tell application \"Terminal\" to do script \"{script}\""),
                         "-e".into(), "tell application \"Terminal\" to activate".into()]);
    }
    let shell = format!("{shell}; echo; read -r -p \"Press Enter to close\" _");
    if which("konsole").is_some() {
        return Some(vec!["konsole".into(), "-p".into(), format!("tabtitle={title}"), "-e".into(), "bash".into(), "-c".into(), shell]);
    }
    if which("gnome-terminal").is_some() {
        return Some(vec!["gnome-terminal".into(), "--title".into(), title.into(), "--".into(), "bash".into(), "-c".into(), shell]);
    }
    if which("x-terminal-emulator").is_some() {
        return Some(vec!["x-terminal-emulator".into(), "-e".into(), "bash".into(), "-c".into(), shell]);
    }
    if which("xterm").is_some() {
        return Some(vec!["xterm".into(), "-T".into(), title.into(), "-e".into(), "bash".into(), "-c".into(), shell]);
    }
    None
}

/// POSIX shell quoting (shlex.quote).
pub fn sh_quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

/// Windows command-line quoting (subprocess.list2cmdline).
pub fn win_quote(s: &str) -> String {
    if !s.is_empty() && !s.contains([' ', '\t', '"']) {
        return s.to_string();
    }
    let mut out = String::from("\"");
    let mut bs = 0;
    for c in s.chars() {
        match c {
            '\\' => bs += 1,
            '"' => {
                out.push_str(&"\\".repeat(bs * 2 + 1));
                out.push('"');
                bs = 0;
            }
            _ => {
                out.push_str(&"\\".repeat(bs));
                bs = 0;
                out.push(c);
            }
        }
    }
    out.push_str(&"\\".repeat(bs * 2));
    out.push('"');
    out
}
