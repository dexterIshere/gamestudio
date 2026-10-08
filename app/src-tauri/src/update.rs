//! The update: the application follows the studio's code, at startup and while running.
//!
//! Three things can lag behind the repository:
//!   - **the binary**: a front or shell source newer than it, or the venv older
//!     than `pyproject.toml`. The rule is that of `make studio`, written once
//!     in the Makefile (`update-check`): copying it here would let it drift;
//!   - **the process**: the binary was rebuilt on disk since launch (a
//!     `make studio` alongside), and what runs is the old one;
//!   - **the server**: a Python file changed since it started. The package is
//!     installed in editable mode: restarting it is enough.
//!
//! The rebuild (`make update-build`) is followed line by line: each phase
//! (venv, front, shell) is recognized by what it prints, and its share of the
//! gauge advances with the time it took last time
//! (`data/run/update-durations.json`). Neither Vite nor a single-crate Rust
//! build reports progress: measured time is the only honest estimate, and a
//! phase never goes past 95 % of its share before finishing.
//!
//! At startup, a lag opens the update window instead of the other two, without
//! a server; it restarts on its own once the build is done. While running, the
//! rail's "Update" button builds while the studio stays open, and the user
//! picks the moment to restart, since restarting interrupts the Chats agents.
//!
//! None of this in development (`tauri dev`): Vite hot-reloads the front, and
//! the debug binary is not the one `make studio` builds. Nor without a
//! Makefile at the root: it is what knows how to rebuild.

use std::collections::VecDeque;
use std::io::BufRead;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};

use tauri::{AppHandle, Emitter};

/// Set by the relauncher: the process it launches was just updated, or the
/// update just failed. Either way, do not try again at startup: a source dated
/// in the future or a broken build would otherwise loop the application.
pub const SKIP_ENV: &str = "GAMESTUDIO_SKIP_UPDATE";

/// The binary as it was at launch: its path, and its date.
///
/// The path is recorded at once: once the file is replaced, Linux returns
/// `current_exe()` suffixed with "(deleted)", unusable to relaunch.
struct Launch {
    exe: PathBuf,
    date: Option<SystemTime>,
}

static LAUNCH: OnceLock<Launch> = OnceLock::new();

/// To call first of all: records this process's binary.
pub fn record_launch() {
    let exe = std::env::current_exe().unwrap_or_default();
    let date = modified_at(&exe);
    let _ = LAUNCH.set(Launch { exe, date });
}

/// The binary to relaunch.
pub fn executable() -> PathBuf {
    LAUNCH
        .get()
        .map(|launch| launch.exe.clone())
        .or_else(|| std::env::current_exe().ok())
        .unwrap_or_default()
}

/// Is the repository's Makefile there? It rebuilds (`update-build`) and
/// relaunches (`studio`): without it, no update and no relaunch through `make`.
pub fn has_makefile(root: &Path) -> bool {
    root.join("Makefile").is_file()
}

/// Does updating make sense here?
pub fn active(root: &Path) -> bool {
    !cfg!(debug_assertions) && has_makefile(root)
}

/// What lags behind. All `false` when it cannot be told: updating inactive
/// here, or `make` not answering.
#[derive(Default)]
pub struct Outdated {
    /// The binary needs a rebuild, or was rebuilt without this process being restarted.
    pub app: bool,
    /// The server the shell launched runs Python code changed since.
    pub server: bool,
}

impl Outdated {
    pub fn json(&self) -> serde_json::Value {
        serde_json::json!({ "app": self.app, "server": self.server })
    }
}

/// What the Makefile says to rebuild: `venv`, `front`, `app`, or nothing.
///
/// `None` if it does not answer: not knowing is no reason to rebuild.
pub fn to_rebuild(root: &Path) -> Option<Vec<String>> {
    let output = Command::new("make")
        .args(["-s", "-C"])
        .arg(root)
        .arg("update-check")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&output.stdout);
    let mut words = text.split_whitespace();
    match words.next()? {
        "up-to-date" => Some(Vec::new()),
        "rebuild" => Some(words.map(str::to_string).collect()),
        _ => None,
    }
}

