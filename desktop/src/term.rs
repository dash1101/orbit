//! Terminals inside Orbit: an app's command (`sudo nova status`) runs here, in a real pseudo-terminal (ConPTY on
//! Windows), and the window shows its output and sends what you type: sudo's password prompt and interactive
//! questions work as they would in a terminal window. Only commands that an app adapter defines can be started.
use crate::net::now;
use crate::util::{self, Error, Res};
use portable_pty::{native_pty_system, CommandBuilder, PtySize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::{Arc, Mutex};

const MAX_BUFFER: usize = 1 << 20; // keep the last 1 MB of output per terminal
const MAX_SESSIONS: usize = 12;

struct Out {
    buf: Vec<u8>,
    base: usize, // bytes dropped from the front
    ended: Option<f64>,
    code: Option<i64>,
}

pub struct Session {
    pub id: String,
    pub title: String,
    pub shown: String,
    pub started: f64,
    out: Arc<Mutex<Out>>,
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    master: Mutex<Box<dyn portable_pty::MasterPty + Send>>,
    killer: Mutex<Box<dyn portable_pty::ChildKiller + Send + Sync>>,
}

fn append(out: &Mutex<Out>, data: &[u8]) {
    let mut o = out.lock().unwrap();
    o.buf.extend_from_slice(data);
    if o.buf.len() > MAX_BUFFER {
        let cut = o.buf.len() - MAX_BUFFER;
        o.buf.drain(..cut);
        o.base += cut;
    }
}

fn mask(arg: &str, secrets: &[String]) -> String {
    let mut a = arg.to_string();
    for s in secrets.iter().filter(|s| !s.is_empty()) {
        a = a.replace(s.as_str(), "••••••");
    }
    a
}

impl Session {
    fn start(argv: &[String], title: &str, cols: u16, rows: u16, cleanup: Vec<String>, secrets: &[String], shown: Option<String>) -> Result<Session, String> {
        let shown = shown.unwrap_or_else(|| argv.iter().map(|a| mask(a, secrets)).collect::<Vec<_>>().join(" "));
        let pty = native_pty_system().openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 }).map_err(|e| e.to_string())?;
        let mut cmd = CommandBuilder::new(&argv[0]);
        cmd.args(&argv[1..]);
        cmd.env("TERM", "xterm-256color");
        cmd.env("COLUMNS", cols.to_string());
        cmd.env("LINES", rows.to_string());
        cmd.env("PYTHONUNBUFFERED", "1");
        cmd.env_remove("NO_COLOR");
        if let Ok(dir) = std::env::current_dir() {
            cmd.cwd(dir);
        }
        let mut child = pty.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
        drop(pty.slave);
        let killer = child.clone_killer();
        let mut reader = pty.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer = pty.master.take_writer().map_err(|e| e.to_string())?;
        let out = Arc::new(Mutex::new(Out { buf: vec![], base: 0, ended: None, code: None }));
        append(&out, format!("\x1b[2m$ {shown}\x1b[0m\r\n").as_bytes());
        let o = out.clone();
        let id = util::new_id();
        std::thread::Builder::new().name(format!("orbit-term-{id}")).spawn(move || {
            let mut buf = [0u8; 65536];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) | Err(_) => break, // the program ended and closed the terminal
                    Ok(n) => append(&o, &buf[..n]),
                }
            }
            let code = child.wait().map(|s| s.exit_code() as i64).unwrap_or(-1);
            {
                let mut x = o.lock().unwrap();
                x.code = Some(code);
                x.ended = Some(now());
            }
            let word = if code == 0 { "finished".to_string() } else { format!("stopped with code {code}") };
            append(&o, format!("\r\n\x1b[2m── {word} ──\x1b[0m\r\n").as_bytes());
            for p in cleanup {
                let _ = std::fs::remove_file(p);
            }
        }).map_err(|e| e.to_string())?;
        Ok(Session { id, title: title.into(), shown, started: now(), out, writer: Mutex::new(Some(writer)),
                     master: Mutex::new(pty.master), killer: Mutex::new(killer) })
    }

    pub fn running(&self) -> bool {
        self.out.lock().unwrap().ended.is_none()
    }

    pub fn write(&self, text: &str) -> bool {
        if !self.running() {
            return false;
        }
        match self.writer.lock().unwrap().as_mut() {
            Some(w) => w.write_all(text.as_bytes()).and_then(|_| w.flush()).is_ok(),
            None => false,
        }
    }

    /// Ctrl+C: what a terminal sends.
    pub fn interrupt(&self) {
        let _ = self.write("\x03");
    }

    pub fn stop(&self) {
        if self.running() {
            let _ = self.killer.lock().unwrap().kill(); // like closing a terminal window
        }
    }

    pub fn resize(&self, cols: u16, rows: u16) {
        if self.running() {
            let _ = self.master.lock().unwrap().resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 });
        }
    }

    pub fn snapshot(&self, offset: usize) -> Value {
        let o = self.out.lock().unwrap();
        let start = offset.saturating_sub(o.base).min(o.buf.len());
        let end = o.base + o.buf.len();
        json!({"id": self.id, "title": self.title, "command": self.shown, "running": o.ended.is_none(), "code": o.code,
               "started": self.started, "ended": o.ended, "offset": end, "reset": offset < o.base,
               "data": String::from_utf8_lossy(&o.buf[start..])})
    }
}

#[derive(Default)]
pub struct Terminals {
    s: Mutex<HashMap<String, Arc<Session>>>,
}

pub struct StartArgs {
    pub cols: u16,
    pub rows: u16,
    pub cleanup: Vec<String>,
    pub mask: Vec<String>,
    pub shown: Option<String>,
}

impl Terminals {
    pub fn start(&self, argv: &[String], title: &str, a: StartArgs) -> Res<Arc<Session>> {
        {
            let mut s = self.s.lock().unwrap();
            let mut done: Vec<(f64, String)> = s.values().filter(|x| !x.running()).map(|x| (x.started, x.id.clone())).collect();
            done.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
            while s.len() >= MAX_SESSIONS && !done.is_empty() {
                s.remove(&done.remove(0).1);
            }
            if s.len() >= MAX_SESSIONS {
                return Err(Error::Bad("too many terminals open; close one first".into()));
            }
        }
        let sess = Session::start(argv, title, a.cols, a.rows, a.cleanup, &a.mask, a.shown)
            .map_err(|e| Error::Bad(format!("couldn't start {}: {e}", argv[0])))?;
        let sess = Arc::new(sess);
        self.s.lock().unwrap().insert(sess.id.clone(), sess.clone());
        Ok(sess)
    }

    pub fn get(&self, id: &str) -> Res<Arc<Session>> {
        self.s.lock().unwrap().get(id).cloned().ok_or_else(|| Error::NotFound("no such terminal".into()))
    }

    pub fn close(&self, id: &str) -> Res<()> {
        let s = self.get(id)?;
        s.stop();
        self.s.lock().unwrap().remove(id);
        Ok(())
    }

    pub fn list(&self) -> Value {
        let mut v: Vec<Arc<Session>> = self.s.lock().unwrap().values().cloned().collect();
        v.sort_by(|a, b| a.started.partial_cmp(&b.started).unwrap());
        json!(v.iter().map(|s| json!({"id": s.id, "title": s.title, "running": s.running(), "started": s.started})).collect::<Vec<_>>())
    }
}
