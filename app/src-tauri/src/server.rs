//! Lifecycle of the Python server.
//!
//! All of the studio's work is in Python: Runware, Blender, the SQLite
//! database, the job queue. The shell only launches it on a free port, waits
//! for it to answer, and stops it on exit, so that no process outlives the
//! window and no terminal is needed.
//!
//! It never attaches to an already running server: a port that answers does
//! not say who listens, and the front hands the API token to whoever answers.
//! A `gamestudio serve` running alongside keeps its port; the shell launches
//! its own on another.

use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime};

/// Time given to the server to open its port. The first start imports numpy,
/// Pillow and FastAPI: a few seconds are normal.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);

/// The variable that forces the token, on this side as on Python's
/// (`api/auth.py`, `TOKEN_ENV`).
const TOKEN_ENV: &str = "GAMESTUDIO_TOKEN";

pub struct Server {
    pub base: String,
    /// The local API token, read the way the server chooses it (see `read_token`).
    ///
    /// The shell does not make it: the server draws it and writes it before
    /// opening its port. A token created here would have to be passed through
    /// the environment, and would be lost to any server launched without the
    /// shell (`make serve`, an agent).
    pub token: String,
    child: Mutex<Option<Child>>,
    /// When the shell launched it: a newer Python file means it runs stale
    /// code (see `update.rs`).
    pub launched: SystemTime,
}

impl Server {
    /// Launches the repository's server on a free port, and waits until it listens.
    pub fn start(root: &Path) -> Result<Self, String> {
        let program = locate(root).ok_or_else(|| {
            format!(
                "the studio server was not found ({}): run `make install` at the \
                 repository root",
                root.join(".venv").display()
            )
        })?;
        let port = free_port()?;

        let mut command = Command::new(&program);
        command
            .arg("serve")
            // The address the front will receive: the server listens there,
            // whatever `GAMESTUDIO_API_HOST` says.
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string())
            .current_dir(root)
            // The shell's root becomes the server's: without it, Python would
            // look for one itself (a `.env` among the ancestors of its working
            // directory), and could read its data, and write its token,
            // somewhere other than where the shell looks.
            .env("GAMESTUDIO_HOME", root)
            // The server's output follows the shell's: the console in
            // development, `data/studio.log` under `make studio`, nowhere from
            // the applications menu; never an open terminal.
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .stdin(Stdio::null());
        bind_to_parent(&mut command);

        let launched = SystemTime::now();
        let child = command
            .spawn()
            .map_err(|error| format!("cannot launch {}: {error}", program.display()))?;

        let mut server = Self {
            base: format!("http://127.0.0.1:{port}"),
            token: String::new(),
            child: Mutex::new(Some(child)),
            launched,
        };
        server.await_ready(port)?;
        server.token = read_token(root)?;
        Ok(server)
    }

    fn await_ready(&self, port: u16) -> Result<(), String> {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while Instant::now() < deadline {
            // A dead process will never answer: saying so at once beats waiting
            // for the deadline.
            if let Ok(mut guard) = self.child.lock() {
                if let Some(child) = guard.as_mut() {
                    if let Ok(Some(status)) = child.try_wait() {
                        return Err(format!("the server stopped ({status})"));
                    }
                }
            }
            if is_listening(port) {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(120));
        }
        Err(format!("the server did not answer on port {port}"))
    }

    /// Stops the server the shell launched.
    pub fn stop(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Ties the server's lifetime to the shell's.
///
/// The clean stop (`Server::stop`) covers closing the window. It does not cover
/// a shell killed by a signal or a crash: the kernel then takes care of it, and
/// no server outlives the window, which is exactly what a desktop application
/// should do.
#[cfg(target_os = "linux")]
fn bind_to_parent(command: &mut Command) {
    use std::os::unix::process::CommandExt;

    // SAFETY: between `fork` and `exec`, only async-signal-safe functions are
    // allowed. `prctl` is one of them.
    unsafe {
        command.pre_exec(|| {
            libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
            Ok(())
        });
    }
}

#[cfg(not(target_os = "linux"))]
fn bind_to_parent(_command: &mut Command) {}

/// A free port, obtained by reserving it then releasing it at once.
fn free_port() -> Result<u16, String> {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("no free port: {error}"))
}

/// Does the port accept a connection?
fn responds(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok()
}

/// Is the port listened to on the loopback, by a process of this user?
///
/// A connection that succeeds says someone is there, not who. Between the
/// moment `free_port` releases the port and the moment the server opens it
/// (the time of its imports), another process could take it, and the front
/// would hand it the token. Under Linux, the kernel's socket table says who
/// owns a listener: only this user's counts, since that user can read the
/// token on disk anyway. Elsewhere, or if the table is unreadable, the
/// connection alone is trusted.
#[cfg(target_os = "linux")]
fn is_listening(port: u16) -> bool {
    let Ok(table) = std::fs::read_to_string("/proc/net/tcp") else {
        return responds(port);
    };
    // `address:port` as the kernel writes them: the address in the machine's
    // byte order, the port as is, both in hexadecimal.
    let wanted = format!("{:08X}:{port:04X}", u32::from_ne_bytes([127, 0, 0, 1]));
    // SAFETY: `getuid` never fails and touches no memory.
    let uid = unsafe { libc::getuid() }.to_string();
    // Columns: 1 local address, 3 state (`0A` = listening), 7 uid.
    let mut listeners = table
        .lines()
        .skip(1)
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .filter(|fields| fields.get(1) == Some(&wanted.as_str()) && fields.get(3) == Some(&"0A"))
        .peekable();
    listeners.peek().is_some() && listeners.all(|fields| fields.get(7) == Some(&uid.as_str()))
}