/// Is the binary up to date with its sources, according to the Makefile?
pub fn sources_up_to_date(root: &Path) -> Option<bool> {
    to_rebuild(root).map(|remaining| remaining.is_empty())
}

/// The binary on disk is no longer the one running.
fn binary_replaced() -> bool {
    let Some(launch) = LAUNCH.get() else {
        return false;
    };
    match (launch.date, modified_at(&launch.exe)) {
        (Some(before), Some(now)) => now != before,
        _ => false,
    }
}

/// Has a studio Python file changed since `since`?
fn python_changed(root: &Path, since: SystemTime) -> bool {
    fn walk(dir: &Path, since: SystemTime) -> bool {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return false;
        };
        entries.flatten().any(|entry| {
            let path = entry.path();
            if path.is_dir() {
                return path.file_name().is_some_and(|name| name != "__pycache__")
                    && walk(&path, since);
            }
            path.extension().is_some_and(|ext| ext == "py")
                && modified_at(&path).is_some_and(|date| date > since)
        })
    }
    walk(&root.join("src").join("gamestudio"), since)
}

/// The full state, for the rail's button.
///
/// `server_started` is when the shell launched the server: the shell always
/// launches its own (see `server.rs`), and that is the one the restart will
/// relaunch.
pub fn outdated(root: &Path, server_started: SystemTime) -> Outdated {
    if !active(root) {
        return Outdated::default();
    }
    Outdated {
        app: binary_replaced() || sources_up_to_date(root) == Some(false),
        server: python_changed(root, server_started),
    }
}

/// At startup: must the studio update before opening anything?
pub fn needed_at_startup(root: &Path) -> bool {
    active(root) && std::env::var_os(SKIP_ENV).is_none() && sources_up_to_date(root) == Some(false)
}

fn modified_at(path: &Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|meta| meta.modified()).ok()
}

/* -------------------------------------------------------------------- build */

/// A build phase, recognized by what it prints.
struct Phase {
    id: &'static str,
    label: &'static str,
    /// Expected duration, in seconds: last time's, otherwise an order of
    /// magnitude. It is the phase's weight in the gauge.
    expected: f64,
    /// When it started, and how long it took once finished.
    started: Option<Instant>,
    duration: Option<f64>,
}

impl Phase {
    fn new(id: &'static str, label: &'static str, expected: f64) -> Self {
        Self { id, label, expected, started: None, duration: None }
    }

    fn state(&self) -> &'static str {
        match (self.started, self.duration) {
            (_, Some(_)) => "done",
            (Some(_), None) => "running",
            _ => "pending",
        }
    }

    /// Completed share of the phase, from 0 to 1.
    fn progress(&self) -> f64 {
        match (self.started, self.duration) {
            (_, Some(_)) => 1.0,
            (Some(started), None) => (started.elapsed().as_secs_f64() / self.expected).min(0.95),
            _ => 0.0,
        }
    }

    /// Does the line open this phase? The marks are the first lines each stage
    /// writes: the command echoed by `make` for pip, the Makefile's
    /// announcement or Vite's header for the front, the Makefile's
    /// announcement or the first compiled crate for the shell.
    fn opened_by(&self, line: &str) -> bool {
        let line = line.trim_start();
        match self.id {
            "venv" => line.contains("pip install"),
            "front" => {
                line.starts_with("building the interface")
                    || line.contains("beforeBuildCommand")
                    || line.starts_with("vite v")
            }
            "rust" => {
                line.starts_with("building the application")
                    || line.starts_with("Compiling ")
                    || line.contains("Running `cargo")
            }
            _ => false,
        }
    }
}

/// Default expected durations: a first measurement replaces them.
fn default_expected(id: &str) -> f64 {
    match id {
        "venv" => 20.0,
        "front" => 15.0,
        _ => 120.0,
    }
}

/// A build's tracking: what the update window shows.
#[derive(Default)]
struct Tracker {
    /// `idle`, `running`, `done` or `failed`.
    state: &'static str,
    phases: Vec<Phase>,
    started: Option<Instant>,
    finished: Option<f64>,
    line: String,
    tail: VecDeque<String>,
    error: Option<String>,
}

