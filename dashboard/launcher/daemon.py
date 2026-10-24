"""Privileged launcher daemon.

This is the only component of the dashboard that runs as root, and it exists
so that the web API never has to. Its entire job is: start one experiment,
report on it, stop it, and optionally hand it a link-failure instruction.

    sudo python3 -m dashboard.launcher.daemon --allow-uid 1000

What it will do
---------------
Run exactly one command, assembled by itself from its own constants:

    <python> -m experiments.run_experiment --variant X --workload Y
             --seed N --duration N [--idle-timeout N] --tag ui

What it will not do
-------------------
* Execute anything a client names. The client sends a variant letter, a
  workload name and three integers; nothing it sends becomes a path, a
  filename or a command fragment. ``shell=False`` throughout.
* Accept connections from anyone. The socket is mode 0660 in a 0750
  directory, and every connection's peer credentials are checked with
  SO_PEERCRED against an allowed uid. Filesystem permissions alone would be
  enough on a single-user machine; the credential check is there so that a
  misconfigured directory is not a privilege escalation.
* Kill anything it did not start. Stop operations resolve an experiment id to
  a process group this daemon created and signal that, by id. There is no
  code path that takes a pid or a pattern from a client.
* Touch results. It never deletes or rewrites a run directory.

Why a daemon rather than a sudoers rule
---------------------------------------
A NOPASSWD sudoers entry for the runner would also work and would be simpler
to install, but it grants the web user the ability to run that command with
*any* arguments sudo's rule permits, and tightening a sudoers command rule to
specific argument values is notoriously easy to get subtly wrong. A daemon
moves the trust boundary into code that can be read, tested and reasoned
about, and the attack surface is one socket with five operations.
"""

from __future__ import annotations

import argparse
import errno
import grp
import json
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dashboard.launcher import protocol  # noqa: E402
from dashboard.launcher.protocol import ProtocolError  # noqa: E402

LOG = logging.getLogger("sdn-rl-launcher")

#: Grace period after an experiment's nominal duration before the daemon
#: considers it hung and terminates it. The runner needs time for Mininet
#: teardown, telemetry flush and manifest writing, so this is generous.
WATCHDOG_GRACE_S = 240

#: How long to wait for a cooperative shutdown before escalating.
STOP_TIMEOUT_S = 40

#: Where per-experiment logs are written (owned by root, world-readable so the
#: unprivileged API can show them).
LOG_DIR = os.path.join(PROJECT_ROOT, "results", "launcher_logs")


@dataclass
class Experiment:
    experiment_id: str
    params: Dict[str, Any]
    pid: int
    pgid: int
    started_at: float
    log_path: str
    argv: List[str]
    deadline: float
    #: The Popen handle. Exit status is read through this rather than with a
    #: bare os.waitpid: waitpid is process-global, so two daemon instances in
    #: one process (as the test suite creates) would race to reap each other's
    #: children and each would see the other's experiment vanish. Popen only
    #: ever reaps the child it owns.
    proc: Optional[subprocess.Popen] = None
    finished_at: Optional[float] = None
    returncode: Optional[int] = None
    stopped_by: Optional[str] = None
    run_dir: Optional[str] = None
    injections: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "params": self.params,
            "pid": self.pid,
            "started_at": self.started_at,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1),
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "stopped_by": self.stopped_by,
            "running": self.running,
            "log_path": self.log_path,
            "run_dir": self.run_dir,
            "command": " ".join(self.argv),
            "injections": self.injections,
        }


