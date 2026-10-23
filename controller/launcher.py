"""Minimal stand-in for the ``osken-manager`` CLI.

Neither the Ubuntu ``python3-os-ken`` package nor upstream ``os-ken`` on PyPI
ships ``os_ken/cmd/`` any more -- os-ken is now maintained as a library for
OpenStack Neutron, and the manager entry point was dropped.  Rather than pin an
abandoned Ryu release to get a launcher back, this module performs the same
startup sequence directly against the public ``AppManager`` API:

    load_apps -> create_contexts -> instantiate_apps -> joinall

``OFPHandler`` (which owns the TCP listener) does not need to be named: os-ken's
``load_apps`` resolves it automatically as a dependent service of any app that
consumes ``ofp_event``.  Its ``start()`` returns the listener greenthread, which
is what ``joinall`` blocks on.

Usage::

    python3 -m controller.launcher --app controller.baseline --port 6653

``SDNRL_CONFIG`` must point at the run configuration JSON; the app reads it.
"""

from __future__ import annotations

# eventlet must monkey-patch before anything imports socket/select for real.
# os_ken.lib.hub does this for us, and it has to happen first.
from os_ken.lib import hub  # noqa: E402  isort:skip

hub.patch(thread=False)

import argparse  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402

from os_ken import cfg  # noqa: E402
from os_ken.base.app_manager import AppManager  # noqa: E402
from os_ken.controller import controller as of_controller  # noqa: E402  (registers CLI opts)

CONF = cfg.CONF
LOG = logging.getLogger("launcher")


def _install_signal_handlers(app_mgr: AppManager) -> None:
    """Shut down cleanly so telemetry is flushed and the Q-table is saved.

    The runner stops the controller with SIGTERM.  Without a handler that calls
    ``app_mgr.close()``, the process would die with the last telemetry rows
    still buffered and no saved Q-table -- the run would look complete and be
    missing its most important output.
    """
    state = {"closing": False}

    def _watchdog(_signum, _frame):
        LOG.error("shutdown exceeded 10s; exiting hard (telemetry may be short)")
        os._exit(1)

    def _handler(signum, _frame):
        if state["closing"]:
            return
        state["closing"] = True
        LOG.info("signal %s received; shutting down apps", signum)
        # A hung shutdown would stall the whole experiment matrix, so bound it.
        signal.signal(signal.SIGALRM, _watchdog)
        signal.setitimer(signal.ITIMER_REAL, 10.0)
        try:
            app_mgr.close()
        except Exception:
            LOG.exception("error during app shutdown")
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        logging.shutdown()
        # os._exit avoids eventlet unwinding the greenthread we are standing on,
        # which can hang; everything that needed flushing has been flushed.
        os._exit(0)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app",
        required=True,
        help="app module to load, e.g. controller.baseline",
    )
    parser.add_argument("--port", type=int, default=6653, help="OpenFlow listen port")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # oslo.config must be invoked once before overrides are accepted.
    CONF(args=[], project="sdn_rl", default_config_files=[])
    CONF.set_override("ofp_tcp_listen_port", args.port)
    CONF.set_override("ofp_listen_host", args.listen_host)

    LOG.info("loading app %s; listening on %s:%d",
             args.app, args.listen_host, args.port)

    app_mgr = AppManager.get_instance()
    app_mgr.load_apps([args.app])
    if not app_mgr.applications_cls:
        LOG.error("no app class found in %s", args.app)
        return 2
    contexts = app_mgr.create_contexts()
    services = app_mgr.instantiate_apps(**contexts)

    if not services:
        LOG.error("no services started; OFPHandler did not provide a listener")
        app_mgr.close()
        return 3

    _install_signal_handlers(app_mgr)
    LOG.info("controller running (%d service thread(s))", len(services))
    try:
        hub.joinall(services)
    finally:
        app_mgr.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
