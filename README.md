<div align="center">

```
 __  __  ____   ____    ____   ____      _    ____   ____
|  \/  ||  _ \ | __ )  | __ ) |  _ \    / \  | __ ) / __ \
| |\/| || | | ||  _ \  |  _ \ | |_) |  / _ \ |  _ \| |  | |
| |  | || |_| || |_) | | |_) ||  _ <  / ___ \| |_) | |__| |
|_|  |_||____/ |____/  |____/ |_| \_\/_/   \_\____/ \____/
```

**MongoDB DBA TUI Cockpit**

*O cockpit do DBA MongoDB que você acessa por SSH.*

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://python.org)
[![MongoDB](https://img.shields.io/badge/MongoDB-4.4%20→%208.0-47A248?style=flat-square&logo=mongodb)](https://www.mongodb.com)
[![Textual](https://img.shields.io/badge/TUI-Textual-purple?style=flat-square)](https://textual.textualize.io)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![DBA BRABO](https://img.shields.io/badge/by-DBA%20BRABO-orange?style=flat-square)](https://github.com/acaciolr)

</div>

---

## O que é o MDB BRABO?

O **MDB BRABO** é uma ferramenta de monitoramento e administração de MongoDB que roda 100% no terminal, via SSH. Não precisa de agente, de browser nem de servidor web exposto.

É irmão de arquitetura do **ORA BRABO** (Oracle), do **SQL BRABO** (SQL Server) e do **PG BRABO** (PostgreSQL): mesmo autor, mesmo padrão de cockpit, motor diferente. Numa única interface ele mostra serverStatus, operações em curso, consultas lentas, filas de lock, bancos, coleções, índices, replica set, sharding, WiredTiger, oplog e segurança. Também permite **várias conexões em abas** e segue um princípio rígido: **observa primeiro e só altera quando pedido** (READ ONLY por padrão).

Usado como opção de **MONITORING** da plataforma de ensino **MONGO SUPER DBA**.

---

## Painéis

| Tecla | Painel | Fonte |
|------|--------|-------|
| **F1** | 🖥️ **Dashboard**: versão, papel (PRIMARY/SECONDARY/STANDALONE/MONGOS/ARBITER), uptime, conexões current/available, opcounters em ops/s, rede, memória, cache WiredTiger (% usado/dirty), tickets R/W, fila de lock, gráficos ao vivo e resumo do Advisor | `serverStatus` |
| **F2** | 👥 **Sessões**: opid, ns, op, secs_running, client, app, user, desc, waitingForLock, plano, comando. `[K]` kill (DBA), `[/]` filtro, `[D]` detalhe, `[I]` mostra/oculta operações internas (oplog fetcher, hello awaitable, threads) | `$currentOp` |
| **F3** | 🔍 **Top operações / consultas lentas**: system.profile ordenado por millis (docsExamined, keysExamined, plano, COLLSCAN em vermelho). Com o profiler desligado, mostra **como ligar** e as ops lentas rodando agora. `[P]`/`[O]` liga/desliga o profiler (DBA) | `system.profile`, `profile:-1` |
| **F4** | 🔒 **Locks / filas**: globalLock.currentQueue, clientes ativos, tickets, operações esperando lock e lockStats (acquire/wait/tempo por recurso e modo) | `serverStatus`, `$currentOp` |
| **F5** | 🗄️ **Bancos e coleções**: em disco, dataSize, storage, coleções, views, documentos, avgObjSize, índices e tamanho dos índices; coleções por tamanho. `[C]` compact (DBA) | `dbStats`, `$collStats` |
| **F6** | 📇 **Índices**: uso (`accesses.ops`), desde quando, janela observada, tamanho, flags (unique/sparse/partial/TTL/hidden), **índices nunca usados**. `[U]` só não usados, `[/]` filtro | `$indexStats` |
| **F7** | 🔁 **Replica Set**: membros, estado, health, uptime, optime, **lag** (relativo ao PRIMARY), sync source, ping, prioridade/votos, última eleição. `[S]` stepDown (DBA, só no PRIMARY) | `replSetGetStatus`, `replSetGetConfig` |
| **F8** | 🧩 **Sharding** (via mongos): shards, balancer (modo/rodando) e distribuição de chunks por coleção e shard | `listShards`, `balancerStatus`, `config.chunks` |
| **F9** | ⚙️ **WiredTiger**: bytes em cache/máximo, dirty, evictions/s (e por thread de aplicação), tickets, checkpoints (último/máximo) e gráficos | `serverStatus.wiredTiger` |
| **F10** | 📜 **Oplog**: tamanho usado/máximo, **janela em horas**, primeira/última entrada, taxa média e ao vivo de geração, janela projetada | `local.oplog.rs` |
| **F11** | 🛡️ **Segurança**: usuários e roles, roles customizadas, mecanismos de auth, **authorization**, keyFile, **TLS**, bindIp e auditLog | `usersInfo`, `rolesInfo`, `getCmdLineOpts` |
| **F12** | 🧠 **Advisor**: regras CRITICAL/WARNING/INFO, cada uma com evidência e recomendação | só o cache (zero carga extra) |
| **h** | 📈 **Histórico**: SQLite local `~/.mdb_brabo/history.db` (conexões, ops/s, cache WT, dados) das últimas 24h e projeção de crescimento | local |

Outras teclas: `+`/`Ctrl+N` abre nova conexão (aba), `Ctrl+W` fecha a aba, `?` mostra a ajuda e `q` sai.

> Painel sem suporte na topologia/versão/privilégio atual mostra **NOT AVAILABLE + o motivo**. Exemplos: "Servidor STANDALONE — não faz parte de um replica set", "Conexão não é um mongos" ou "Usuário sem a ação 'inprog' (precisa de clusterMonitor)". Fora do `--demo`, **nunca** aparecem dados simulados.

### Regras do Advisor

| ID | Severidade | Regra |
|----|-----------|-------|
| MDB-CONN-001 | WARNING ≥ 80% / CRITICAL ≥ 90% | conexões perto do limite |
| MDB-WT-001 | CRITICAL | cache WiredTiger > 95% |
| MDB-WT-002 | WARNING > 10% / CRITICAL > 20% | cache dirty |
| MDB-WT-003 | WARNING | tickets de leitura/escrita esgotados |
| MDB-REPL-001 | WARNING ≥ 10s / CRITICAL ≥ 60s | lag de réplica |
| MDB-REPL-002 | CRITICAL | membro não saudável |
| MDB-REPL-003 | CRITICAL | replica set sem PRIMARY |
| MDB-OPLOG-001 | WARNING < 24h / CRITICAL < 4h | janela do oplog (projetada enquanto o oplog não encheu; exige ≥ 1h observada) |
| MDB-OPS-001 | WARNING | operações de usuário ativas há ≥ 60s |
| MDB-LOCK-001 | WARNING | fila de lock ≥ 10 ou ops esperando lock |
| MDB-IDX-001 | INFO (< 7 dias de stats) / WARNING | índices nunca usados |
| MDB-PROF-001 | INFO | profiler desligado em todos os bancos |
| MDB-SEC-001 | CRITICAL | authorization desligado |
| MDB-SEC-002 | INFO | TLS desabilitado |
| MDB-SHARD-001 | WARNING | balancer parado com chunks desbalanceados |
| MDB-SHARD-002 | INFO | shard em draining |
| MDB-CAP-001 | INFO | monitoramento parcial por falta de privilégio |

---

## Ambientes suportados

| Alvo | Suporte |
|------|---------|
| MongoDB 4.4 / 5.0 / 6.0 / 7.0 | esperado (caminhos por capability cobertos por testes unitários; não validado em servidor real) |
| MongoDB 8.0 | ✅ **validado em servidor real** |
| Standalone | ✅ |
| Replica set (PRIMARY / SECONDARY / PSA com arbiter) | ✅ |
| mongos (sharded cluster) | ✅ (F8) |
| Config server / arbiter | ✅ (painéis não aplicáveis ficam NOT AVAILABLE) |
| Usuário `root` | tudo, incluindo as ações em `--dba` |
| Usuário `clusterMonitor` | todos os painéis de leitura, nenhuma ação |

Tudo o que depende de topologia, versão ou privilégio passa por `core/capabilities.py` (`hello`, `buildInfo`, `connectionStatus {showPrivileges}`, `getCmdLineOpts`).

---

## Instalação

**Requisitos:** Python 3.11+ e acesso de rede ao mongod/mongos.

```bash
git clone https://github.com/acaciolr/mdb-brabo.git
cd mdb-brabo
./install.sh            # cria .venv e faz pip install -e ".[full]"
```

Instalação manual:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[full]"     # textual, rich, pymongo + plotext e keyring (opcionais)
```

Dependências: `textual`, `rich` e `pymongo>=4.6`. Opcionais: `plotext` (gráficos; sem ele, sparklines) e `keyring` (senha de perfis salvos).

---

## Uso

```bash
# URI completa (a senha pode ficar fora da URI, ver abaixo)
mdb-brabo --uri "mongodb://root_admin@192.168.60.1:27017/?authSource=admin&directConnection=true"

# Campos separados
mdb-brabo --host 192.168.60.1 --port 27017 --user root_admin --auth-db admin --direct

# Senha por ambiente (não aparece no `ps`); --password também funciona
export MDB_BRABO_PASSWORD='***'

# Modo DBA (habilita killOp, stepDown, profiler on/off, compact — sempre com confirmação)
mdb-brabo --host db01 --user dba --direct --dba

# Várias abas a partir de perfis salvos
mdb-brabo --start-saved all
mdb-brabo --start-saved NODE01,NODE02
mdb-brabo --list-saved

# Sem banco (aula, demonstração): tudo marcado como DEMO — DADOS SIMULADOS
mdb-brabo --demo

# Relatório sem TUI (cron/CI) — código de saída 0 = ok, 1 = WARNING, 2 = CRITICAL, 3 = falha
mdb-brabo --uri "..." --report health --report-format json --report-output /tmp/health.json
mdb-brabo --demo --report health

mdb-brabo --version
mdb-brabo --help
```

Opções: `--label` (nome da aba), `--refresh N` (segundos, padrão 5) e `--tls`. O entry point `mongodb-brabo` é um alias de `mdb-brabo`.

### Perfis salvos — `~/.mdb_brabo/connections.json`

```json
[
  {"label": "NODE01", "host": "192.168.60.1", "port": 27017, "user": "root_admin",
   "auth_db": "admin", "mode": "READ_ONLY", "direct": true}
]
```

**A senha nunca é gravada no arquivo.** Ordem de busca:

1. keyring do sistema (serviço `mdb_brabo`), se houver backend;
2. `MDB_BRABO_PASSWORD_<LABEL>` (ex.: `MDB_BRABO_PASSWORD_NODE01`);
3. `MDB_BRABO_PASSWORD`.

Um campo `password` encontrado no JSON é ignorado e gera um aviso no log.

Arquivos locais ficam em `~/.mdb_brabo/`: `mdb_brabo.log`, `history.db` e `connections.json`. A variável `MDB_BRABO_HOME` muda esse diretório.

---

## Segurança operacional

- **READ ONLY por padrão.** Os coletores só enviam comandos de leitura.
- Toda ação administrativa (`killOp`, `replSetStepDown`, profiler on/off, `compact`) passa por **um único caminho**: `check_action()` (modo, privilégio real e topologia) → **modal de confirmação mostrando o `runCommand` exato** → `ConnectionManager.execute_action()`, que recusa de novo se a sessão for READ ONLY.
- Em `--demo`, nenhuma ação é executada.
- O cliente usa timeouts curtos (seleção/conexão 5s, socket 15s), `maxTimeMS` em toda agregação e `maxPoolSize=4`. Um coletor lento nunca empilha outro ciclo sobre o servidor.

---

## Validado

Foi testado contra **MongoDB real** em 2026-10-23, nos containers LXD do lab MONGO SUPER DBA, com MongoDB **8.0.34** e usuário `root_admin` (role `root`). Tudo rodou em **READ ONLY**, sem ligar o profiler e sem matar operações.

| Alvo | Comando | Resultado |
|------|---------|-----------|
| `mongo-node-01` standalone (192.168.60.1) | `--uri ... --report health` | papel **STANDALONE**; F1/F2/F3/F5/F6/F9/F11 **OK**; F7/F10 **NOT AVAILABLE** ("Servidor STANDALONE — não faz parte de um replica set"); F8 **NOT AVAILABLE** ("não é um mongos"); Advisor: 0 CRITICAL, 0 WARNING, 2 INFO (profiler desligado, TLS desabilitado); **exit 0** |
| replica set `rs0` (192.168.60.2 PRIMARY e 192.168.60.1 SECONDARY) | `--host ... --direct --report health` | papel PRIMARY e SECONDARY detectados; F7/F10 **OK**; oplog de 990 MB; **exit 0** nos dois |
| `rs0` com um membro inalcançável (192.168.60.3) | TUI headless, F1→F12→h | todos os painéis renderizaram sem erro. **Bug encontrado e corrigido:** o membro DOWN reporta `optimeDate=1970-01-01`, o que gerava um "lag" de 56 anos; agora sai como lag desconhecido (teste de regressão `test_unreachable_member_epoch_optime_is_unknown_lag`) |
| PSA `rs1` (PRIMARY + SECONDARY + ARBITER 192.168.60.20) | `--uri ... --report health` | ARBITER reconhecido (lag "—"); **ajuste feito:** com o oplog recém-criado (2 min), a janela projetada disparava MDB-OPLOG-001; agora a projeção exige ≥ 1h observada. Depois do ajuste, com um membro fora durante a troca do lab: MDB-REPL-002 CRITICAL (estado real), **exit 2** |
| mongos (192.168.60.10) | `--uri ... --report health` | **pendente**: o mongos chegou a responder (hello `isdbgrid` + listShards OK) por alguns segundos, mas caiu antes do relatório rodar (o lab estava trocando de topologia). O relatório saiu com **exit 3** e a mensagem curta "Connection refused", que é o comportamento correto. O F8 foi validado só por testes unitários (`summarize_chunks`) e pelo `--demo` |

Suíte automatizada: `pytest`, com 40 testes (advisor, capabilities, transformações dos coletores, CLI/`--report` em demo, config/URI/senha, perfis salvos, histórico e um smoke da TUI Textual em `--demo` que navega F1..F12 + h e falha se algum painel logar erro).

---

## Arquitetura

Veja [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Em resumo: cada aba é uma `ConnectionSession` isolada (MongoClient + capabilities + cache + scheduler + advisor). Os coletores (pymongo síncrono, executados em threads) gravam num `MetricsCache`, os painéis só leem o cache e o Advisor só lê o cache.

---

## Autor

**Acácio Lima Rocha** — DBA BRABO

Licença [MIT](LICENSE).