#[cfg(not(target_os = "linux"))]
fn is_listening(port: u16) -> bool {
    responds(port)
}

/// The studio root: the cloned repository, whose venv holds the server.
///
/// `GAMESTUDIO_HOME` if set (`~` expanded, as Python does), otherwise the first
/// studio repository among the ancestors of the working directory (that of
/// `make studio` and of the applications menu entry), otherwise that of the
/// binary itself, built in the repository's `app/src-tauri/target/`.
///
/// The server receives this root through `GAMESTUDIO_HOME` (see
/// `Server::start`): it does not look for it on its own, and both read the
/// same data.
///
/// Outside a repository, nothing: the studio ships as its repository (clone,
/// `make install`, `make studio`), and its server is the one in the
/// repository's venv.
pub fn root_dir() -> Result<PathBuf, String> {
    if let Some(home) = std::env::var_os("GAMESTUDIO_HOME") {
        let home = home.to_string_lossy().trim().to_string();
        if !home.is_empty() {
            let path = expand_home(&home);
            return if is_studio_repo(&path) {
                Ok(canonical(path))
            } else {
                Err(format!(
                    "GAMESTUDIO_HOME ({}) is not a studio repository",
                    path.display()
                ))
            };
        }
    }
    let start_dir = std::env::current_dir().ok();
    let binary = std::env::current_exe().ok();
    [start_dir, binary]
        .into_iter()
        .flatten()
        .find_map(|path| {
            path
                .ancestors()
                .find(|dir| is_studio_repo(dir))
                .map(Path::to_path_buf)
        })
        .map(canonical)
        .ok_or_else(|| {
            "no studio repository here: the studio runs from its cloned repository \
             (`make install`, then `make studio` at its root), or with \
             GAMESTUDIO_HOME=<repository>"
                .to_string()
        })
}

/// A folder is a studio repository if it holds its Python package and its
/// declaration, which `make install` installs into the venv.
fn is_studio_repo(dir: &Path) -> bool {
    dir.join("pyproject.toml").is_file()
        && dir
            .join("src")
            .join("gamestudio")
            .join("__init__.py")
            .is_file()
}

/// `~` and `~/...` to the home folder, like `Path.expanduser`.
fn expand_home(path: &str) -> PathBuf {
    match (path.strip_prefix('~'), std::env::var_os("HOME")) {
        (Some(rest), Some(home)) if rest.is_empty() || rest.starts_with('/') => {
            PathBuf::from(home).join(rest.trim_start_matches('/'))
        }
        _ => PathBuf::from(path),
    }
}

/// The absolute path, links resolved, like `Path.resolve`: the server receives
/// it, and it no longer depends on the working directory.
fn canonical(path: PathBuf) -> PathBuf {
    std::fs::canonicalize(&path).unwrap_or(path)
}

/// A studio variable, read the way Python reads it (`config.settings`): the
/// environment first, then the root's `.env`, which never overrides a variable
/// already set (`config._load_dotenv`: the first occurrence of a key wins,
/// quotes removed).
///
/// The server inherits the shell's environment and reads the same `.env`: both
/// agree on the token and on the data folder.
fn variable(root: &Path, name: &str) -> Option<String> {
    if let Some(value) = std::env::var_os(name) {
        return Some(value.to_string_lossy().into_owned());
    }
    let text = std::fs::read_to_string(root.join(".env")).ok()?;
    text.lines().find_map(|line| {
        let line = line.trim();
        if line.starts_with('#') {
            return None;
        }
        let (key, value) = line.split_once('=')?;
        (key.trim() == name).then(|| value.trim().trim_matches(['\'', '"']).to_string())
    })
}

/// The studio's `data/run`: what the server and the shell leave there while
/// running, never versioned.
///
/// The same rule as on the Python side (`Settings.run_dir`):
/// `GAMESTUDIO_DATA_DIR` if set (in the environment or the `.env`), otherwise
/// `./data`, a relative path starting from the root.
pub fn run_dir(root: &Path) -> PathBuf {
    let data =
        PathBuf::from(variable(root, "GAMESTUDIO_DATA_DIR").unwrap_or_else(|| "./data".into()));
    let data = if data.is_absolute() {
        data
    } else {
        root.join(data)
    };
    data.join("run")
}

/// The API token, chosen the way `api/auth.py` chooses it: `GAMESTUDIO_TOKEN`
/// if set and not empty, otherwise the file the server wrote before opening
/// its port.
///
/// Failing is the right behaviour: without a token, every call from the front
/// would be refused, and a window that shows nothing without saying why is
/// harder to diagnose than a stop message.
fn read_token(root: &Path) -> Result<String, String> {
    let forced = variable(root, TOKEN_ENV).map(|value| value.trim().to_string());
    if let Some(forced) = forced.filter(|value| !value.is_empty()) {
        return Ok(forced);
    }
    let path = run_dir(root).join("api-token");
    std::fs::read_to_string(&path)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            format!(
                "the API token was not found ({}): the server could not write it",
                path.display()
            )
        })
}

/// The repository's server: the one `make install` puts in its venv.
///
/// No fallback on the PATH: a `gamestudio` found elsewhere would belong to
/// another repository, whose code would match neither this root nor the
/// sources the update watches.
fn locate(root: &Path) -> Option<PathBuf> {
    [
        root.join(".venv").join("bin").join("gamestudio"),
        root.join(".venv").join("Scripts").join("gamestudio.exe"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_file())
}
