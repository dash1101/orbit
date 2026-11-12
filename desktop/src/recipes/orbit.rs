//! Orbit itself: the program updates itself in place (Linux, Windows and macOS).
//!
//! The new program comes from Orbit's own GitHub release and is checked against the release's SHA256SUMS. It then
//! has to start and say which version it is, so a broken or wrong download never takes the old one's place. It
//! waits next to the running program (`orbit.new`, with `orbit.new.ok` saying it passed) until Orbit restarts.
//! At the switch the old program is renamed (`orbit.old`; Windows allows renaming a running program), the new one
//! takes its name, and the new one starts. The new Orbit deletes `orbit.old` once it runs. On macOS the whole app
//! (Orbit.app) is replaced; the waiting and old copies are hidden .app folders (.Orbit-new.app, .Orbit-old.app).
//!
//! The release files are named without the version (orbit-windows-x64.exe …). A copy named with one
//! (orbit-v0.3.4-windows-x64.exe) takes the plain name at its next update, and Orbit's own menu entry follows it.
use crate::engine::{Session, Sources};
use crate::host::Host;
use crate::recipe::{need, step, Recipe, RecipeBase, StepDef};
use crate::util::{self, failed, Res};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

pub const REPO: &str = "dash1101/orbit";

/// The program Orbit is running from.
pub fn running_program() -> Option<PathBuf> {
    let mut p = std::env::current_exe().ok()?;
    // Linux: once apt (or anything) has put a new file in its place, the running program's path ends in " (deleted)"
    if let Some(s) = p.to_str().and_then(|s| s.strip_suffix(" (deleted)")) {
        p = PathBuf::from(s);
    }
    if cfg!(windows) {
        return Some(p); // canonical paths there start with \\?\, which shortcuts and logs shouldn't show
    }
    Some(std::fs::canonicalize(&p).unwrap_or(p))
}

/// What an update replaces: the program file, or on macOS the whole app it's inside (…/Orbit.app).
pub fn unit(exe: &Path) -> PathBuf {
    let parent = exe.parent();
    if let (Some(macos), Some(contents)) = (parent, parent.and_then(|p| p.parent())) {
        if macos.file_name().map(|n| n == "MacOS").unwrap_or(false) && contents.file_name().map(|n| n == "Contents").unwrap_or(false) {
            if let Some(app) = contents.parent() {
                if app.extension().map(|e| e == "app").unwrap_or(false) {
                    return app.to_path_buf();
                }
            }
        }
    }
    exe.to_path_buf()
}

/// The new or old copy next to `path` (".new", ".old" or ".new.ok"). An app's folder must keep ending in .app to
/// start, so for a Mac app these are hidden names: .Orbit-new.app, .Orbit-old.app, .Orbit-new.ok.
pub fn beside(path: &Path, suffix: &str) -> PathBuf {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    if path.extension().map(|e| e == "app").unwrap_or(false) {
        let s = path.file_stem().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let n = match suffix { ".new" => format!(".{s}-new.app"), ".old" => format!(".{s}-old.app"), _ => format!(".{s}-new.ok") };
        return path.with_file_name(n);
    }
    path.with_file_name(format!("{name}{suffix}"))
}

/// Where the program goes at its update: its own place, except that a file named with a version
/// (orbit-v0.3.4-linux-x86_64) becomes orbit-linux-x86_64, when nothing by that name is in the way.
pub fn settled(u: &Path) -> PathBuf {
    let re = regex::Regex::new(r"^orbit-v\d+(?:\.\d+)*-(.+)$").unwrap();
    let name = u.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    if u.extension().map(|e| e == "app").unwrap_or(false) {
        return u.to_path_buf();
    }
    match re.captures(&name) {
        Some(c) => {
            let plain = u.with_file_name(format!("orbit-{}", &c[1]));
            if util::exists(&plain) { u.to_path_buf() } else { plain }
        }
        None => u.to_path_buf(),
    }
}

/// Delete the previous program left by an update, once the new one runs. Quietly.
pub fn cleanup_old(exe: Option<&Path>) -> bool {
    let Some(exe) = exe.map(Path::to_path_buf).or_else(running_program) else { return true };
    util::remove_any(&beside(&unit(&exe), ".old")).is_ok() // Windows: the old one may still be closing
}

/// Orbit installed from its Debian/Ubuntu package: /usr/bin/orbit, which the orbit package owns. It updates through
/// apt (the new .deb is installed in place; the system asks for the password).
pub fn deb_install(exe: &Path) -> bool {
    cfg!(target_os = "linux") && exe == Path::new("/usr/bin/orbit") && deb_version().is_some()
}

