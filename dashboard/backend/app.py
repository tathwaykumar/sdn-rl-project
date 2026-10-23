"""FastAPI application for the SDN + RL dashboard.

Every endpoint that returns data also returns where that data came from
(``source``: live / historical / config / mock / unavailable) and how old it is.
The frontend renders those differently and never shows mock data as a
measurement.

Run it::

    python3 -m uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000

Bound to loopback by default. When started as root it can launch experiments,
so it must not be exposed on a network interface.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
import time
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import mock, telemetry
from .comparison import analysis_artifacts, collect_run_metrics, compare
from .config import (
    ALLOWED_ORIGINS, ANALYSIS_OUT, LIVE_POLL_INTERVAL_S, PROJECT_ROOT, RUNS_DIR,
    DataSource, Mode, current_mode, probe_capabilities,
)
from .experiments import MANAGER, ExperimentError, options as experiment_options
from .runs import discover_runs, find_run, live_status
from .topology import static_topology, topology_state

app = FastAPI(
    title="SDN + RL Research Dashboard",
    version="1.0.0",
    description=(
        "Reads live telemetry written by the SDN controller and experiment "
        "runner. Issues no OpenFlow requests of its own, so it cannot distort "
        "the controller-message metric the project measures."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def is_mock() -> bool:
    return current_mode() is Mode.MOCK


def _active_raw_dir() -> Optional[str]:
    status = live_status()
    if status.active and status.run_path:
        return os.path.join(status.run_path, "raw")
    return None


def _resolve_raw_dir(run_id: Optional[str]) -> tuple[Optional[str], bool]:
    """Return (raw_dir, is_live). An explicit run_id always reads history."""
    if run_id:
        ref = find_run(run_id)
        if ref is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        return ref.raw_dir, False
    raw = _active_raw_dir()
    return raw, raw is not None


@app.middleware("http")
async def tag_data_source(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-SDNRL-Data-Source"] = "mock" if is_mock() else "live"
    return response


# --------------------------------------------------------------------- system
@app.get("/api/health")
def health() -> Dict[str, object]:
    caps = probe_capabilities()
    status = live_status()
    return {
        "ok": True,
        "time": time.time(),
        "mode": caps.mode,
        "mock_mode": is_mock(),
        "project_root": PROJECT_ROOT,
        "runs_dir_readable": caps.can_read_runs,
        "experiment_active": status.active,
        "matrix_running": status.matrix_running,
    }


@app.get("/api/capabilities")
def capabilities() -> Dict[str, object]:
    caps = probe_capabilities()
    return {
        "mode": caps.mode,
        "mock_mode": is_mock(),
        "is_root": caps.is_root,
        "can_read_runs": caps.can_read_runs,
        "can_start_experiments": caps.can_start_experiments,
        "can_query_switches_directly": caps.can_query_switches_directly,
        "reason_cannot_start": caps.reason_cannot_start,
        "reason_cannot_query": caps.reason_cannot_query,
        "notes": caps.notes,
        "project_root": caps.project_root,
        "runs_dir": caps.runs_dir,
    }


@app.get("/api/live/status")
def live_state() -> Dict[str, object]:
    return live_status().as_dict()


# ------------------------------------------------------------------ topology
@app.get("/api/topology")
def topology_config() -> Dict[str, object]:
    return static_topology()


@app.get("/api/topology/state")
def topology_live(run_id: Optional[str] = Query(None)) -> Dict[str, object]:
    if is_mock():
        return mock.mock_topology_state()
    raw_dir, live = _resolve_raw_dir(run_id)
    return topology_state(raw_dir, live)


# ---------------------------------------------------------------- flow tables
@app.get("/api/flows")
def flows(switch: Optional[str] = Query(None),
          run_id: Optional[str] = Query(None)) -> Dict[str, object]:
    if is_mock():
        return mock.mock_flow_table(switch)
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {
            "available": False,
            "source": DataSource.UNAVAILABLE.value,
            "reason": ("No experiment is running. Flow tables are captured by "
                       "the experiment runner; select a completed run to view "
                       "its recorded flow tables."),
        }
    payload = telemetry.flow_table(raw_dir, switch)
    if payload.get("available") and not live:
        payload["source"] = DataSource.HISTORICAL.value
    return payload


@app.get("/api/flows/occupancy")
def flow_occupancy(run_id: Optional[str] = Query(None),
                   limit: int = Query(60, ge=1, le=500)) -> Dict[str, object]:
    if is_mock():
        return {"source": DataSource.MOCK.value, "history": []}
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "reason": "no run selected and no experiment running",
                "history": []}
    return {
        "available": True,
        "source": DataSource.LIVE.value if live else DataSource.HISTORICAL.value,
        "history": telemetry.occupancy_history(raw_dir, limit),
    }


# ------------------------------------------------------------------- RL agent
@app.get("/api/rl/decisions")
def rl_decisions(run_id: Optional[str] = Query(None),
                 limit: int = Query(60, ge=1, le=500)) -> Dict[str, object]:
    if is_mock():
        decisions = mock.mock_decisions(limit)
        return {"available": True, "source": DataSource.MOCK.value,
                "decisions": decisions,
                "states": {d["state"]: telemetry.decode_state(str(d["state"]))
                           for d in decisions}}
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "reason": "no experiment running and no run selected",
                "decisions": []}
    decisions = telemetry.read_decisions(raw_dir, limit)
    return {
        "available": True,
        "source": DataSource.LIVE.value if live else DataSource.HISTORICAL.value,
        "decisions": decisions,
        "states": {str(d["state"]): telemetry.decode_state(str(d["state"]))
                   for d in decisions if d.get("state")},
        "note": ("Variant A has no agent; its rows carry no Q-values or "
                 "epsilon and those fields stay null rather than being "
                 "filled with plausible numbers."),
    }


@app.get("/api/rl/verification")
def rl_verification(run_id: Optional[str] = Query(None)) -> Dict[str, object]:
    """Latest decision compared against the rules actually on the switches."""
    if is_mock():
        decisions = mock.mock_decisions(1)
        return {"available": True, "source": DataSource.MOCK.value,
                "decision": decisions[-1], "status": "confirmed",
                "detail": "MOCK DATA - no switch was consulted."}
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "reason": "no experiment running and no run selected"}
    payload = telemetry.decision_vs_installed(raw_dir)
    payload["source"] = DataSource.LIVE.value if live else DataSource.HISTORICAL.value
    return payload


# ------------------------------------------------------------------- metrics
@app.get("/api/metrics/live")
def metrics_live(run_id: Optional[str] = Query(None)) -> Dict[str, object]:
    if is_mock():
        return mock.mock_metrics()
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "reason": "no experiment running and no run selected"}
    payload = telemetry.latest_metrics(raw_dir)
    payload["source"] = DataSource.LIVE.value if live else DataSource.HISTORICAL.value
    return payload


@app.get("/api/metrics/series")
def metrics_series(run_id: Optional[str] = Query(None),
                   limit: int = Query(180, ge=1, le=2000)) -> Dict[str, object]:
    if is_mock():
        return mock.mock_series(limit)
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"points": [], "count": 0, "source": DataSource.UNAVAILABLE.value,
                "reason": "no experiment running and no run selected"}
    payload = telemetry.read_metric_series(raw_dir, limit)
    payload["source"] = DataSource.LIVE.value if live else DataSource.HISTORICAL.value
    return payload


@app.get("/api/metrics/latency")
def metrics_latency(run_id: Optional[str] = Query(None),
                    window_s: float = Query(30.0, ge=1.0, le=600.0)) -> Dict[str, object]:
    if is_mock():
        return {"available": True, "source": DataSource.MOCK.value,
                "median_ms": 7.4, "p95_ms": 19.1, "samples": 120,
                "losses": 0, "loss_fraction": 0.0, "series": [],
                "window_s": window_s, "note": "MOCK DATA."}
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "reason": "no experiment running and no run selected"}
    payload = telemetry.read_latency(raw_dir, window_s)
    payload["source"] = DataSource.LIVE.value if live else DataSource.HISTORICAL.value
    return payload


@app.get("/api/events")
def events(run_id: Optional[str] = Query(None),
           limit: int = Query(80, ge=1, le=1000)) -> Dict[str, object]:
    if is_mock():
        return {"available": True, "source": DataSource.MOCK.value, "events": []}
    raw_dir, live = _resolve_raw_dir(run_id)
    if raw_dir is None:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "events": [], "reason": "no experiment running and no run selected"}
    return {
        "available": True,
        "source": DataSource.LIVE.value if live else DataSource.HISTORICAL.value,
        "events": telemetry.read_events(raw_dir, limit),
    }


# --------------------------------------------------------------- experiments
@app.get("/api/experiments/options")
def experiments_options() -> Dict[str, object]:
    return experiment_options()


@app.get("/api/experiments/status")
def experiments_status() -> Dict[str, object]:
    return MANAGER.status()


@app.post("/api/experiments/start")
async def experiments_start(request: Request) -> Dict[str, object]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="request body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")

    try:
        seed = int(body.get("seed", 1))
        duration = int(body.get("duration_s", 90))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail="seed and duration_s must be integers")
    idle = body.get("idle_timeout")
    if idle is not None:
        try:
            idle = int(idle)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="idle_timeout must be an integer")

    try:
        return MANAGER.start(
            variant=str(body.get("variant", "")),
            workload=str(body.get("workload", "")),
            seed=seed, duration_s=duration, idle_timeout=idle,
        )
    except ExperimentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@app.post("/api/experiments/stop")
def experiments_stop() -> Dict[str, object]:
    try:
        return MANAGER.stop()
    except ExperimentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@app.get("/api/experiments/command")
def experiments_command(variant: str, workload: str, seed: int = 1,
                        duration_s: int = 90,
                        idle_timeout: Optional[int] = None) -> Dict[str, object]:
    """The exact CLI invocation, so the UI can show it when it cannot run it."""
    try:
        MANAGER._validate(variant, workload, seed, duration_s, idle_timeout)
    except ExperimentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    argv = MANAGER.build_command(variant, workload, seed, duration_s, idle_timeout)
    return {"argv": argv, "command": "sudo " + " ".join(argv), "cwd": PROJECT_ROOT}


@app.get("/api/experiments/log")
def experiments_log(lines: int = Query(200, ge=1, le=2000)) -> Dict[str, object]:
    return MANAGER.read_log(lines)


# ---------------------------------------------------------------------- runs
@app.get("/api/runs")
def runs_list(limit: int = Query(300, ge=1, le=2000)) -> Dict[str, object]:
    status = live_status()
    refs = discover_runs()[:limit]
    items: List[Dict[str, object]] = []
    for ref in refs:
        manifest = ref.manifest or {}
        items.append({
            "run_id": ref.run_id,
            "variant": ref.variant,
            "workload": ref.workload,
            "seed": ref.seed,
            "status": ref.status(status.run_id),
            "complete": ref.complete,
            "started_at": ref.started_at(),
            "ended_at": ref.ended_at(),
            "duration_s": ref.config.get("duration_s"),
            "git_commit": ref.git_commit(),
            "flow_dump_samples": manifest.get("flow_dump_samples"),
            "iperf_files": len(manifest.get("iperf3_json_files", []) or []),
        })
    return {
        "source": DataSource.HISTORICAL.value,
        "runs": items,
        "total": len(items),
        "live_run_id": status.run_id,
    }


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> Dict[str, object]:
    ref = find_run(run_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    status = live_status()
    return {
        "run_id": ref.run_id,
        "status": ref.status(status.run_id),
        "complete": ref.complete,
        "config": ref.config,
        "manifest": ref.manifest,
        "started_at": ref.started_at(),
        "ended_at": ref.ended_at(),
        "source": (DataSource.LIVE.value if ref.run_id == status.run_id
                   else DataSource.HISTORICAL.value),
    }


@app.get("/api/runs/{run_id}/export.csv")
def run_export(run_id: str, window: str = Query("full")) -> StreamingResponse:
    """Per-run metrics as CSV, computed by the project's analysis code."""
    if window not in ("full", "second_half"):
        raise HTTPException(status_code=400, detail="window must be full or second_half")
    ref = find_run(run_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")

    rows = [r for r in collect_run_metrics(RUNS_DIR, window, include_incomplete=True)
            if r.get("run_id") == run_id]
    if not rows:
        raise HTTPException(status_code=404, detail="no metrics available for this run")

    buffer = io.StringIO()
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{run_id}-{window}.csv"'},
    )


