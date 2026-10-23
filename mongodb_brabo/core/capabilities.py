"""
core/capabilities.py
Probe once per connection what this server IS and what this user CAN do,
then let every collector/panel gate on a named flag instead of wrapping
whole collection cycles in try/except.

MongoDB varies along three axes that matter for a monitoring tool:
  * topology  — standalone / replica set member / mongos / config server /
                arbiter: replSetGetStatus, oplog, listShards and the profiler
                only make sense on some of them;
  * version   — tickets moved from wiredTiger.concurrentTransactions to
                queues.execution (7.0), config.chunks keyed by uuid (5.0)…;
  * privileges — a clusterMonitor user can read serverStatus/$currentOp but
                not killOp; usersInfo needs viewUser, etc.

`parse_capabilities()` is a pure function over the raw command replies so it
can be unit-tested with fabricated documents (tests/test_capabilities.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

TOPOLOGY_STANDALONE = "standalone"
TOPOLOGY_REPLSET = "replset"
TOPOLOGY_MONGOS = "mongos"
TOPOLOGY_CONFIGSVR = "configsvr"
TOPOLOGY_ARBITER = "arbiter"


@dataclass
class Capabilities:
    version: str = ""
    major: int = 0
    minor: int = 0
    topology: str = TOPOLOGY_STANDALONE
    set_name: str | None = None
    is_primary: bool = False
    is_secondary: bool = False
    storage_engine: str = ""
    auth_enabled: bool | None = None     # None = couldn't tell
    users: list[str] = field(default_factory=list)   # authenticated users (user@db)
    roles: list[str] = field(default_factory=list)   # role@db
    actions: set[str] = field(default_factory=set)   # cluster/anyResource actions
    all_privileges: bool = False          # auth off or root-like → everything allowed
    probe_errors: dict[str, str] = field(default_factory=dict)

    # ── Topology helpers ─────────────────────────────────────────────
    @property
    def is_mongos(self) -> bool:
        return self.topology == TOPOLOGY_MONGOS

    @property
    def is_replset(self) -> bool:
        return self.topology in (TOPOLOGY_REPLSET, TOPOLOGY_CONFIGSVR, TOPOLOGY_ARBITER) and bool(self.set_name)

    @property
    def is_arbiter(self) -> bool:
        return self.topology == TOPOLOGY_ARBITER

    @property
    def has_wiredtiger(self) -> bool:
        return self.storage_engine == "wiredTiger"

    @property
    def role_label(self) -> str:
        if self.is_mongos:
            return "MONGOS"
        if self.is_arbiter:
            return "ARBITER"
        if self.topology == TOPOLOGY_CONFIGSVR:
            return "CONFIGSVR " + ("PRIMARY" if self.is_primary else "SECONDARY")
        if self.is_replset:
            if self.is_primary:
                return "PRIMARY"
            if self.is_secondary:
                return "SECONDARY"
            return "RS-OTHER"
        return "STANDALONE"

    def can(self, action: str) -> bool:
        return self.all_privileges or action in self.actions

    # ── Feature flags (each panel gates on exactly one of these) ─────
    @property
    def can_server_status(self) -> bool:
        return self.can("serverStatus")

    @property
    def can_current_op(self) -> bool:
        return self.can("inprog")

    @property
    def can_kill_op(self) -> bool:
        return self.can("killop")

    @property
    def can_repl_status(self) -> bool:
        return self.is_replset and self.can("replSetGetStatus")

    @property
    def can_read_oplog(self) -> bool:
        # oplog lives in local.oplog.rs only on replica set data-bearing nodes.
        return self.is_replset and not self.is_arbiter

    @property
    def can_list_shards(self) -> bool:
        return self.is_mongos and self.can("listShards")

    @property
    def can_profile(self) -> bool:
        # mongos has no system.profile of its own; arbiters hold no data.
        return not self.is_mongos and not self.is_arbiter

    @property
    def can_index_stats(self) -> bool:
        return self.major >= 4 or (self.major == 3 and self.minor >= 2)

    @property
    def can_cmdline_opts(self) -> bool:
        return self.can("getCmdLineOpts")

    @property
    def can_view_users(self) -> bool:
        return self.can("viewUser")

    @property
    def can_step_down(self) -> bool:
        return self.is_replset and self.is_primary and self.can("replSetStateChange")

    @property
    def can_compact(self) -> bool:
        return not self.is_mongos and not self.is_arbiter and self.can("compact")

    @property
    def tickets_in_queues(self) -> bool:
        """7.0+ reports execution tickets under serverStatus.queues.execution."""
        return self.major >= 7

    def summary(self) -> dict:
        return {
            "version": self.version, "topology": self.topology, "role": self.role_label,
            "set_name": self.set_name, "storage_engine": self.storage_engine,
            "auth_enabled": self.auth_enabled, "users": self.users, "roles": self.roles,
            "all_privileges": self.all_privileges,
            "flags": {
                "server_status": self.can_server_status, "current_op": self.can_current_op,
                "kill_op": self.can_kill_op, "repl_status": self.can_repl_status,
                "oplog": self.can_read_oplog, "list_shards": self.can_list_shards,
                "profiler": self.can_profile, "index_stats": self.can_index_stats,
                "cmdline_opts": self.can_cmdline_opts, "view_users": self.can_view_users,
            },
            "probe_errors": self.probe_errors,
        }


# Roles that imply every action we care about (cluster-wide superuser-ish).
_ALL_POWER_ROLES = {"root", "__system"}

# Built-in roles → the subset of actions MDB BRABO gates on. connectionStatus
# with showPrivileges gives the authoritative list; this map is only the
# fallback when privileges weren't returned (very old servers).
_ROLE_ACTIONS = {
    "clusterMonitor": {"serverStatus", "inprog", "replSetGetStatus", "listShards",
                       "getCmdLineOpts", "listDatabases", "dbStats", "collStats", "indexStats"},
    "clusterManager": {"replSetStateChange", "listShards", "listDatabases"},
    "clusterAdmin": {"serverStatus", "inprog", "killop", "replSetGetStatus", "replSetStateChange",
                     "listShards", "getCmdLineOpts", "listDatabases", "compact", "enableProfiler"},
    "hostManager": {"killop", "inprog"},
    "userAdminAnyDatabase": {"viewUser", "viewRole"},
    "dbAdminAnyDatabase": {"dbStats", "collStats", "indexStats", "enableProfiler", "compact", "listDatabases"},
}


def parse_version(version: str) -> tuple[int, int]:
    parts = (version or "0.0").split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0, 0


def detect_topology(hello: dict, cmdline: dict | None = None) -> str:
    if hello.get("msg") == "isdbgrid":
        return TOPOLOGY_MONGOS
    if hello.get("arbiterOnly"):
        return TOPOLOGY_ARBITER
    cluster_role = (((cmdline or {}).get("parsed") or {}).get("sharding") or {}).get("clusterRole")
    if hello.get("configsvr") or cluster_role == "configsvr":
        return TOPOLOGY_CONFIGSVR
    if hello.get("setName"):
        return TOPOLOGY_REPLSET
    return TOPOLOGY_STANDALONE


def parse_capabilities(
    hello: dict,
    build_info: dict | None = None,
    conn_status: dict | None = None,
    server_status: dict | None = None,
    cmdline: dict | None = None,
) -> Capabilities:
    caps = Capabilities()
    build_info = build_info or {}
    caps.version = str(build_info.get("version") or "")
    caps.major, caps.minor = parse_version(caps.version)
    caps.topology = detect_topology(hello, cmdline)
    caps.set_name = hello.get("setName")
    caps.is_primary = bool(hello.get("isWritablePrimary", hello.get("ismaster", False)))
    caps.is_secondary = bool(hello.get("secondary", False))
    if server_status:
        caps.storage_engine = ((server_status.get("storageEngine") or {}).get("name")) or ""
    elif caps.is_mongos:
        caps.storage_engine = ""

    auth = (conn_status or {}).get("authInfo") or {}
    users = auth.get("authenticatedUsers") or []
    roles = auth.get("authenticatedUserRoles") or []
    caps.users = [f"{u.get('user')}@{u.get('db')}" for u in users]
    caps.roles = [f"{r.get('role')}@{r.get('db')}" for r in roles]

    if conn_status is not None and not users:
        # No authenticated user but the connection works: either auth is
        # disabled (localhost exception aside) or we really are anonymous.
        # The server enforces privileges anyway; we optimistically enable
        # panels and let real errors surface as NOT AVAILABLE.
        caps.all_privileges = True
    role_names = {r.get("role") for r in roles}
    if role_names & _ALL_POWER_ROLES:
        caps.all_privileges = True

    privileges = auth.get("authenticatedUserPrivileges")
    if privileges:
        for p in privileges:
            res = p.get("resource") or {}
            # Cluster-wide actions, or anyResource / any db+collection —
            # those are the ones that let us monitor the whole server.
            if res.get("cluster") or res.get("anyResource") or (res.get("db") == "" and res.get("collection") == ""):
                caps.actions.update(p.get("actions") or [])
            elif res.get("db") == "admin":
                caps.actions.update(a for a in (p.get("actions") or []) if a in ("viewUser", "viewRole"))
    else:
        for r in role_names:
            caps.actions.update(_ROLE_ACTIONS.get(r, set()))

    if cmdline:
        sec = ((cmdline.get("parsed") or {}).get("security") or {})
        if "authorization" in sec:
            caps.auth_enabled = sec.get("authorization") == "enabled"
        elif sec.get("keyFile") or sec.get("clusterAuthMode") in ("keyFile", "x509"):
            caps.auth_enabled = True   # keyFile implies authorization
        elif cmdline.get("parsed") is not None:
            caps.auth_enabled = False
    return caps


def discover(conn) -> Capabilities:
    """Run the probe commands (sync — call via conn.run). Any single probe
    failing is recorded in probe_errors rather than aborting discovery:
    a user without clusterMonitor should still get the panels it can use."""
    errors: dict[str, str] = {}

    def _try(name: str, fn):
        try:
            return fn()
        except Exception as exc:  # recorded, shown in the UI as NOT AVAILABLE
            errors[name] = str(exc).split(", full error")[0][:200]
            return None

    hello = _try("hello", lambda: conn.command("admin", "hello")) or \
        _try("isMaster", lambda: conn.command("admin", "isMaster")) or {}
    build = _try("buildInfo", lambda: conn.command("admin", "buildInfo")) or {}
    cstat = _try("connectionStatus", lambda: conn.command("admin", {"connectionStatus": 1, "showPrivileges": True}))
    sstat = _try("serverStatus", lambda: conn.command("admin", {"serverStatus": 1, "repl": 0, "metrics": 0, "locks": 0}))
    cmdline = _try("getCmdLineOpts", lambda: conn.command("admin", "getCmdLineOpts"))
    caps = parse_capabilities(hello, build, cstat, sstat, cmdline)
    caps.probe_errors = errors
    if errors:
        log.info("Capability probe partial: %s", errors)
    return caps