/// Output lines kept to diagnose a failure.
const TAIL: usize = 60;

impl Tracker {
    fn json(&self) -> serde_json::Value {
        let total: f64 = self.phases.iter().map(|phase| phase.expected).sum();
        let progress = match self.state {
            "done" => 1.0,
            _ if total > 0.0 => {
                self.phases.iter().map(|phase| phase.expected * phase.progress()).sum::<f64>()
                    / total
            }
            _ => 0.0,
        };
        let elapsed = self
            .finished
            .or_else(|| self.started.map(|started| started.elapsed().as_secs_f64()))
            .unwrap_or(0.0);
        serde_json::json!({
            "state": if self.state.is_empty() { "idle" } else { self.state },
            "progress": progress,
            "phases": self.phases.iter().map(|phase| serde_json::json!({
                "id": phase.id,
                "label": phase.label,
                "state": phase.state(),
            })).collect::<Vec<_>>(),
            "line": self.line,
            "tail": self.tail,
            "elapsed": elapsed,
            "error": self.error,
        })
    }

    /// An output line: it may advance the phase, and it is displayed.
    fn read_line(&mut self, line: &str) {
        let line = strip_escapes(line);
        let line = line.trim_end();
        if line.trim().is_empty() {
            return;
        }
        // Only move forward: a finished phase does not reopen, and a mark of a
        // phase already passed (a late "Compiling") does not move the gauge
        // back.
        let current = self.phases.iter().rposition(|phase| phase.started.is_some());
        let next_phase = self
            .phases
            .iter()
            .enumerate()
            .skip(current.map_or(0, |index| index + 1))
            .find(|(_, phase)| phase.opened_by(line))
            .map(|(index, _)| index);
        if let Some(index) = next_phase {
            let now = Instant::now();
            for phase in &mut self.phases[..index] {
                // A skipped phase (nothing to do for it) does not count.
                if phase.duration.is_none() {
                    phase.duration = Some(phase.started.map_or(0.0, |started| {
                        now.duration_since(started).as_secs_f64()
                    }));
                }
            }
            self.phases[index].started = Some(now);
        }
        self.line = line.to_string();
        if self.tail.len() == TAIL {
            self.tail.pop_front();
        }
        self.tail.push_back(line.to_string());
    }

    fn finish(&mut self, succeeded: bool, error: Option<String>) {
        let now = Instant::now();
        if succeeded {
            for phase in &mut self.phases {
                if phase.duration.is_none() {
                    phase.duration = Some(phase.started.map_or(0.0, |started| {
                        now.duration_since(started).as_secs_f64()
                    }));
                }
            }
        }
        self.finished = self.started.map(|started| started.elapsed().as_secs_f64());
        self.state = if succeeded { "done" } else { "failed" };
        self.error = error;
    }
}

/// The running build, shared between the windows and the thread following it.
#[derive(Default)]
pub struct Build {
    tracker: Mutex<Tracker>,
}

/// The event the windows listen to for their gauge.
const EVENT: &str = "update-progress";

impl Build {
    pub fn json(&self) -> serde_json::Value {
        self.tracker.lock().map(|tracker| tracker.json()).unwrap_or_default()
    }

    /// Starts the build, unless it already runs: two `make` on the same
    /// targets would step on each other.
    pub fn start(self: &Arc<Self>, app: AppHandle, root: PathBuf) {
        {
            let Ok(mut tracker) = self.tracker.lock() else {
                return;
            };
            if tracker.state == "running" {
                return;
            }
            *tracker = Tracker { state: "running", started: Some(Instant::now()), ..Tracker::default() };
            tracker.line = "Checking…".to_string();
        }
        let build = Arc::clone(self);
        std::thread::spawn(move || build.run(&app, &root));
    }

    fn publish(&self, app: &AppHandle) {
        let _ = app.emit(EVENT, self.json());
    }

