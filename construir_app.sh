#!/bin/bash
set -e
# ============================================================
#  Recompila AgentFactory.app (bundle + ícono) desde cero.
#  Correr después de cualquier cambio para tener el .app al día.
# ============================================================
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
APP="$APP_DIR/AgentFactory.app"
PY="$APP_DIR/venv/bin/python"
[ -x "$PY" ] || PY="python3"

VERDE='\033[0;32m'; AZUL='\033[0;34m'; NC='\033[0m'
echo -e "${AZUL}🏭 Recompilando AgentFactory.app${NC}"

# --- 1) ícono .icns desde el logo nuevo (1024) ---
SRC_ICON="$APP_DIR/agentfactory-icon-1024.png"
ICONSET="/tmp/AgentFactory.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  "$PY" -c "from PIL import Image; Image.open('$SRC_ICON').resize(($s,$s),Image.LANCZOS).save('$ICONSET/icon_${s}x${s}.png')"
  d=$((s*2))
  "$PY" -c "from PIL import Image; Image.open('$SRC_ICON').resize(($d,$d),Image.LANCZOS).save('$ICONSET/icon_${s}x${s}@2x.png')"
done
iconutil -c icns "$ICONSET" -o "$APP_DIR/AppIcon.icns"
echo -e "${VERDE}✅ AppIcon.icns${NC}"

# --- 2) el runtime pinta el Dock/cmd+tab desde icono.png: que sea el mismo ---
cp "$SRC_ICON" "$APP_DIR/icono.png"

# --- 3) (re)armar el bundle ---
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$APP_DIR/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/MacOS/AgentFactory" <<'LAUNCHER'
#!/bin/bash
# El .app vive DENTRO de la carpeta AgentFactory. Sube tres niveles y delega
# en INICIAR.command (venv + dependencias + main_ui.py).
RAIZ="$(cd "$(dirname "$0")/../../.." && pwd)"
if [ -x "$RAIZ/INICIAR.command" ]; then
    exec "$RAIZ/INICIAR.command" "$@"
fi
osascript -e 'display alert "AgentFactory" message "No encuentro INICIAR.command junto al .app."'
exit 1
LAUNCHER
chmod +x "$APP/Contents/MacOS/AgentFactory"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>AgentFactory</string>
    <key>CFBundleDisplayName</key>       <string>AgentFactory</string>
    <key>CFBundleExecutable</key>        <string>AgentFactory</string>
    <key>CFBundleIconFile</key>          <string>AppIcon</string>
    <key>CFBundleIdentifier</key>        <string>local.agentfactory.app</string>
    <key>CFBundleVersion</key>           <string>0.1</string>
    <key>CFBundleShortVersionString</key><string>0.1</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>NSHighResolutionCapable</key>   <true/>
    <key>LSMinimumSystemVersion</key>    <string>11.0</string>
</dict>
</plist>
PLIST

# --- 4) refrescar caches de ícono (Finder/Dock) ---
touch "$APP"
touch "$APP/Contents/Info.plist"
/usr/bin/killall Dock 2>/dev/null || true

echo -e "${VERDE}✅ AgentFactory.app listo en $APP${NC}"
