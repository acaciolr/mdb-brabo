#!/usr/bin/env bash
# MDB BRABO installer — cria .venv ao lado do código e instala em modo editável.
# Ubuntu / Debian / RHEL / Rocky / AlmaLinux / Oracle Linux. Não usa sudo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "MDB BRABO installer"
echo "==================="

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "python3 não encontrado. Instale Python 3.11+." >&2; exit 1; }
"$PY" - <<'PYEOF' || { echo "Python 3.11+ é obrigatório." >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PYEOF

if [ ! -x .venv/bin/python ]; then
    echo "Criando .venv ..."
    "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
echo "Instalando MDB BRABO (editável) + plotext + keyring ..."
.venv/bin/python -m pip install --quiet -e ".[full]"

echo
echo "Pronto. Rode com:"
echo "  $SCRIPT_DIR/.venv/bin/mdb-brabo --demo"
echo "  MDB_BRABO_PASSWORD=*** $SCRIPT_DIR/.venv/bin/mdb-brabo --host <host> --user <user> --direct"
echo "(ou: source .venv/bin/activate && mdb-brabo --help)"