    fn run(self: &Arc<Self>, app: &AppHandle, root: &Path) {
        self.publish(app);
        // The phases to come: only what the Makefile says lags behind. None at
        // all when only the server, or the process, is stale: the build then
        // ends at once, and only the restart remains.
        let remaining = to_rebuild(root).unwrap_or_else(|| vec!["venv".into(), "app".into()]);
        let durations = read_durations(root);
        let expected = |id: &str| {
            durations.get(id).and_then(|value| value.as_f64()).unwrap_or(default_expected(id))
        };
        let mut phases = Vec::new();
        if remaining.iter().any(|word| word == "venv") {
            phases.push(Phase::new("venv", "Python dependencies", expected("venv")));
        }
        // `front` without `app` does not happen: the shell embeds the front.
        // The reverse does: only the shell changed, Vite does not run.
        if remaining.iter().any(|word| word == "front") {
            phases.push(Phase::new("front", "Interface", expected("front")));
        }
        if remaining.iter().any(|word| word == "app") {
            phases.push(Phase::new("rust", "Application", expected("rust")));
        }
        if let Ok(mut tracker) = self.tracker.lock() {
            tracker.phases = phases;
        }

        let outcome = self.follow_make(app, root);
        if let Ok(mut tracker) = self.tracker.lock() {
            match outcome {
                Ok(()) => {
                    tracker.finish(true, None);
                    write_durations(root, &tracker.phases);
                }
                Err(error) => tracker.finish(false, Some(error)),
            }
        }
        self.publish(app);
    }

    /// `make update-build`, its output read line by line, the gauge emitted
    /// four times per second so that it also moves during the long silences
    /// of linking.
    fn follow_make(self: &Arc<Self>, app: &AppHandle, root: &Path) -> Result<(), String> {
        // `sh` to join both outputs: cargo writes its progress to stderr, Vite
        // and `make` to stdout.
        let mut child = Command::new("sh")
            .arg("-c")
            .arg(r#"exec make -C "$1" update-build 2>&1"#)
            .arg("gamestudio-update")
            .arg(root)
            .current_dir(root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("make not found: {error}"))?;
        let output = child.stdout.take().ok_or("make output unreadable")?;

        let this = Arc::clone(self);
        let reader = std::thread::spawn(move || {
            for line in std::io::BufReader::new(output).split(b'\n').map_while(Result::ok) {
                if let Ok(mut tracker) = this.tracker.lock() {
                    tracker.read_line(&String::from_utf8_lossy(&line));
                }
            }
        });

        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) => {
                    self.publish(app);
                    std::thread::sleep(Duration::from_millis(250));
                }
                Err(error) => return Err(error.to_string()),
            }
        };
        let _ = reader.join();
        if status.success() {
            Ok(())
        } else {
            Err(format!("the build failed ({status})"))
        }
    }
}

/// Strips ANSI escape sequences: colors and cursor moves make no sense in a
/// line of text.
fn strip_escapes(line: &str) -> String {
    let mut clean = String::with_capacity(line.len());
    let mut chars = line.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '\u{1b}' {
            if chars.peek() == Some(&'[') {
                chars.next();
                for next_char in chars.by_ref() {
                    if next_char.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        // A progress bar redrawn with `\r`: only the last state counts.
        if ch == '\r' {
            clean.clear();
            continue;
        }
        clean.push(ch);
    }
    clean
}

fn durations_file(root: &Path) -> PathBuf {
    crate::server::run_dir(root).join("update-durations.json")
}

fn read_durations(root: &Path) -> serde_json::Map<String, serde_json::Value> {
    std::fs::read_to_string(durations_file(root))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

/// Records the duration of each phase actually run, for the next gauge.
fn write_durations(root: &Path, phases: &[Phase]) {
    let mut durations = read_durations(root);
    for phase in phases {
        if let Some(duration) = phase.duration.filter(|duration| *duration > 0.5) {
            durations.insert(phase.id.to_string(), serde_json::json!(duration));
        }
    }
    let path = durations_file(root);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(path, serde_json::Value::Object(durations).to_string());
}
