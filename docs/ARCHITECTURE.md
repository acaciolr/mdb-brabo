# MDB BRABO — Architecture

## Where this design comes from

MDB BRABO is the MongoDB member of the DBA BRABO family (`ora_brabo`,
`sql_brabo`, `pg_brabo`). Same author, same cockpit pattern, different engine.
The pattern reused verbatim from PG BRABO:

- **Per-connection isolation.** Every tab is an independent
  `ConnectionSession`: its own `MongoClient`, capability probe, `MetricsCache`,
  `Scheduler` and `AdvisorEngine`. A dead or slow server in one tab never
  affects another. The only shared thing is the SQLite history file, keyed by
  `host:port`.
- **Collectors never touch the UI; panels never touch the database.**
  Collectors run on tiered intervals and write to `MetricsCache`; panels read
  the cache on a 0.5s UI tick. Switching panel triggers one immediate
  collection of the collector(s) behind it (`Scheduler.collect_now`) — the same
  command the interval loop would run anyway.
- **A capability layer instead of try/except roulette.**
  `core/capabilities.py` probes once per connection and every
  topology/version/privilege-sensitive collector gates on a named flag.
- **Rule-based advisor reads the cache only.** Zero extra load on the server,
  no matter how many rules.
- **READ ONLY by default, gate checked twice.** See *Safety*.

## What's MongoDB-specific

- **pymongo is synchronous.** Each collector implements `collect_sync()`;
  `BaseCollector.collect()` offloads it with `asyncio.to_thread`, so the
  Textual event loop never blocks on the network. `MongoClient` is
  thread-safe, so concurrently running collectors share one client
  (`maxPoolSize=4` — the monitor must not be what exhausts connections).
  The `--report` path calls the very same `collect_sync()` without an event
  loop, so report and TUI can never disagree.
- **Every round-trip is bounded.** `serverSelectionTimeoutMS`,
  `connectTimeoutMS`, `socketTimeoutMS` on the client, plus `maxTimeMS` on every
  aggregation/find. The scheduler also refuses to stack a second run of a
  collector whose previous cycle hasn't returned (`_inflight`).
- **Topology drives what exists.** `hello` (+ `getCmdLineOpts` for config
  servers) classifies the node as standalone / replset member / mongos /
  config server / arbiter. `replSetGetStatus` and the oplog only on replica set
  data-bearing members; sharding only through a mongos; profiler never on
  mongos/arbiter. A panel that doesn't apply shows **NOT AVAILABLE + the
  reason** instead of empty tables.
- **Privileges from `connectionStatus {showPrivileges: true}`.** Cluster-wide
  actions (`serverStatus`, `inprog`, `killop`, `replSetGetStatus`,
  `replSetStateChange`, `listShards`, `getCmdLineOpts`, `viewUser`, `compact`)
  are read once. A `clusterMonitor` user gets every read panel and no admin
  action; `root` gets everything. When the server returns no privileges
  (very old versions) a built-in role → action map is used.
- **One `serverStatus` feeds three panels** (F1 Dashboard, F4 Locks, F9
  WiredTiger): cheaper, and the three panels show the same instant. Counters
  are cumulative, so rates come from deltas between two samples; the first
  sample has *no* rate (shown as "—"), never a fake zero.
- **Tickets moved in 7.0** from `wiredTiger.concurrentTransactions` to
  `queues.execution`; the collector reads whichever block the server returned
  and labels the source.
- **One catalog walk feeds F5 and F6.** `ObjectsCollector` runs
  `listDatabases` → `dbStats` → `listCollections` → `$collStats` →
  `$indexStats` once, capped at 300 collections per cycle, on the slow tier.
  Through a mongos, `$collStats`/`$indexStats` return one document per shard;
  they're summed (and the *oldest* `accesses.since` is kept, because that's the
  window during which "0 ops" is actually meaningful).
- **Not every in-progress op is a session.** `$currentOp` also returns
  oplog fetchers, awaitable `hello` (parked ~10s by drivers on purpose), change
  streams and server threads. Those are flagged `internal`: dimmed/hidden in F2
  and ignored by the "long operation" rule.
- **Replication lag is relative to the PRIMARY** (`optimeDate` difference),
  or to the freshest member when there's no primary — and the panel says which.
  Unreachable members report `optimeDate = 1970-01-01`; that is treated as
  *unknown lag*, not 56 years (bug found against the real lab replica set).
- **Oplog window** = last − first `ts` of `local.oplog.rs`. While the capped
  collection hasn't filled yet the current window is meaningless for alerting,
  so the Advisor uses the *projected* window (`maxSize ÷ average rate`).
- **Chunk distribution**: `config.chunks` is keyed by collection `uuid` since
  5.0 (`ns` before); the collector groups by both and maps back through
  `config.collections`.
- **No statement statistics in MongoDB.** There is no `pg_stat_statements`
  equivalent. F3 reads `system.profile` only where the profiler is on and
  otherwise says so (PROFILER NOT CONFIGURED + how to enable) and shows slow
  ops currently running from `$currentOp`.

## Safety

- No collector issues anything but read commands (`serverStatus`,
  `$currentOp`, `dbStats`, `$collStats`, `$indexStats`, `replSetGetStatus`,
  `replSetGetConfig`, `listShards`, `balancerStatus`, `usersInfo`,
  `rolesInfo`, `getCmdLineOpts`, `getParameter`, `profile: -1` — which only
  *reads* the level — and finds on `local.oplog.rs`, `system.profile`,
  `config.*`).
- `ConnectionManager.execute_action()` is the only method that can send an
  administrative command, and it raises `ReadOnlyViolation` when the session
  is READ ONLY.
- The only UI path to it is `BasePanel.request_admin_action()`:
  `permissions.check_action()` (mode, demo, real privilege, topology) →
  `ConfirmModal` showing the exact `runCommand` document → `execute_action()`.
- Admin actions available in `--dba`: `killOp` (F2 K), `replSetStepDown`
  (F7 S), profiler on/off (F3 P/O), `compact` (F5 C).
- `--demo` refuses every action (there is no server).

## Directory layout

See `CLAUDE.md` for the annotated tree.