/// The orbit package's installed version, when the package is installed.
pub fn deb_version() -> Option<String> {
    if !cfg!(target_os = "linux") || crate::runner::which("dpkg-query").is_none() {
        return None;
    }
    let (rc, out) = crate::runner::run_capture(&["dpkg-query".into(), "-W".into(), "-f=${Status}|${Version}".into(), "orbit".into()],
                                               Duration::from_secs(8), true);
    (rc == 0 && out.contains("install ok installed")).then(|| out.rsplit('|').next().unwrap_or("").trim().to_string())
}

/// The version of a checked new Orbit waiting next to this one for a restart, or "". For the Debian package: a newer
/// version apt has already put in place.
pub fn waiting(exe: Option<&Path>) -> String {
    let Some(exe) = exe.map(Path::to_path_buf).or_else(running_program) else { return String::new() };
    if deb_install(&exe) {
        return deb_version().filter(|v| crate::net::is_newer(v, crate::VERSION)).unwrap_or_default();
    }
    let u = unit(&exe);
    let said = std::fs::read_to_string(beside(&u, ".new.ok")).unwrap_or_default();
    let said = said.trim();
    match said.strip_prefix("orbit ") {
        Some(v) if util::exists(&beside(&u, ".new")) => v.to_string(),
        _ => String::new(),
    }
}

/// Put the waiting new Orbit in this one's place. Returns the program to start (the same path, or the plain name
/// when this one's name had its version in it), or None when nothing was waiting. Call it only right before
/// starting the new one.
pub fn switch(exe: Option<&Path>) -> std::io::Result<Option<PathBuf>> {
    let Some(exe) = exe.map(Path::to_path_buf).or_else(running_program) else { return Ok(None) };
    if waiting(Some(&exe)).is_empty() || deb_install(&exe) {
        return Ok(None); // the Debian package: apt has already put the new program in place
    }
    let u = unit(&exe);
    let target = settled(&u);
    let (new, old) = (beside(&u, ".new"), beside(&target, ".old"));
    let _ = util::remove_any(&old);
    std::fs::rename(&u, &old)?;
    if let Err(e) = std::fs::rename(&new, &target) {
        let _ = std::fs::rename(&old, &u); // put the old one back
        return Err(e);
    }
    let _ = std::fs::remove_file(beside(&u, ".new.ok"));
    if target != u {
        crate::desktop_entry::follow(&u, &target);
        return Ok(Some(target));
    }
    Ok(Some(exe))
}

pub struct OrbitRecipe {
    base: RecipeBase,
    /// Tests point this at a stand-in program; None = the one running.
    pub program: Option<PathBuf>,
    pub version: String,
}

impl OrbitRecipe {
    pub fn new(host: Host, sources: Arc<Sources>, program: Option<PathBuf>) -> OrbitRecipe {
        OrbitRecipe { base: RecipeBase::new(host, sources), program, version: crate::VERSION.into() }
    }

    fn exe(&self) -> Option<PathBuf> {
        self.program.clone().or_else(running_program)
    }

    /// The release file for this computer (orbit-windows-x64.exe …). With a tag: the name releases before 0.3.5 used.
    pub fn asset(&self, tag: &str) -> Option<String> {
        let h = &self.base.host;
        let kind = match (h.system.as_str(), h.arch.as_str()) {
            ("windows", "arm64") => "windows-arm64.exe".to_string(),
            ("windows", _) => "windows-x64.exe".to_string(),
            ("linux", "amd64") => "linux-x86_64".to_string(),
            ("linux", "arm64") => "linux-arm64".to_string(),
            ("darwin", a) => format!("macos-{}.dmg", if a == "arm64" { "apple-silicon" } else { "intel" }),
            _ => return None,
        };
        Some(if tag.is_empty() { format!("orbit-{kind}") } else { format!("orbit-{tag}-{kind}") })
    }

    fn deb(&self) -> bool {
        self.program.is_none() && self.exe().map(|e| deb_install(&e)).unwrap_or(false)
    }

    /// The name this release gives the file: without the version (0.3.5 on), or with it (before). Orbit installed
    /// from the Debian package gets the package.
    pub fn pick(&self, rel: &Value) -> String {
        if self.deb() {
            return format!("{}.deb", self.asset("").unwrap_or_default());
        }
        let tag = rel["tag"].as_str().unwrap_or("");
        for name in [self.asset(""), self.asset(tag)].into_iter().flatten() {
            if rel["assets"].get(&name).is_some() {
                return name;
            }
        }
        self.asset("").unwrap_or_default()
    }

