#!/usr/bin/env bash
# ============================================================
#  AgentFactory — arranque portable para Linux (Debian/Ubuntu,
#  Fedora/RHEL, openSUSE y derivados).
#
#  Doble clic (o ./INICIAR.sh). Si no hay un Python 3.12+ con tkinter, se baja
#  uno (~30 MB) y queda DENTRO de esta carpeta. No instala nada en el sistema,
#  no pide sudo. Si borrás la carpeta, no queda rastro.
# ============================================================
set -e

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

# Datos propios (conversaciones, .env, captura, tareas) separados del código.
export AGENTE_DATOS="${AGENTE_DATOS:-$HOME/tmp/agentfactory}"
export AGENTE_APP="$APP_DIR"

RUNTIME_DIR="$APP_DIR/runtime"
PY_STANDALONE="$RUNTIME_DIR/python/bin/python3"

VERSION_PY="3.12.13"
RELEASE="20260807"

case "$(uname -m)" in
    x86_64|amd64)   ARCO="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64)  ARCO="aarch64-unknown-linux-gnu" ;;
    *)              ARCO="" ;;
esac
URL_RUNTIME="https://github.com/astral-sh/python-build-standalone/releases/download/${RELEASE}/cpython-${VERSION_PY}+${RELEASE}-${ARCO}-install_only.tar.gz"

VERDE='\033[0;32m'; AMARILLO='\033[1;33m'; ROJO='\033[0;31m'; AZUL='\033[0;34m'; NC='\033[0m'

echo ""
echo -e "${AZUL}🏭 AgentFactory${NC}"
echo ""

# --- ¿Sirve este Python? (3.12+ y con tkinter, que usa la interfaz) -------
# Mínimo 3.12 por mitmproxy (la captura). En muchas distros el Python del
# sistema NO trae tkinter salvo que se instale python3-tk / python3-tkinter:
# por eso se valida acá y, si falta, se baja el runtime propio.
python_sirve() {
    [ -x "$1" ] || return 1
    "$1" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
        >/dev/null 2>&1
}

buscar_python() {
    if python_sirve "$PY_STANDALONE"; then echo "$PY_STANDALONE"; return 0; fi
    for cand in /usr/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
        if [ -n "$cand" ] && python_sirve "$cand"; then echo "$cand"; return 0; fi
    done
    return 1
}

PYTHON="$(buscar_python || true)"

if [ -z "$PYTHON" ]; then
    echo -e "${AMARILLO}No encontré un Python 3.12+ con tkinter en este sistema.${NC}"
    echo -e "Voy a bajar uno (~30 MB) y dejarlo en esta carpeta."
    echo -e "${AZUL}No se instala nada en el sistema ni hace falta sudo.${NC}"
    echo ""
    if [ -z "$ARCO" ]; then
        echo -e "${ROJO}❌ No reconozco el procesador: $(uname -m).${NC}"
        echo -e "   Hay runtime para x86_64 (Intel/AMD) y aarch64 (ARM)."
        echo ""; read -p "Enter para cerrar…" _; exit 1
    fi
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        echo -e "${ROJO}❌ Necesito curl o wget para bajar el runtime.${NC}"
        echo ""; read -p "Enter para cerrar…" _; exit 1
    fi
    mkdir -p "$RUNTIME_DIR"
    echo -e "${AZUL}⬇️  Descargando Python ${VERSION_PY}…${NC}"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar "$URL_RUNTIME" -o "$RUNTIME_DIR/python.tar.gz" || {
            echo -e "${ROJO}❌ No pude descargarlo. ¿Hay conexión a internet?${NC}"
            read -p "Enter para cerrar…" _; exit 1; }
    else
        wget -q --show-progress "$URL_RUNTIME" -O "$RUNTIME_DIR/python.tar.gz" || {
            echo -e "${ROJO}❌ No pude descargarlo. ¿Hay conexión a internet?${NC}"
            read -p "Enter para cerrar…" _; exit 1; }
    fi
    tar xzf "$RUNTIME_DIR/python.tar.gz" -C "$RUNTIME_DIR"
    rm -f "$RUNTIME_DIR/python.tar.gz"
    if ! python_sirve "$PY_STANDALONE"; then
        echo -e "${ROJO}❌ El Python descargado no abre tkinter.${NC}"
        echo -e "   Suele faltar alguna librería de X11 del sistema. En Debian/Ubuntu:"
        echo -e "     sudo apt install libx11-6 libxext6 libxrender1 libxft2"
        echo -e "   En Fedora/RHEL:"
        echo -e "     sudo dnf install libX11 libXext libXrender libXft"
        echo ""; read -p "Enter para cerrar…" _; exit 1
    fi
    PYTHON="$PY_STANDALONE"
    echo -e "${VERDE}✅ Python listo${NC}"; echo ""
fi

# --- El resto ya es Python: entorno, dependencias y arranque --------------
exec "$PYTHON" "$APP_DIR/bootstrap.py" "$@"
