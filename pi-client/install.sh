#!/bin/bash
# ============================================================================
# Pi Camera Client – Installationsskript
# ============================================================================
# Kör detta skript på din Raspberry Pi för att installera kamera-klienten.
#
# Användning:
#   chmod +x install.sh
#   ./install.sh
#
# Anpassat för Home Assistant Add-on:
#   Servern körs som ett HA-tillägg. Du behöver bara ange din HA-adress
#   och kamerahemligheten (visas i HA-tilläggets viewer-app).
# ============================================================================

set -e

echo "============================================"
echo "  Pi Camera Client – Installation"
echo "  (Home Assistant Add-on Edition)"
echo "============================================"
echo ""

# Kontrollera att vi kör på Linux
if [[ "$(uname)" != "Linux" ]]; then
    echo "VARNING: Detta skript är designat för Linux/Raspberry Pi"
fi

# Installera systemberoenden
echo "[1/5] Installerar systemberoenden..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv ffmpeg

# Fråga om kameratyp
echo ""
echo "Vilka kameror ska denna Pi hantera?"
echo "  1) Axis nätverkskameror (VAPIX/RTSP)"
echo "  2) Andra RTSP-kameror (Hikvision, Dahua, etc.)"
echo "  3) Raspberry Pi Camera Module"
echo "  4) USB-kameror"
echo "  5) Blandning av ovanstående"
echo "  6) Testläge (inga kameror)"
read -p "Välj [1-6]: " camera_choice

INSTALL_OPENCV=false
INSTALL_PICAMERA=false

case $camera_choice in
    1|2|5)
        INSTALL_OPENCV=true
        if [[ "$camera_choice" == "5" ]]; then
            read -p "Har du en Pi Camera Module? [j/n]: " has_picam
            [[ "$has_picam" == "j" ]] && INSTALL_PICAMERA=true
        fi
        ;;
    3)
        INSTALL_PICAMERA=true
        ;;
    4)
        INSTALL_OPENCV=true
        ;;
    6)
        echo "Testläge valt."
        ;;
    *)
        echo "Ogiltigt val, installerar OpenCV som standard."
        INSTALL_OPENCV=true
        ;;
esac

echo "[2/5] Installerar kameraberoenden..."
if $INSTALL_OPENCV; then
    echo "  → Installerar OpenCV..."
    pip3 install opencv-python-headless
fi
if $INSTALL_PICAMERA; then
    echo "  → Installerar picamera2..."
    sudo apt-get install -y -qq python3-picamera2
fi

# Installera Python-beroenden
echo "[3/5] Installerar Python-beroenden..."
pip3 install websockets Pillow pyyaml requests

# Fråga om valfria funktioner
echo ""
read -p "Vill du aktivera AI-objektdetektering? (kräver ~200MB extra) [j/n]: " install_ai
if [[ "$install_ai" == "j" ]]; then
    echo "  → Installerar AI-beroenden..."
    pip3 install ultralytics
fi

# Konfiguration
echo ""
echo "[4/5] Konfiguration"
echo ""
echo "Du behöver:"
echo "  1. Din Home Assistant-adress (t.ex. homeassistant.local)"
echo "  2. Kamerahemligheten (visas i HA-tilläggets viewer → sidopanel)"
echo ""

read -p "Home Assistant adress (t.ex. homeassistant.local): " HA_HOST
read -p "Port (standard 8099, tryck Enter för standard): " HA_PORT
HA_PORT=${HA_PORT:-8099}

read -p "Använd SSL/WSS? [j/n]: " USE_SSL
if [[ "$USE_SSL" == "j" ]]; then
    WS_URL="wss://${HA_HOST}"
else
    WS_URL="ws://${HA_HOST}:${HA_PORT}"
fi

read -p "Kamerahemlighet (secret): " SECRET_KEY

# Skapa cameras.yaml
INSTALL_DIR="$(pwd)"
CONFIG_FILE="${INSTALL_DIR}/cameras.yaml"

echo ""
echo "Skapar konfigurationsfil..."

cat > "${CONFIG_FILE}" << EOF
# Pi Camera Client – Konfiguration
# Genererad av install.sh $(date +%Y-%m-%d)

server:
  url: "${WS_URL}"
  secret: "${SECRET_KEY}"
  reconnect_delay: 1
  max_reconnect_delay: 60

cameras:
EOF

# Lägg till kameror interaktivt
echo ""
echo "Nu lägger vi till kameror. Skriv 'klar' när du är färdig."
echo ""