# ----------------------------------------------------------------- comparison
@app.get("/api/comparison")
def comparison(window: str = Query("full"),
               baseline: str = Query("A"),
               workloads: Optional[str] = Query(None),
               variants: Optional[str] = Query(None),
               seeds: Optional[str] = Query(None)) -> Dict[str, object]:
    if window not in ("full", "second_half"):
        raise HTTPException(status_code=400, detail="window must be full or second_half")

    def split(value: Optional[str]) -> Optional[List[str]]:
        if not value:
            return None
        return [part.strip() for part in value.split(",") if part.strip()]

    seed_list: Optional[List[int]] = None
    if seeds:
        try:
            seed_list = [int(s) for s in split(seeds) or []]
        except ValueError:
            raise HTTPException(status_code=400, detail="seeds must be integers")

    return compare(
        window=window, baseline=baseline,
        workloads=split(workloads), variants=split(variants), seeds=seed_list,
    )


@app.get("/api/comparison/export.csv")
def comparison_export(window: str = Query("full")) -> StreamingResponse:
    if window not in ("full", "second_half"):
        raise HTTPException(status_code=400, detail="window must be full or second_half")
    rows = collect_run_metrics(RUNS_DIR, window, include_incomplete=True)
    if not rows:
        raise HTTPException(status_code=404, detail="no run metrics available")
    buffer = io.StringIO()
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="per_run_metrics-{window}.csv"'},
    )


