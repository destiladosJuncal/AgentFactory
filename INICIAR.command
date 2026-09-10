#!/bin/bash
# ============================================================
#  AgentFactory — arranque portable para macOS (Apple Silicon)
#
#  Fork independiente de AgenteDeepSeek. NO usa ni toca esa instalación:
#  código, venv y datos son propios de AgentFactory.
#
#  Doble clic acá y listo. Si no hay un Python usable con tkinter, se baja
#  uno (~24 MB) y queda DENTRO de esta carpeta. No instala nada en el sistema.
# ============================================================
set -e

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

# --- Aislamiento del fork -------------------------------------------------
# Datos propios (conversaciones, proyectos, biblioteca, versiones y .env con
# las claves) separados de AgenteDeepSeek. El código soporta estos overrides.
export AGENTE_DATOS="$HOME/tmp/agentfactory"
export AGENTE_APP="$APP_DIR"

RUNTIME_DIR="$APP_DIR/runtime"
PY_STANDALONE="$RUNTIME_DIR/python/bin/python3"

VERSION_PY="3.12.13"
RELEASE="20260807"
URL_RUNTIME="https://github.com/astral-sh/python-build-standalone/releases/download/${RELEASE}/cpython-${VERSION_PY}+${RELEASE}-aarch64-apple-darwin-install_only.tar.gz"

VERDE='\033[0;32m'; AMARILLO='\033[1;33m'; ROJO='\033[0;31m'; AZUL='\033[0;34m'; NC='\033[0m'

echo ""
echo -e "${AZUL}🏭 AgentFactory${NC}"
echo ""

# --- ¿Sirve este Python? (3.9+ y con tkinter, que usa la interfaz) --------
python_sirve() {
    [ -x "$1" ] || return 1
    "$1" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
        >/dev/null 2>&1
}

buscar_python() {
    if python_sirve "$PY_STANDALONE"; then echo "$PY_STANDALONE"; return 0; fi
    for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
        if [ -n "$cand" ] && python_sirve "$cand"; then echo "$cand"; return 0; fi
    done
    if xcode-select -p >/dev/null 2>&1 && python_sirve /usr/bin/python3; then
        echo /usr/bin/python3; return 0
    fi
    return 1
}

PYTHON="$(buscar_python || true)"

if [ -z "$PYTHON" ]; then
    echo -e "${AMARILLO}No encontré un Python con tkinter en esta Mac.${NC}"
    echo -e "Voy a bajar uno (~24 MB) y dejarlo en esta carpeta."
    echo -e "${AZUL}No se instala nada en el sistema: todo queda acá adentro.${NC}"
    echo ""
    if [ "$(uname -m)" != "arm64" ]; then
        echo -e "${ROJO}❌ Esta versión es para Macs con Apple Silicon (M1 o posterior).${NC}"
        echo -e "   Tu Mac es $(uname -m)."
        echo ""; read -p "Enter para cerrar…" _; exit 1
    fi
    mkdir -p "$RUNTIME_DIR"
    echo -e "${AZUL}⬇️  Descargando Python ${VERSION_PY}…${NC}"
    if ! curl -fL --progress-bar "$URL_RUNTIME" -o "$RUNTIME_DIR/python.tar.gz"; then
        echo -e "${ROJO}❌ No pude descargarlo. ¿Hay conexión a internet?${NC}"
        echo ""; read -p "Enter para cerrar…" _; exit 1
    fi
    tar xzf "$RUNTIME_DIR/python.tar.gz" -C "$RUNTIME_DIR"
    rm -f "$RUNTIME_DIR/python.tar.gz"
    if ! python_sirve "$PY_STANDALONE"; then
        echo -e "${ROJO}❌ El Python descargado no funciona como esperaba.${NC}"
        echo ""; read -p "Enter para cerrar…" _; exit 1
    fi
    PYTHON="$PY_STANDALONE"
    echo -e "${VERDE}✅ Python listo${NC}"; echo ""
fi

# --- El resto ya es Python: entorno, dependencias y arranque --------------
exec "$PYTHON" "$APP_DIR/bootstrap.py" "$@"