class LauncherDaemon:
    def __init__(self, allow_uid: int, socket_path: str, allow_gid: Optional[int] = None):
        self.allow_uid = allow_uid
        self.allow_gid = allow_gid
        self.socket_path = socket_path
        self._lock = threading.Lock()
        self._current: Optional[Experiment] = None
        self._history: List[Experiment] = []
        self._stopping = threading.Event()
        self._server: Optional[socket.socket] = None
        # Shutdown can be requested from the serving thread's `finally` and
        # from whoever asked it to stop. Running the teardown twice races on
        # the socket and on terminating the child, so it happens once.
        self._cleaned = threading.Event()
        # Set once the socket is bound AND listening. The socket *file* appears
        # at bind() time, so waiting for the path to exist is not enough: a
        # client connecting between bind() and listen() is refused. Anything
        # hosting this daemon should wait on this instead.
        self._ready = threading.Event()

    # ------------------------------------------------------------- lifecycle
    def _prepare_socket(self) -> socket.socket:
        directory = os.path.dirname(self.socket_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
            # 0750: only root and the allowed group may even traverse it.
            try:
                os.chmod(directory, 0o750)
                if self.allow_gid is not None:
                    os.chown(directory, 0, self.allow_gid)
            except OSError as exc:
                LOG.warning("could not tighten %s: %s", directory, exc)

        if os.path.exists(self.socket_path):
            # A stale socket from a previous run; removing it is safe because
            # we only ever bind our own well-known path.
            try:
                os.unlink(self.socket_path)
            except OSError as exc:
                raise SystemExit(f"cannot remove stale socket {self.socket_path}: {exc}")

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        os.chmod(self.socket_path, 0o660)
        if self.allow_gid is not None:
            try:
                os.chown(self.socket_path, 0, self.allow_gid)
            except OSError as exc:
                LOG.warning("could not chown socket: %s", exc)
        server.listen(8)
        return server

    def serve_forever(self) -> int:
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            os.chmod(LOG_DIR, 0o755)
        except OSError:
            pass

        self._server = self._prepare_socket()
        self._ready.set()
        LOG.info("listening on %s (allow uid=%s gid=%s)",
                 self.socket_path, self.allow_uid, self.allow_gid)

        def _signal_handler(signum, _frame):
            LOG.info("signal %s received; shutting down", signum)
            self._stopping.set()
            try:
                if self._server:
                    self._server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        # Signal handlers can only be installed from the main thread. The
        # daemon normally owns the process, but it is also hosted in a thread
        # by the test suite, where shutdown is driven directly instead.
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)
        else:
            LOG.debug("not on the main thread; skipping signal handlers")

        watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        watchdog.start()

        try:
            while not self._stopping.is_set():
                try:
                    conn, _ = self._server.accept()
                except OSError as exc:
                    if self._stopping.is_set() or exc.errno in (errno.EBADF, errno.EINVAL):
                        break
                    LOG.exception("accept failed")
                    continue
                threading.Thread(
                    target=self._handle_connection, args=(conn,), daemon=True
                ).start()
        finally:
            self._cleanup()
        return 0

    def _cleanup(self) -> None:
        if self._cleaned.is_set():
            return
        self._cleaned.set()
        self._stopping.set()

        # The daemon exiting must not orphan a running experiment: an orphaned
        # runner would keep Mininet and TCP 6653 held with nothing tracking it.
        with self._lock:
            current = self._current
        if current is not None and current.running:
            LOG.info("stopping experiment %s before exit", current.experiment_id)
            self._terminate(current, reason="daemon_shutdown")
        try:
            if self._server:
                self._server.close()
        except OSError:
            pass
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except OSError:
            pass

    # ------------------------------------------------------------ connection
    def _peer_uid(self, conn: socket.socket) -> Optional[int]:
        try:
            raw = conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            _pid, uid, _gid = struct.unpack("3i", raw)
            return uid
        except (OSError, struct.error):
            return None

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(15)
            uid = self._peer_uid(conn)
            # Defence in depth: the socket permissions should already prevent
            # this, but a privileged service should not rely on one control.
            if uid is None or (uid != self.allow_uid and uid != 0):
                LOG.warning("rejecting connection from uid=%s", uid)
                conn.sendall(protocol.encode(
                    protocol.error("connection refused: peer not permitted")
                ))
                return

            data = self._read_request(conn)
            if data is None:
                return
            try:
                request = protocol.validate_request(protocol.decode(data))
                response = self._dispatch(request)
            except ProtocolError as exc:
                response = protocol.error(str(exc))
            except Exception as exc:  # never leak a traceback to the client
                LOG.exception("request failed")
                response = protocol.error(f"internal error: {type(exc).__name__}")
            conn.sendall(protocol.encode(response))
        except (OSError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _read_request(self, conn: socket.socket) -> Optional[bytes]:
        chunks: List[bytes] = []
        total = 0
        while True:
            try:
                chunk = conn.recv(4096)
            except (OSError, socket.timeout):
                return None
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > protocol.MAX_REQUEST_BYTES:
                conn.sendall(protocol.encode(protocol.error("request too large")))
                return None
            if b"\n" in chunk:
                break
        return b"".join(chunks).strip() or None

    # -------------------------------------------------------------- dispatch
    def _dispatch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        op = request["op"]
        if op == "ping":
            return protocol.ok(pong=True, pid=os.getpid(),
                               project_root=PROJECT_ROOT)
        if op == "status":
            return self._op_status()
        if op == "start":
            return self._op_start(request)
        if op == "stop":
            return self._op_stop(request)
        if op == "inject":
            return self._op_inject(request)
        raise ProtocolError(f"unsupported op {op!r}")

    def _reap_locked(self) -> None:
        current = self._current
        if current is None or not current.running or current.proc is None:
            return
        returncode = current.proc.poll()
        if returncode is None:
            return  # still running
        current.finished_at = time.time()
        current.returncode = returncode
        LOG.info("experiment %s finished rc=%s",
                 current.experiment_id, current.returncode)
        self._history.append(current)
        self._current = None

    def _op_status(self) -> Dict[str, Any]:
        with self._lock:
            self._reap_locked()
            current = self._current.as_dict() if self._current else None
            history = [e.as_dict() for e in self._history[-10:]]
        return protocol.ok(current=current, history=history,
                           project_root=PROJECT_ROOT)

    def _op_start(self, request: Dict[str, Any]) -> Dict[str, Any]:
        params = protocol.validate_start(request)

        with self._lock:
            self._reap_locked()
            if self._current is not None:
                raise ProtocolError(
                    "an experiment is already running "
                    f"({self._current.experiment_id}); stop it first"
                )

            # An experiment started outside the daemon (a terminal matrix
            # sweep) holds the same exclusive resources. Refuse rather than
            # corrupt both.
            external = _external_experiment()
            if external:
                raise ProtocolError(
                    "an experiment is already running outside the dashboard "
                    f"(pid {external}); refusing to start a second one"
                )

            experiment_id = "exp-{}-{}".format(
                time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8]
            )
            argv = _build_argv(params)
            log_path = os.path.join(LOG_DIR, f"{experiment_id}.log")

            env = dict(os.environ)
            env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"
            env["SDNRL_EXPERIMENT_ID"] = experiment_id

            try:
                log_fh = open(log_path, "ab", buffering=0)
            except OSError as exc:
                raise ProtocolError(f"cannot open log file: {exc}")

            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=PROJECT_ROOT,
                    env=env,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    shell=False,
                    start_new_session=True,
                )
            except OSError as exc:
                log_fh.close()
                raise ProtocolError(f"failed to launch runner: {exc}")
            finally:
                try:
                    log_fh.close()
                except OSError:
                    pass

            try:
                pgid = os.getpgid(proc.pid)
            except OSError:
                pgid = proc.pid

            experiment = Experiment(
                experiment_id=experiment_id,
                params=params,
                pid=proc.pid,
                pgid=pgid,
                started_at=time.time(),
                log_path=log_path,
                argv=argv,
                deadline=time.time() + params["duration_s"] + WATCHDOG_GRACE_S,
                proc=proc,
            )
            self._current = experiment
            LOG.info("started %s pid=%s: %s", experiment_id, proc.pid, " ".join(argv))

        # Give the runner a moment to fail loudly (bad environment, port in
        # use) so the UI gets a real error instead of a run that dies silently.
        time.sleep(1.5)
        with self._lock:
            self._reap_locked()
            if self._current is None:
                failed = self._history[-1] if self._history else None
                tail = _tail(failed.log_path, 20) if failed else ""
                raise ProtocolError(
                    "the runner exited immediately "
                    f"(exit code {failed.returncode if failed else '?'}). "
                    + (f"Last output: {tail}" if tail else "")
                )
            return protocol.ok(experiment=self._current.as_dict())

    def _op_stop(self, request: Dict[str, Any]) -> Dict[str, Any]:
        experiment_id = protocol.validate_experiment_id(
            request.get("experiment_id")
        )
        with self._lock:
            self._reap_locked()
            current = self._current
            if current is None:
                raise ProtocolError("no experiment is running")
            # Stop is addressed by id, never by pid, and never by pattern.
            if current.experiment_id != experiment_id:
                raise ProtocolError(
                    "experiment_id does not match the running experiment"
                )
        result = self._terminate(current, reason="user")
        return protocol.ok(experiment=result)

    def _op_inject(self, request: Dict[str, Any]) -> Dict[str, Any]:
        experiment_id, action, link = protocol.validate_inject(request)
        with self._lock:
            self._reap_locked()
            current = self._current
            if current is None or current.experiment_id != experiment_id:
                raise ProtocolError(
                    "that experiment is not running"
                )
            run_dir = current.run_dir or _discover_run_dir(current.started_at)
            current.run_dir = run_dir

        if not run_dir:
            raise ProtocolError(
                "the run directory does not exist yet; try again in a moment"
            )

        # The daemon does not touch Mininet. It appends an instruction to a
        # control file that the runner -- which owns the Mininet session --
        # polls and applies. The runner re-validates the link against the
        # topology before acting.
        command = {
            "id": uuid.uuid4().hex[:8],
            "t": time.time(),
            "action": action,
            "link": link,
        }
        control_path = os.path.join(run_dir, "control.jsonl")
        try:
            with open(control_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(command) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(control_path, 0o644)
        except OSError as exc:
            raise ProtocolError(f"cannot write control file: {exc}")

        with self._lock:
            if self._current is not None:
                self._current.injections.append(command)
        LOG.info("queued %s on %s for %s", action, link, experiment_id)
        return protocol.ok(command=command, run_dir=run_dir)

    # ------------------------------------------------------------ terminate
    def _terminate(self, experiment: Experiment, reason: str) -> Dict[str, Any]:
        """Stop an experiment we started, cooperatively if at all possible.

        SIGTERM to the process group first, with a long wait: the runner's
        teardown stops Mininet, flushes telemetry and writes the manifest.
        Killing it outright would leave a half-written run directory and could
        orphan the controller holding TCP 6653.
        """
        escalated = False
        try:
            os.killpg(experiment.pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            LOG.warning("SIGTERM to pgid %s failed: %s", experiment.pgid, exc)

        deadline = time.time() + STOP_TIMEOUT_S
        while time.time() < deadline:
            with self._lock:
                self._reap_locked()
                if self._current is None or self._current.experiment_id != experiment.experiment_id:
                    break
            time.sleep(0.4)
        else:
            escalated = True
            LOG.warning("escalating to SIGKILL for %s", experiment.experiment_id)
            try:
                os.killpg(experiment.pgid, signal.SIGKILL)
            except OSError:
                pass
            time.sleep(1.0)
            with self._lock:
                self._reap_locked()

        with self._lock:
            if self._current is not None and \
                    self._current.experiment_id == experiment.experiment_id:
                # Reaping did not see it exit; record what we know.
                self._current.finished_at = time.time()
                self._current.stopped_by = reason
                self._history.append(self._current)
                self._current = None
            else:
                for item in reversed(self._history):
                    if item.experiment_id == experiment.experiment_id:
                        item.stopped_by = reason
                        break

        result = experiment.as_dict()
        result["escalated_to_sigkill"] = escalated
        result["stopped_by"] = reason
        return result

    # ------------------------------------------------------------- watchdog
    def _watchdog_loop(self) -> None:
        while not self._stopping.wait(10.0):
            with self._lock:
                self._reap_locked()
                current = self._current
                expired = (
                    current is not None
                    and current.running
                    and time.time() > current.deadline
                )
            if expired and current is not None:
                LOG.warning(
                    "experiment %s exceeded its deadline; terminating",
                    current.experiment_id,
                )
                self._terminate(current, reason="watchdog_timeout")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _build_argv(params: Dict[str, Any]) -> List[str]:
    """Assemble the runner command. Built here, from constants, never from input."""
    argv = [
        sys.executable,
        "-m", "experiments.run_experiment",
        "--variant", params["variant"],
        "--workload", params["workload"],
        "--seed", str(params["seed"]),
        "--duration", str(params["duration_s"]),
        "--tag", "ui",
    ]
    if params.get("idle_timeout") is not None:
        argv += ["--idle-timeout", str(params["idle_timeout"])]
    return argv


def _external_experiment() -> Optional[int]:
    """Pid of an experiment runner this daemon did not start, if any."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,cmd"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    mine = os.getpid()
    for line in out.splitlines()[1:]:
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if pid == mine or "ps -eo" in cmd:
            continue
        if "experiments.run_experiment" in cmd or "experiments.run_diagnostics" in cmd:
            return pid
    return None


def _discover_run_dir(started_after: float) -> Optional[str]:
    """Newest run directory created after the experiment started."""
    runs = os.path.join(PROJECT_ROOT, "results", "runs")
    best: Optional[str] = None
    best_mtime = started_after - 5  # small tolerance for clock jitter
    try:
        entries = os.listdir(runs)
    except OSError:
        return None
    for entry in entries:
        path = os.path.join(runs, entry)
        if not os.path.isdir(path):
            continue
        config = os.path.join(path, "config.json")
        if not os.path.exists(config):
            continue
        try:
            mtime = os.path.getmtime(config)
        except OSError:
            continue
        if mtime >= best_mtime:
            best_mtime = mtime
            best = path
    return best


def _tail(path: str, lines: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = [ln for ln in fh.readlines() if "sch_htb" not in ln]
        return " / ".join(ln.strip() for ln in content[-lines:] if ln.strip())[:400]
    except OSError:
        return ""


def _resolve_uid(value: Optional[str]) -> int:
    if value is None:
        # Default to whoever owns the project checkout: that is the developer
        # the dashboard runs as.
        return os.stat(PROJECT_ROOT).st_uid
    try:
        return int(value)
    except ValueError:
        import pwd
        try:
            return pwd.getpwnam(value).pw_uid
        except KeyError:
            raise SystemExit(f"unknown user {value!r}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-uid", default=None,
                        help="uid or username permitted to connect "
                             "(default: owner of the project directory)")
    parser.add_argument("--socket", default=protocol.socket_path())
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if os.geteuid() != 0:
        print(
            "ERROR: the launcher daemon must run as root -- that is its "
            "entire purpose. Start it with:\n"
            "  sudo python3 -m dashboard.launcher.daemon",
            file=sys.stderr,
        )
        return 2

    allow_uid = _resolve_uid(args.allow_uid)
    try:
        allow_gid = os.stat(PROJECT_ROOT).st_gid
    except OSError:
        allow_gid = None

    daemon = LauncherDaemon(allow_uid=allow_uid, socket_path=args.socket,
                            allow_gid=allow_gid)
    return daemon.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