cam_count=0
while true; do
    cam_count=$((cam_count + 1))
    echo "── Kamera ${cam_count} ──"
    echo "  Typ: axis, rtsp, usb, picamera, test"
    read -p "  Typ (eller 'klar'): " cam_type

    [[ "$cam_type" == "klar" ]] && break

    read -p "  Kamera-ID (t.ex. axis-entre): " cam_id
    read -p "  Namn (t.ex. Entré): " cam_name

    case $cam_type in
        axis)
            read -p "  IP-adress: " cam_host
            read -p "  Användarnamn [root]: " cam_user
            cam_user=${cam_user:-root}
            read -p "  Lösenord: " cam_pass
            read -p "  Upplösning bredd [1280]: " cam_w
            cam_w=${cam_w:-1280}
            read -p "  Upplösning höjd [720]: " cam_h
            cam_h=${cam_h:-720}
            read -p "  FPS [10]: " cam_fps
            cam_fps=${cam_fps:-10}

            cat >> "${CONFIG_FILE}" << EOF
  - type: axis
    camera_id: "${cam_id}"
    name: "${cam_name}"
    host: "${cam_host}"
    username: "${cam_user}"
    password: "${cam_pass}"
    port: 80
    mode: "snapshot"
    width: ${cam_w}
    height: ${cam_h}
    fps: ${cam_fps}
    quality: 70
EOF
            ;;
        rtsp)
            read -p "  RTSP URL: " cam_rtsp
            read -p "  FPS [10]: " cam_fps
            cam_fps=${cam_fps:-10}

            cat >> "${CONFIG_FILE}" << EOF
  - type: rtsp
    camera_id: "${cam_id}"
    name: "${cam_name}"
    rtsp_url: "${cam_rtsp}"
    width: 1280
    height: 720
    fps: ${cam_fps}
    quality: 70
EOF
            ;;
        usb)
            read -p "  Enhet (/dev/video0 = 0) [0]: " cam_dev
            cam_dev=${cam_dev:-0}

            cat >> "${CONFIG_FILE}" << EOF
  - type: usb
    camera_id: "${cam_id}"
    name: "${cam_name}"
    device: ${cam_dev}
    width: 640
    height: 480
    fps: 15
    quality: 70
EOF
            ;;
        picamera)
            cat >> "${CONFIG_FILE}" << EOF
  - type: picamera
    camera_id: "${cam_id}"
    name: "${cam_name}"
    width: 1280
    height: 720
    fps: 15
    quality: 70
EOF
            ;;
        test)
            cat >> "${CONFIG_FILE}" << EOF
  - type: test
    camera_id: "${cam_id}"
    name: "${cam_name}"
    width: 640
    height: 480
    fps: 5
    quality: 70
EOF
            ;;
        *)
            echo "  Okänd typ, hoppar över."
            cam_count=$((cam_count - 1))
            continue
            ;;
    esac

    echo "  ✓ ${cam_name} tillagd!"
    echo ""
done

# Lägg till rörelsedetektering
cat >> "${CONFIG_FILE}" << 'EOF'

# Rörelsedetektering (events skickas till HA via MQTT)
motion:
  enabled: false
  sensitivity: 50
  min_area_percent: 1.0
  min_frames: 2
  cooldown: 5

# Push-notiser (rekommendation: använd HA:s notis-system via MQTT istället)
notifications:
  enabled: false

# AI Objektdetektering
object_detection:
  enabled: false
  model: "mobilenet"
  confidence: 0.5
  detect_classes:
    - person
    - car
EOF

echo ""
echo "Konfiguration sparad i ${CONFIG_FILE}"

# Installera systemd-tjänst
echo "[5/5] Installerar systemd-tjänst..."

PYTHON_PATH="$(which python3)"

sudo tee /etc/systemd/system/pi-camera.service > /dev/null << EOF
[Unit]
Description=Pi Camera Streaming Client (HA Add-on)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/camera_client.py --config ${CONFIG_FILE}
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pi-camera.service

echo ""
echo "============================================"
echo "  Installation klar!"
echo "============================================"
echo ""
echo "Kommandon:"
echo "  Starta:       sudo systemctl start pi-camera"
echo "  Stoppa:       sudo systemctl stop pi-camera"
echo "  Status:       sudo systemctl status pi-camera"
echo "  Loggar:       journalctl -u pi-camera -f"
echo "  Redigera:     nano ${CONFIG_FILE}"
echo ""
echo "Manuell start:"
echo "  python3 camera_client.py --config ${CONFIG_FILE}"
echo ""
echo "Tips: Aktivera rörelsedetektering i cameras.yaml"
echo "      för att få motion-events i Home Assistant!"
echo ""