    fn find(&self, s: &mut Session) -> Res<bool> {
        let rel = s.release(REPO)?;
        let name = self.pick(&rel);
        s.asset(&rel, &name, REPO)?;
        s.asset(&rel, "SHA256SUMS", REPO)?;
        s.detail(rel["tag"].as_str().unwrap_or(""));
        s.vars.insert("name".into(), json!(name));
        s.vars.insert("rel".into(), rel);
        Ok(true)
    }

    fn download(&self, s: &mut Session) -> Res<bool> {
        let (rel, name) = (s.vars["rel"].clone(), s.var_str("name"));
        let f = s.download(&rel, &name, REPO, None)?;
        s.vars.insert("file".into(), json!(f.to_string_lossy()));
        Ok(true)
    }

    fn verify(&self, s: &mut Session) -> Res<bool> {
        let rel = s.vars["rel"].clone();
        let sums = s.fetch_text(&rel, "SHA256SUMS", REPO)?;
        s.verify(&PathBuf::from(s.var_str("file")), &sums, &s.var_str("name"))?;
        Ok(true)
    }

    /// The Debian package: apt installs the new .deb in place (the running Orbit keeps going until it restarts).
    fn apt_install(&self, s: &mut Session) -> Res<bool> {
        let want = util::version_of(s.vars["rel"]["tag"].as_str().unwrap_or(""));
        let file = s.var_str("file");
        let done = || deb_version().map(|v| v == want || crate::net::is_newer(&v, crate::VERSION)).unwrap_or(false);
        let argv: Vec<String> = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "--allow-downgrades", &file]
            .iter().map(|x| x.to_string()).collect();
        s.admin(&argv, "install Orbit's update", Some(&done), "The log below shows what apt said.")?;
        if !s.dry_run && !done() {
            return Err(failed(format!("apt finished, but the orbit package isn't {want}."), "The log below shows what apt said."));
        }
        s.log(&format!("Orbit {want} is installed; it takes over when Orbit restarts."));
        Ok(true)
    }

    /// Copy it next to the running program and start it once: it has to write its version back.
    fn try_it(&self, s: &mut Session) -> Res<bool> {
        let exe = self.exe().ok_or_else(|| failed("Orbit can't find its own program.", ""))?;
        let u = unit(&exe);
        let new = beside(&u, ".new");
        let want = util::version_of(s.vars["rel"]["tag"].as_str().unwrap_or(""));
        if waiting(Some(&exe)) == want {
            // already checked and waiting (Orbit got it ready by itself, or an earlier update did): leave it be
            s.log(&format!("Orbit {want} is already ready next to this one; it takes over when Orbit restarts."));
            return Ok(false);
        }
        s.detail(&format!("starting it once · it should say {want}"));
        if s.dry_run {
            s.pause(0.4)?;
            s.log(&format!("dry run: would copy it to {} and run it once to check it says orbit {want}", new.display()));
            return Ok(true);
        }
        let _ = util::remove_any(&new);
        let file = PathBuf::from(s.var_str("file"));
        let new_exe = if u != exe {
            // macOS: the app comes out of the release's disk image
            let mnt = s.tmp().join("image");
            let _ = std::fs::create_dir_all(&mnt);
            let m = mnt.to_string_lossy().to_string();
            s.run(&["hdiutil".into(), "attach".into(), "-nobrowse".into(), "-readonly".into(), "-noautoopen".into(), "-mountpoint".into(),
                    m.clone(), file.to_string_lossy().into()], &[0], true, Some("Couldn't open the downloaded disk image."), "")?;
            let inside = mnt.join(u.file_name().unwrap_or_default());
            let copied = if inside.is_dir() {
                s.run(&["ditto".into(), inside.to_string_lossy().into(), new.to_string_lossy().into()], &[0], true,
                      Some("Couldn't copy Orbit out of the disk image."), "").map(|_| ())
            } else {
                Err(failed(format!("The disk image doesn't contain {}.", u.file_name().unwrap_or_default().to_string_lossy()), ""))
            };
            let _ = s.run(&["hdiutil".into(), "detach".into(), m, "-force".into()], &(0..256).collect::<Vec<_>>(), true, None, "");
            copied?;
            new.join(exe.strip_prefix(&u).unwrap_or(Path::new("Contents/MacOS/Orbit")))
        } else {
            std::fs::copy(&file, &new).map_err(|e| failed(format!("Couldn't put the new Orbit next to this one: {e}"), ""))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&new, std::fs::Permissions::from_mode(0o755));
            }
            new.clone()
        };
        let out = std::env::temp_dir().join(format!("orbit-version-{}", util::new_id()));
        let argv = vec![new_exe.to_string_lossy().into_owned(), "--write-version".into(), out.to_string_lossy().into_owned()];
        let (code, printed) = crate::runner::run_capture(&argv, Duration::from_secs(120), true);
        if code != 0 {
            for line in printed.trim().lines().rev().take(15).collect::<Vec<_>>().into_iter().rev() {
                s.log(&format!("  {line}"));
            }
        }
        let said = std::fs::read_to_string(&out).map(|t| t.trim().to_string()).unwrap_or_default();
        let _ = std::fs::remove_file(&out);
        s.log(&format!("The new program says: {}{}", if said.is_empty() { "(nothing)" } else { &said }, format_args!(" (exit {code})")));
        if code != 0 || said != format!("orbit {want}") {
            let _ = util::remove_any(&new);
            return Err(failed(format!("The download didn't start as Orbit {want}, so it won't replace this one."),
                              "Nothing was replaced. Try again later, or download it from the releases page."));
        }
        std::fs::write(beside(&u, ".new.ok"), format!("orbit {want}\n")).map_err(|e| failed(e.to_string(), ""))?;
        s.log(&format!("Orbit {want} is ready next to this one; it takes over when Orbit restarts."));
        Ok(true)
    }
}

