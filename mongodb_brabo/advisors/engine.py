"""
advisors/engine.py
Rule-based advisor. Reads ONLY the MetricsCache the collectors already
filled — it never talks to MongoDB, so adding rules costs zero load on the
monitored server. Every finding carries evidence (the number that tripped
it) and a concrete recommendation, because "WARNING: replication" without
the lag value and what to check is useless at 3 a.m.

Thresholds are module constants so tests and readers see them in one place.
A rule whose input is missing from the cache simply doesn't fire — the
Advisor never guesses.

Cache keys written: advisor.findings, advisor.summary
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from enum import Enum

from mongodb_brabo.core.cache import MetricsCache
from mongodb_brabo.core.capabilities import Capabilities

log = logging.getLogger(__name__)

CONN_WARN_PCT = 80
CONN_CRIT_PCT = 90
WT_CACHE_CRIT_PCT = 95      # WiredTiger eviction_trigger default: app threads start evicting
WT_DIRTY_WARN_PCT = 10
WT_DIRTY_CRIT_PCT = 20      # eviction_dirty_trigger default: writes stall
REPL_LAG_WARN_S = 10
REPL_LAG_CRIT_S = 60
OPLOG_WINDOW_WARN_H = 24
OPLOG_WINDOW_CRIT_H = 4
LOCK_QUEUE_WARN = 10
UNUSED_INDEX_TRUST_HOURS = 24 * 7   # less than a week of $indexStats is not proof
SHARD_CHUNK_SPREAD_WARN = 8


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Finding:
    id: str
    severity: Severity
    category: str
    title: str
    detail: str
    recommendation: str
    evidence: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


class AdvisorEngine:
    def __init__(self, cache: MetricsCache, capabilities: Capabilities, interval: int = 10) -> None:
        self.cache = cache
        self.caps = capabilities
        self.interval = interval
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                self.publish(self.evaluate())
            except Exception as exc:
                log.warning("Advisor error: %s", exc)
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        self._running = False

    def publish(self, findings: list[Finding]) -> None:
        ttl = self.interval * 3
        self.cache.set("advisor.findings", [f.as_dict() for f in findings], ttl=ttl)
        self.cache.set("advisor.summary", summarize(findings), ttl=ttl)

    # ------------------------------------------------------------------
    def evaluate(self) -> list[Finding]:
        findings: list[Finding] = []
        for rule in (self._connections, self._wt_cache, self._tickets, self._replication, self._oplog,
                     self._long_ops, self._lock_queue, self._unused_indexes, self._profiler,
                     self._security, self._sharding, self._visibility):
            try:
                findings.extend(rule())
            except Exception as exc:  # one broken rule must not hide the others
                log.warning("Advisor rule %s failed: %s", rule.__name__, exc)
        findings.sort(key=lambda f: _ORDER[f.severity])
        return findings

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    def _connections(self) -> list[Finding]:
        c = self.cache.get("health.connections")
        if not c or not c.get("max"):
            return []
        pct = c["current"] / c["max"] * 100
        ev = f"current={c['current']} available={c['available']} ({pct:.1f}% do limite)"
        if pct >= CONN_CRIT_PCT:
            sev = Severity.CRITICAL
        elif pct >= CONN_WARN_PCT:
            sev = Severity.WARNING
        else:
            return []
        return [Finding("MDB-CONN-001", sev, "Conexões", "Conexões perto do limite",
                        "O mongod está perto de recusar novas conexões (net.maxIncomingConnections / ulimit -n).",
                        "Revise o maxPoolSize dos drivers (cada app × instância abre um pool), procure vazamento "
                        "de conexão e, se legítimo, aumente net.maxIncomingConnections e o ulimit do processo.", ev)]

    def _wt_cache(self) -> list[Finding]:
        w = self.cache.get("wt.cache")
        if not w:
            return []
        out = []
        used, dirty = w.get("used_pct"), w.get("dirty_pct")
        if used is not None and used > WT_CACHE_CRIT_PCT:
            out.append(Finding("MDB-WT-001", Severity.CRITICAL, "WiredTiger", "Cache WiredTiger acima de 95%",
                               "Acima do eviction_trigger as threads da aplicação passam a fazer eviction — a "
                               "latência de todas as operações sobe.",
                               "Verifique working set × RAM (storage.wiredTiger.engineConfig.cacheSizeGB), "
                               "consultas sem índice varrendo coleções (COLLSCAN no F3) e considere mais memória.",
                               f"cache usado = {used:.1f}%"))
        if dirty is not None and dirty > WT_DIRTY_CRIT_PCT:
            out.append(Finding("MDB-WT-002", Severity.CRITICAL, "WiredTiger", "Cache sujo (dirty) acima de 20%",
                               "Acima do eviction_dirty_trigger as escritas ficam bloqueadas até o cache ser limpo.",
                               "Investigue picos de escrita, checkpoints lentos (F9) e o disco (iostat: await/util).",
                               f"dirty = {dirty:.1f}%"))
        elif dirty is not None and dirty > WT_DIRTY_WARN_PCT:
            out.append(Finding("MDB-WT-002", Severity.WARNING, "WiredTiger", "Cache sujo (dirty) elevado",
                               "O volume de páginas modificadas ainda não gravadas está alto.",
                               "Acompanhe a duração dos checkpoints (F9) e a latência de disco.",
                               f"dirty = {dirty:.1f}%"))
        return out

    def _tickets(self) -> list[Finding]:
        t = self.cache.get("wt.tickets")
        if not t:
            return []
        out = []
        for kind in ("read", "write"):
            k = t.get(kind) or {}
            if k.get("total") and k.get("available") == 0:
                out.append(Finding(f"MDB-WT-003-{kind.upper()}", Severity.WARNING, "WiredTiger",
                                   f"Tickets de {'leitura' if kind == 'read' else 'escrita'} esgotados",
                                   "Todas as vagas de concorrência do storage engine estão em uso; novas "
                                   "operações entram em fila.",
                                   "Procure operações longas (F2/F3) e gargalo de disco/CPU; aumentar tickets "
                                   "raramente é a solução.",
                                   f"out={k.get('out')} available=0 total={k.get('total')}"))
        return out

    def _replication(self) -> list[Finding]:
        st = self.cache.get("replication.status")
        if not st:
            return []
        out = []
        if not st.get("primary"):
            out.append(Finding("MDB-REPL-003", Severity.CRITICAL, "Replica Set", "Replica set sem PRIMARY",
                               "Nenhum membro está como PRIMARY — escritas estão indisponíveis.",
                               "Verifique se a maioria dos membros votantes está acessível (rs.status()) e a rede "
                               "entre eles.", f"set={st.get('set')}"))
        if st.get("unhealthy"):
            out.append(Finding("MDB-REPL-002", Severity.CRITICAL, "Replica Set", "Membro do replica set não saudável",
                               "Há membro(s) DOWN/RECOVERING/inalcançável(eis); a tolerância a falhas está reduzida.",
                               "Veja lastHeartbeatMessage no F7, o log do mongod do membro e a conectividade.",
                               ", ".join(st["unhealthy"])))
        lag = st.get("max_lag_seconds") or 0
        if lag >= REPL_LAG_WARN_S:
            worst = max((m for m in st.get("members", []) if m.get("lag_seconds") is not None),
                        key=lambda m: m["lag_seconds"], default={})
            out.append(Finding("MDB-REPL-001", Severity.CRITICAL if lag >= REPL_LAG_CRIT_S else Severity.WARNING,
                               "Replica Set", "Lag de replicação alto",
                               "Um secundário está atrasado em relação ao primário; leituras secundárias ficam "
                               "defasadas e o failover pode perder escritas sem w:majority.",
                               "Cheque disco/CPU/rede do secundário, índices faltando (aplicação do oplog), "
                               "e se a janela do oplog cobre o atraso.",
                               f"{worst.get('name', '?')}: {lag:.0f}s (ref: {st.get('lag_reference')})"))
        return out

    def _oplog(self) -> list[Finding]:
        o = self.cache.get("oplog.info")
        if not o or o.get("window_hours") is None:
            return []
        full = (o.get("used_pct") or 0) >= 90
        hours = o["window_hours"] if full else o.get("projected_window_hours")
        if hours is None:
            return []
        basis = "janela atual" if full else "janela projetada (oplog ainda não encheu)"
        ev = f"{basis} = {hours:.1f}h; usado {o.get('used_pct')}% de {o.get('max_bytes')} bytes"
        if hours < OPLOG_WINDOW_CRIT_H:
            sev = Severity.CRITICAL
        elif hours < OPLOG_WINDOW_WARN_H:
            sev = Severity.WARNING
        else:
            return []
        return [Finding("MDB-OPLOG-001", sev, "Oplog", "Janela do oplog menor que 24h",
                        "Um secundário parado por mais tempo que a janela precisa de initial sync completo.",
                        "Aumente o oplog (replSetResizeOplog / storage.oplogMinRetentionHours) para cobrir "
                        "manutenção e picos de escrita.", ev)]

    def _long_ops(self) -> list[Finding]:
        c = self.cache.get("sessions.counts")
        if not c or not c.get("long_running"):
            return []
        return [Finding("MDB-OPS-001", Severity.WARNING, "Operações", "Operações de usuário longas em execução",
                        "Operações ativas há mais de 60s seguram recursos (tickets, cache, snapshots).",
                        "Inspecione no F2 (D = detalhe); use explain() no comando e, se necessário, killOp em modo DBA.",
                        f"{c['long_running']} op(s) ≥ 60s; maior = {c.get('longest_secs')}s")]

    def _lock_queue(self) -> list[Finding]:
        q = self.cache.get("locks.queue") or {}
        counts = self.cache.get("sessions.counts") or {}
        total = q.get("total", 0) or 0
        waiting = counts.get("waiting_lock", 0) or 0
        if total < LOCK_QUEUE_WARN and not waiting:
            return []
        return [Finding("MDB-LOCK-001", Severity.WARNING, "Locks", "Operações em fila / esperando lock",
                        "Há operações enfileiradas aguardando lock ou ticket.",
                        "Veja no F4 quem está esperando e no F2 quem segura (ops longas, índices em foreground, "
                        "DDL). Locks de coleção prolongados costumam ser createIndex/compact/drop.",
                        f"globalLock.currentQueue.total={total}; waitingForLock={waiting}")]

    def _unused_indexes(self) -> list[Finding]:
        unused = self.cache.get("indexes.unused") or []
        if not unused:
            return []
        trusted = [i for i in unused if (i.get("tracked_hours") or 0) >= UNUSED_INDEX_TRUST_HOURS]
        sample = ", ".join(f"{i['ns']}.{i['name']}" for i in (trusted or unused)[:5])
        if trusted:
            return [Finding("MDB-IDX-001", Severity.WARNING, "Índices", "Índices nunca usados",
                            "Índices sem nenhum acesso há ≥ 7 dias custam escrita, cache e disco.",
                            "Confirme em TODOS os membros (o $indexStats é por nó) e oculte antes de remover "
                            "(db.coll.hideIndex) para testar sem risco.",
                            f"{len(trusted)} índice(s): {sample}")]
        return [Finding("MDB-IDX-001", Severity.INFO, "Índices", "Índices sem uso desde o último restart",
                        "Contadores de $indexStats zeram no restart; a janela observada ainda é curta.",
                        "Reavalie depois de um ciclo completo de uso (≥ 7 dias, incluindo fechamentos/relatórios).",
                        f"{len(unused)} índice(s): {sample}")]

    def _profiler(self) -> list[Finding]:
        levels = self.cache.get("profiler.levels")
        if not levels:
            return []
        user = {db: v for db, v in levels.items() if db not in ("admin", "config", "local")}
        if not user or any((v.get("level") or 0) > 0 for v in user.values()):
            return []
        return [Finding("MDB-PROF-001", Severity.INFO, "Profiler", "Profiler desligado em todos os bancos",
                        "Sem profiler não há histórico de consultas lentas — o F3 só vê o que está rodando agora.",
                        "Ligue no nível 1 com slowms adequado (db.setProfilingLevel(1, {slowms: 100})) ou use "
                        "F3 [P] em modo DBA; o log do mongod também registra ops acima de slowms.",
                        f"bancos: {', '.join(sorted(user))}")]

    def _security(self) -> list[Finding]:
        o = self.cache.get("security.options") or {}
        if not o.get("known"):
            return []
        out = []
        if not o.get("auth_enabled"):
            out.append(Finding("MDB-SEC-001", Severity.CRITICAL, "Segurança", "Authorization desligado",
                               "Qualquer cliente que alcance a porta tem acesso total aos dados.",
                               "Crie um usuário administrador e ligue security.authorization: enabled (e keyFile "
                               "em replica set).",
                               f"authorization={o.get('authorization')} bindIp={o.get('bind_ip')}"))
        if o.get("tls_mode") in (None, "", "disabled"):
            out.append(Finding("MDB-SEC-002", Severity.INFO, "Segurança", "TLS desabilitado",
                               "Credenciais e dados trafegam em claro na rede.",
                               "Em produção configure net.tls.mode: requireTLS com certificados válidos.",
                               f"net.tls.mode={o.get('tls_mode')}"))
        return out

    def _sharding(self) -> list[Finding]:
        bal = self.cache.get("sharding.balancer")
        colls = self.cache.get("sharding.collections") or []
        out = []
        if bal and bal.get("mode") and bal.get("mode") != "full":
            worst = max(colls, key=lambda c: c.get("spread", 0), default=None)
            if worst and worst.get("spread", 0) >= SHARD_CHUNK_SPREAD_WARN:
                out.append(Finding("MDB-SHARD-001", Severity.WARNING, "Sharding",
                                   "Balancer parado com chunks desbalanceados",
                                   "Com o balancer desligado a distribuição não se corrige sozinha; um shard "
                                   "concentra carga e disco.",
                                   "Se não houver janela de manutenção em curso, rode sh.startBalancer() e "
                                   "acompanhe sh.balancerCollectionStatus().",
                                   f"balancer={bal.get('mode')}; {worst['ns']} max={worst['max']} min={worst['min']}"))
        for s in self.cache.get("sharding.shards") or []:
            if s.get("draining"):
                out.append(Finding("MDB-SHARD-002", Severity.INFO, "Sharding", f"Shard {s['id']} em draining",
                                   "Há remoção de shard em andamento.", "Acompanhe com removeShard até 'completed'.",
                                   s.get("host", "")))
        return out

    def _visibility(self) -> list[Finding]:
        missing = [name for name, key in (("serverStatus", "health"), ("$currentOp", "sessions"))
                   if self.cache.get(f"{key}.available") is False]
        if not missing:
            return []
        return [Finding("MDB-CAP-001", Severity.INFO, "Visibilidade", "Monitoramento parcial por falta de privilégio",
                        "Alguns painéis estão NOT AVAILABLE para este usuário, então o Advisor não enxerga tudo.",
                        "Conceda a role clusterMonitor (leitura) ao usuário de monitoramento.",
                        ", ".join(missing))]


def summarize(findings: list[Finding]) -> dict:
    score = 100
    for f in findings:
        score -= {"CRITICAL": 15, "WARNING": 5, "INFO": 1}[f.severity.value]
    return {
        "critical": sum(1 for f in findings if f.severity == Severity.CRITICAL),
        "warning": sum(1 for f in findings if f.severity == Severity.WARNING),
        "info": sum(1 for f in findings if f.severity == Severity.INFO),
        "score": max(0, min(100, score)),
    }
