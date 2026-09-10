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

# Se elige el artefacto según el procesador. Antes estaba fijo en aarch64 y el
# script rechazaba de plano cualquier Mac Intel; python-build-standalone
# publica las dos, así que no hay razón para dejar afuera esas máquinas.
case "$(uname -m)" in
    arm64)  ARCO="aarch64-apple-darwin" ;;
    x86_64) ARCO="x86_64-apple-darwin" ;;
    *)      ARCO="" ;;
esac
URL_RUNTIME="https://github.com/astral-sh/python-build-standalone/releases/download/${RELEASE}/cpython-${VERSION_PY}+${RELEASE}-${ARCO}-install_only.tar.gz"

VERDE='\033[0;32m'; AMARILLO='\033[1;33m'; ROJO='\033[0;31m'; AZUL='\033[0;34m'; NC='\033[0m'

echo ""
echo -e "${AZUL}🏭 AgentFactory${NC}"
echo ""

# --- ¿Sirve este Python? (3.12+ y con tkinter, que usa la interfaz) -------
# El mínimo es 3.12 y no 3.9 por mitmproxy (la captura de tráfico), que pide
# 3.12+. Con el mínimo en 3.9, un Python 3.10 del sistema pasaba este control
# y después reventaba en pip, que es un lugar mucho peor para enterarse.
python_sirve() {
    [ -x "$1" ] || return 1
    "$1" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
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
    if [ -z "$ARCO" ]; then
        echo -e "${ROJO}❌ No reconozco el procesador de esta Mac: $(uname -m).${NC}"
        echo -e "   Hay runtime para Apple Silicon (arm64) e Intel (x86_64)."
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