impl Recipe for OrbitRecipe {
    fn base(&self) -> &RecipeBase {
        &self.base
    }
    fn id(&self) -> &str {
        "orbit"
    }
    fn name(&self) -> &str {
        "Orbit"
    }
    fn repo(&self) -> &str {
        REPO
    }
    fn blurb(&self) -> &str {
        "This app: one place for all your apps"
    }
    fn glyph(&self) -> &str {
        "OR"
    }
    fn color(&self) -> &str {
        "#f0668e"
    }
    fn self_app(&self) -> bool {
        true
    }
    fn platform(&self, system: &str) -> (String, String) {
        match system {
            "linux" | "windows" | "darwin" => ("full".into(), String::new()),
            _ => ("none".into(), "Orbit isn't available on this system yet.".into()),
        }
    }

    fn detect(&self) -> Value {
        let exe = self.exe();
        if self.deb() {
            return json!({"installed": true, "version": self.version, "pending": waiting(exe.as_deref()),
                          "where": "/usr/bin/orbit", "how": "the Debian package"});
        }
        json!({"installed": true, "version": self.version, "pending": waiting(exe.as_deref()),
               "where": exe.map(|e| e.to_string_lossy().into_owned()).unwrap_or_default(), "how": "the program"})
    }

    fn can_uninstall(&self) -> (bool, String) {
        (false, "Orbit doesn't remove itself: close it and delete the program file.".into())
    }

    fn requirements(&self, op: &str) -> Vec<Value> {
        if op == "uninstall" {
            return vec![];
        }
        if self.asset("").is_none() {
            return vec![need("A program for this computer", false, &format!("There's no Orbit for {} yet.", self.base.host.label()), true, "")];
        }
        let Some(exe) = self.exe() else { return vec![] };
        if self.deb() {
            let h = &self.base.host;
            let way = (h.which("pkexec").is_some() && h.has_display()) || h.which("sudo").is_some();
            return vec![need("A way to ask for the administrator password", way,
                             "Orbit installs its update with apt, which needs the administrator. Install pkexec (polkit), or update with                               `sudo apt install ./orbit-linux-…deb` from the releases page.", true, "")];
        }
        let place = unit(&exe).parent().map(Path::to_path_buf).unwrap_or_default();
        vec![need("Orbit's folder can be written to", writable(&place),
                  &format!("Orbit can't replace itself in {}. Download the new version from the releases page instead.", place.display()),
                  true, &place.to_string_lossy())]
    }

    fn assets(&self, rel: &Value) -> Vec<String> {
        if self.asset("").is_none() { vec![] } else { vec![self.pick(rel), "SHA256SUMS".into()] }
    }

    fn title(&self, _op: &str, rel: Option<&Value>) -> String {
        let new = rel.map(|r| util::version_of(r["tag"].as_str().unwrap_or(""))).unwrap_or_default();
        if !new.is_empty() && new != self.version { format!("Update Orbit to {new}") } else { "Reinstall Orbit".into() }
    }

