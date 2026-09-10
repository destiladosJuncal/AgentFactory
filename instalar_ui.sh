#!/bin/bash
set -e

# 🖥️  Instala la UI de escritorio como /Applications/AgenteDeepSeek.app
#
# No necesita sudo: el .app se crea en /Applications (escribible por el
# grupo admin) y NO toca /Applications/AgenteDeepSeek, que es de root.
# main_ui.py viaja adentro del .app y encuentra la instalación sola.

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

INSTALL_DIR="/Applications/AgenteDeepSeek"
APP="/Applications/AgenteDeepSeek.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$INSTALL_DIR/core/chat.py" ]; then
    echo -e "${RED}❌ No encuentro la instalación en $INSTALL_DIR${NC}"
    echo "   Corré primero ./instalar_interactivo.sh"
    exit 1
fi

if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    echo -e "${RED}❌ No encuentro $INSTALL_DIR/venv/bin/python${NC}"
    exit 1
fi

if ! "$INSTALL_DIR/venv/bin/python" -c "import tkinter" 2>/dev/null; then
    echo -e "${RED}❌ Ese Python no tiene tkinter compilado.${NC}"
    echo "   Probá: brew install python-tk  (o usá el python.org framework build)"
    exit 1
fi

echo -e "${BLUE}🖥️  Instalando UI en $APP${NC}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$SCRIPT_DIR/main_ui.py" "$APP/Contents/Resources/main_ui.py"

cat > "$APP/Contents/MacOS/AgenteDeepSeek" <<'LAUNCHER'
#!/bin/bash
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec /Applications/AgenteDeepSeek/venv/bin/python "$RES/main_ui.py" "$@"
LAUNCHER
chmod +x "$APP/Contents/MacOS/AgenteDeepSeek"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>AgenteDeepSeek</string>
    <key>CFBundleDisplayName</key>     <string>AgenteDeepSeek</string>
    <key>CFBundleExecutable</key>      <string>AgenteDeepSeek</string>
    <key>CFBundleIdentifier</key>      <string>local.agentedeepseek.ui</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
PLIST

echo -e "${GREEN}✅ Listo${NC}"
echo ""
echo -e "${BLUE}Abrila desde Finder/Launchpad como cualquier app, o:${NC}"
echo "   open -a AgenteDeepSeek"
echo ""
echo -e "${YELLOW}Para actualizar la UI después de editar main_ui.py, volvé a correr este script.${NC}"