@app.get("/api/analysis/artifacts")
def analysis_list() -> Dict[str, object]:
    return analysis_artifacts()


@app.get("/api/analysis/file/{name}")
def analysis_file(name: str) -> FileResponse:
    """Serve a generated figure or table. Confined to analysis/out."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid file name")
    path = os.path.join(ANALYSIS_OUT, name)
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(ANALYSIS_OUT):
        raise HTTPException(status_code=400, detail="invalid path")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"no such artefact {name!r}")
    media = ("image/png" if name.endswith(".png")
             else "text/csv" if name.endswith(".csv")
             else "text/markdown" if name.endswith(".md")
             else "application/json" if name.endswith(".json")
             else "application/octet-stream")
    return FileResponse(path, media_type=media, filename=name)


# ------------------------------------------------------------------ websocket
class ConnectionManager:
    """Tracks websocket clients and broadcasts live snapshots."""

    def __init__(self) -> None:
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, payload: Dict[str, object]) -> None:
        async with self._lock:
            targets = list(self.active)
        dead: List[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


CONNECTIONS = ConnectionManager()


def build_live_snapshot() -> Dict[str, object]:
    """One push payload: everything the live views need, in one message."""
    if is_mock():
        return {
            "type": "snapshot",
            "t": time.time(),
            "source": DataSource.MOCK.value,
            "mock_mode": True,
            "live": {"active": True, "run_id": "MOCK-RUN", "variant": "C",
                     "workload": "heavy", "seed": 1, "detail": "MOCK DATA"},
            "metrics": mock.mock_metrics(),
            "topology": mock.mock_topology_state().get("live"),
            "decisions": mock.mock_decisions(8),
            "verification": {"status": "confirmed",
                             "detail": "MOCK DATA - no switch was consulted."},
            "events": [],
        }

    status = live_status()
    payload: Dict[str, object] = {
        "type": "snapshot",
        "t": time.time(),
        "mock_mode": False,
        "live": status.as_dict(),
    }
    raw_dir = os.path.join(status.run_path, "raw") if status.run_path else None
    if not status.active or not raw_dir or not os.path.isdir(raw_dir):
        payload["source"] = DataSource.UNAVAILABLE.value
        payload["metrics"] = {"available": False,
                              "reason": "no experiment is running"}
        payload["topology"] = {"available": False,
                               "reason": "no experiment is running"}
        payload["decisions"] = []
        payload["verification"] = {"available": False}
        payload["events"] = []
        return payload

    payload["source"] = DataSource.LIVE.value
    payload["metrics"] = telemetry.latest_metrics(raw_dir)
    payload["latency"] = telemetry.read_latency(raw_dir, 30.0)
    payload["topology"] = topology_state(raw_dir, True).get("live")
    payload["decisions"] = telemetry.read_decisions(raw_dir, 12)
    payload["verification"] = telemetry.decision_vs_installed(raw_dir)
    payload["events"] = telemetry.read_events(raw_dir, 12)
    payload["flow_counts"] = (
        telemetry.flow_table(raw_dir).get("counts_by_switch")
        if telemetry.latest_flow_dump(raw_dir) else {}
    )
    return payload


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await CONNECTIONS.connect(ws)
    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(build_live_snapshot)
                await ws.send_json(snapshot)
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                # One bad read must not kill the stream; report and keep going.
                await ws.send_json({
                    "type": "error",
                    "t": time.time(),
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            await asyncio.sleep(LIVE_POLL_INTERVAL_S)
    except WebSocketDisconnect:
        pass
    finally:
        await CONNECTIONS.disconnect(ws)


@app.get("/api/snapshot")
def snapshot() -> Dict[str, object]:
    """Polling fallback for clients without a working websocket."""
    return build_live_snapshot()
