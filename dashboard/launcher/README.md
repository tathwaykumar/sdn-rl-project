# Privileged launcher — security model and setup

The dashboard lets you start and stop experiments from the browser. Mininet
needs root; a web API should not have it. This directory holds the component
that resolves that tension.

---

## 1. The shape of the solution

```
browser  ──HTTP──►  FastAPI (your user, unprivileged)
                         │
                         │  Unix socket, 0660, root:yourgroup
                         ▼
                    launcher daemon (root)
                         │  argv list, shell=False
                         ▼
                    experiments/run_experiment.py  ──►  Mininet / OVS
```

The API never gains privilege. It becomes a *client* of a small root daemon
whose entire vocabulary is five operations.

---

## 2. Why a daemon and not a sudoers rule

A `NOPASSWD` entry for the runner would be fewer moving parts, and it is a
legitimate choice. It was rejected here because:

* A sudoers command rule grants the web user the right to run that command
  with whatever arguments the rule's pattern permits. Constraining a sudoers
  rule to *specific argument values* is possible but notoriously easy to get
  subtly wrong, and a mistake is a root shell.
* The trust boundary ends up expressed in sudoers syntax, which cannot be
  unit-tested. Here it is Python with 67 tests against it.
* A sudoers rule is a permanent system configuration change. The daemon is a
  process you start and stop; when it is not running, the capability simply
  does not exist, and the dashboard says so.

**No sudoers file is added or modified by this project.**

---

## 3. What the daemon will and will not do

**Will:** run exactly one command, which it assembles itself:

```
<python> -m experiments.run_experiment --variant X --workload Y
         --seed N --duration N [--idle-timeout N] --tag ui
```

**Will not:**

| | |
|---|---|
| Execute anything the caller names | The client sends a variant letter, a workload name and three integers. Nothing it sends becomes a path, a filename or a command fragment. `shell=False` throughout; there is no shell anywhere in the path. |
| Accept arbitrary connections | Socket is `0660` inside a `0750` directory, and every connection's peer credentials are checked with `SO_PEERCRED` against an allowed uid. Two independent controls, so a misconfigured directory is not an escalation. |
| Kill anything it did not start | Stop resolves an **experiment id** to a process group this daemon created. There is no code path that accepts a pid or a pattern from a client. |
| Run two experiments at once | Mininet, Open vSwitch and TCP 6653 are exclusive. It also detects runners started from a terminal and refuses rather than corrupting both. |
| Touch results | It never deletes or rewrites a run directory. |
| Leave orphans | On shutdown it stops a running experiment; a watchdog terminates one that outlives its duration plus a grace period. |

Input validation runs on **both** sides. The API validates for a fast, clear
error; the daemon validates again because a privileged service that trusts its
caller's validation has no validation.

---

## 4. Setup

### Run it in a terminal (simplest)

```bash
sudo python3 -m dashboard.launcher.daemon
```

Leave it running. It prints one line on start and logs each operation. Stop it
with Ctrl-C; the capability disappears and the dashboard reports that.

By default it permits connections from the uid that owns the project
directory. Override with `--allow-uid 1000` or `--allow-uid alice`.

### Run it as a systemd service (optional, persistent)

```bash
sudo cp dashboard/launcher/sdn-rl-launcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sdn-rl-launcher
sudo systemctl status sdn-rl-launcher
```

Edit `User=`/`WorkingDirectory=`/`--allow-uid` in the unit if your checkout
lives elsewhere. Under WSL2, systemd must be enabled (`systemd=true` in
`/etc/wsl.conf`) — if it is not, use the terminal method.

### Verify

```bash
curl -s http://127.0.0.1:8000/api/capabilities | python3 -m json.tool
```

Look for `"start_mechanism": "launcher"` and
`"can_start_experiments": true` while `"is_root": false`.

---

## 5. Permissions this grants

The allowed uid gains the ability to:

* start one experiment at a time, with parameters drawn from a fixed
  allowlist, as root;
* stop that experiment;
* bring one of the five configured core links down or up **inside a running
  experiment**;
* read the launcher's own logs.

It does **not** gain: arbitrary command execution, file access, the ability to
stop anything else, or any persistent privilege. Stopping the daemon removes
all of it immediately.

Threat model note: this is a **single-user research tool**. If the allowed uid
is compromised, the attacker can start and stop Mininet experiments as root
and cut emulated links. That is a real but bounded capability — it does not
include running their own code as root, which is the property a sudoers rule
on a script would have been at risk of giving away.

---

## 6. Link injection into a running experiment

The dashboard cannot touch Mininet. An injection is a line appended by the
daemon to `<run_dir>/control.jsonl`; a thread *inside the runner* — the
process that owns the Mininet session — picks it up, **re-validates the link
against the real topology**, and applies it.

That ordering is what makes it real rather than theatrical: the port genuinely
goes down, Open vSwitch reports it, and the controller's `OFPPortStatus`
handler observes it exactly as it would for a scheduled failure. The result is
visible in `events.csv`, in the flow dumps, and in the topology view — but
only once the switches confirm it, never on the strength of the request alone.

Only the five switch-to-switch links in `topo/spec.py` can be targeted. Host
access links are not offered.

---

## 7. Protocol

Newline-delimited JSON, one request and one reply per connection.

| Op | Request fields | Notes |
|---|---|---|
| `ping` | — | liveness |
| `status` | — | current experiment + recent history |
| `start` | `variant`, `workload`, `seed`, `duration_s`, `idle_timeout?` | refuses if anything is running |
| `stop` | `experiment_id` | must match the running experiment |
| `inject` | `experiment_id`, `action`, `link` | `link_down` / `link_up` |

Replies are `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. Errors are
written to be shown to a person.

---

## 8. Limitations

* **One experiment at a time**, by design.
* **The daemon must be running** for browser launching. When it is not, the
  dashboard says so and shows the exact terminal command instead — the feature
  degrades to the previous behaviour rather than breaking.
* **It does not survive a reboot** unless installed as a systemd service.
* **No authentication beyond uid.** Any process running as the allowed uid can
  use it. For a localhost research tool that is the intended boundary.
* **Stopping an experiment makes its run incomplete**, and the analysis
  excludes incomplete runs (and reports how many it excluded). This is correct
  behaviour, not a bug: a run cut short has not measured what it set out to.
* **The daemon logs to `results/launcher_logs/`**, which is not rotated.