    fn summary(&self, _op: &str, _opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        let tag = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the newest release");
        let exe = self.exe();
        let u = exe.as_ref().map(|e| unit(e));
        let name = u.as_ref().and_then(|u| u.file_name()).map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "orbit".into());
        if self.deb() {
            return vec![format!("Download Orbit {tag} from GitHub: {}", rel.map(|r| self.pick(r)).unwrap_or_else(|| "its Debian package".into())),
                        "Check it against the release's checksums".into(),
                        "Install it with apt, as the administrator (the system asks for your password)".into(),
                        "Restart Orbit on it".into()];
        }
        let moved = u.as_ref().filter(|u| settled(u) != **u).map(|u| format!(" as {}", settled(u).file_name().unwrap_or_default().to_string_lossy())).unwrap_or_default();
        vec![format!("Download Orbit {tag} from GitHub: {}", rel.map(|r| self.pick(r)).unwrap_or_else(|| "the program for this computer".into())),
             "Check it against the release's checksums".into(),
             "Start it once to make sure it runs and is the version it should be".into(),
             format!("Keep it next to this one ({name}) until you restart Orbit: then it takes this one's place{moved}, and the old one is kept until the new one has started")]
    }

    fn steps(self: Arc<Self>, _op: &str, _opts: &Map<String, Value>) -> Vec<StepDef> {
        let me = |f: fn(&OrbitRecipe, &mut Session) -> Res<bool>| {
            let r = self.clone();
            move |s: &mut Session| f(&r, s)
        };
        let last = if self.deb() { step("install", "Install it with apt", 2.0, me(Self::apt_install)) }
                   else { step("try", "Make sure it runs", 1.0, me(Self::try_it)) };
        vec![
            step("find", "Find the latest release", 0.5, me(Self::find)),
            step("download", "Download", 4.0, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
            last,
        ]
    }

    fn finish(&self, sess: &Session, _op: &str, _opts: &Map<String, Value>) -> Value {
        let v = util::version_of(sess.vars.get("rel").and_then(|r| r["tag"].as_str()).unwrap_or(""));
        json!({"headline": format!("Orbit {v} is ready."),
               "body": "Orbit restarts on it: its window closes and opens again on the new version, in the same place. \
                        (If you close Orbit instead, it switches the next time it starts.)",
               "next": [{"id": "restart", "label": "restart orbit", "kind": "restart", "primary": true}]})
    }
}

fn writable(dir: &Path) -> bool {
    let probe = dir.join(format!(".orbit-write-check-{}", util::new_id()));
    let ok = std::fs::write(&probe, b"").is_ok();
    let _ = std::fs::remove_file(&probe);
    ok
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!("orbit-test-{}", util::new_id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn a_waiting_update_switches_in_place() {
        let d = tmp();
        let exe = d.join("orbit-linux-x86_64");
        std::fs::write(&exe, "old").unwrap();
        assert_eq!(waiting(Some(&exe)), "");
        std::fs::write(beside(&exe, ".new"), "new").unwrap();
        assert_eq!(waiting(Some(&exe)), ""); // not checked yet: never switched to
        std::fs::write(beside(&exe, ".new.ok"), "orbit 9.9.9\n").unwrap();
        assert_eq!(waiting(Some(&exe)), "9.9.9");
        assert_eq!(switch(Some(&exe)).unwrap(), Some(exe.clone()));
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "new");
        assert!(cleanup_old(Some(&exe)));
        let left: Vec<_> = std::fs::read_dir(&d).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(left, vec![std::ffi::OsString::from("orbit-linux-x86_64")]);
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn a_copy_named_with_its_version_takes_the_plain_name() {
        let d = tmp();
        let exe = d.join("orbit-v0.3.4-linux-x86_64");
        std::fs::write(&exe, "old").unwrap();
        std::fs::write(beside(&exe, ".new"), "new").unwrap();
        std::fs::write(beside(&exe, ".new.ok"), "orbit 9.9.9\n").unwrap();
        let plain = d.join("orbit-linux-x86_64");
        assert_eq!(switch(Some(&exe)).unwrap(), Some(plain.clone()));
        assert_eq!(std::fs::read_to_string(&plain).unwrap(), "new");
        assert!(!exe.exists());
        assert!(cleanup_old(Some(&plain)));
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn mac_apps_use_hidden_app_folders() {
        let app = Path::new("/Applications/Orbit.app");
        assert_eq!(unit(&app.join("Contents/MacOS/Orbit")), app);
        assert_eq!(beside(app, ".new"), Path::new("/Applications/.Orbit-new.app"));
        assert_eq!(beside(app, ".old"), Path::new("/Applications/.Orbit-old.app"));
        assert_eq!(settled(app), app);
    }
}
