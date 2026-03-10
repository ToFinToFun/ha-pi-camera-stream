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
#   Servern körs som ett HA-tillägg. Du behöver bara ange din server-adress
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

# ── Steg 1: Namnge denna Pi ────────────────────────────────────────
echo "[1/6] Namnge denna Pi"
echo ""

# Försök detektera Pi-modell
PI_MODEL="Okänd"
if [[ -f /proc/device-tree/model ]]; then
    PI_MODEL=$(cat /proc/device-tree/model | tr -d '\0')
    echo "  Detekterad modell: ${PI_MODEL}"
fi

DEFAULT_NAME=$(hostname)
echo "  Hostname: ${DEFAULT_NAME}"
echo ""
echo "  Varje Pi i systemet behöver ett unikt namn."
echo "  Exempel: garage, entre, stuga-norr, kontor"
echo ""
read -p "  Namn för denna Pi [${DEFAULT_NAME}]: " PI_NAME
PI_NAME=${PI_NAME:-${DEFAULT_NAME}}

echo "  ✓ Denna Pi heter: ${PI_NAME}"
echo ""

# ── Steg 2: Systemberoenden ────────────────────────────────────────
echo "[2/6] Installerar systemberoenden..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv ffmpeg

# ── Steg 3: Kameratyp ─────────────────────────────────────────────
echo ""
echo "[3/6] Kamerakonfiguration"
echo ""
echo "Vilka kameror ska denna Pi hantera?"
echo "  1) Axis nätverkskameror (VAPIX/RTSP)"
echo "  2) Andra RTSP-kameror (Hikvision, Dahua, etc.)"
echo "  3) Raspberry Pi Camera Module"
echo "  4) USB-kameror"
echo "  5) Blandning av ovanstående"
echo "  6) Inga kameror (bara GPIO/sensorer)"
echo "  7) Testläge (inga kameror, simulerad data)"
read -p "Välj [1-7]: " camera_choice

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
        echo "Bara GPIO/sensorer – inga kameraberoenden installeras."
        ;;
    7)
        echo "Testläge valt."
        ;;
    *)
        echo "Ogiltigt val, installerar OpenCV som standard."
        INSTALL_OPENCV=true
        ;;
esac

echo ""
echo "  Installerar kameraberoenden..."
if $INSTALL_OPENCV; then
    echo "  → Installerar OpenCV..."
    pip3 install opencv-python-headless
fi
if $INSTALL_PICAMERA; then
    echo "  → Installerar picamera2..."
    sudo apt-get install -y -qq python3-picamera2
fi

# Installera Python-beroenden
echo "  → Installerar Python-beroenden..."
pip3 install websockets Pillow pyyaml requests numpy

# ── Steg 4: Hårdvarudetektering ────────────────────────────────────
echo ""
echo "[4/6] Hårdvarudetektering & benchmark"
echo ""
echo "  Kör automatisk hårdvarudetektering..."
echo "  (detta tar ~10 sekunder vid första körning)"
echo ""

INSTALL_DIR="$(pwd)"
HW_RESULT=$(python3 "${INSTALL_DIR}/hardware_detect.py" 2>/dev/null || echo "FAILED")

if [[ "$HW_RESULT" == *"FAILED"* ]]; then
    echo "  ⚠ Hårdvarudetektering misslyckades, använder standardinställningar."
    RECOMMENDED_LEVEL=1
    GPIO_PINS=17
else
    echo "$HW_RESULT"
    # Extrahera rekommenderad nivå
    RECOMMENDED_LEVEL=$(echo "$HW_RESULT" | grep "Recommended level:" | grep -oP '\d+' | head -1)
    RECOMMENDED_LEVEL=${RECOMMENDED_LEVEL:-1}
fi

echo ""
read -p "  Vill du använda rekommenderad detekteringsnivå ${RECOMMENDED_LEVEL}? [J/n]: " use_recommended
if [[ "$use_recommended" == "n" || "$use_recommended" == "N" ]]; then
    read -p "  Ange detekteringsnivå (0-5): " RECOMMENDED_LEVEL
fi

# Installera AI-beroenden om nivå 3+
if [[ "$RECOMMENDED_LEVEL" -ge 3 ]]; then
    echo ""
    echo "  → Installerar AI-beroenden (detekteringsnivå ${RECOMMENDED_LEVEL})..."
    if [[ "$RECOMMENDED_LEVEL" -ge 4 ]]; then
        pip3 install ultralytics
    else
        # MobileNet behöver bara OpenCV
        if ! $INSTALL_OPENCV; then
            pip3 install opencv-python-headless
        fi
    fi
fi

# ── Steg 5: Serveranslutning & kameror ─────────────────────────────
echo ""
echo "[5/6] Serveranslutning"
echo ""
echo "  Du behöver:"
echo "    1. Server-adressen (t.ex. wss://cam.dindomän.se:2053)"
echo "    2. Kamerahemligheten (visas i viewer-appen → sidopanel)"
echo ""

read -p "  Server-URL (t.ex. wss://cam.example.com:2053): " SERVER_URL

# Validera URL
if [[ ! "$SERVER_URL" =~ ^wss?:// ]]; then
    echo "  ⚠ URL ska börja med ws:// eller wss://"
    read -p "  Använd SSL? [J/n]: " use_ssl
    if [[ "$use_ssl" == "n" || "$use_ssl" == "N" ]]; then
        SERVER_URL="ws://${SERVER_URL}"
    else
        SERVER_URL="wss://${SERVER_URL}"
    fi
    echo "  → URL: ${SERVER_URL}"
fi

read -p "  Kamerahemlighet (secret): " SECRET_KEY

# Skapa cameras.yaml
CONFIG_FILE="${INSTALL_DIR}/cameras.yaml"

echo ""
echo "  Skapar konfigurationsfil..."

cat > "${CONFIG_FILE}" << EOF
# Pi Camera Client – Konfiguration
# Genererad av install.sh $(date +%Y-%m-%d)
# Pi-namn: ${PI_NAME}
# Modell: ${PI_MODEL}

# ── Serveranslutning ──────────────────────────────────────────────
server:
  url: "${SERVER_URL}"
  secret: "${SECRET_KEY}"
  reconnect_delay: 1
  max_reconnect_delay: 60

# ── Pi-identitet ──────────────────────────────────────────────────
# Unikt namn för denna Pi i systemet
client_name: "${PI_NAME}"

# ── Kameror ───────────────────────────────────────────────────────
cameras:
EOF

# Detektera lokal IP och nätmask
LOCAL_NET=""
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -n "$LOCAL_IP" ]]; then
    # Hämta nätmask och prefix
    IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
    if [[ -n "$IFACE" ]]; then
        PREFIX=$(ip -4 addr show "$IFACE" | grep -oP 'inet \K[0-9./]+' | head -1 | cut -d/ -f2)
        SUBNET_MASK=$(python3 -c "import ipaddress; print(ipaddress.IPv4Network('0.0.0.0/$PREFIX').netmask)" 2>/dev/null || echo "255.255.255.0")
        NETWORK=$(python3 -c "import ipaddress; print(ipaddress.IPv4Network('${LOCAL_IP}/${PREFIX}', strict=False).network_address)" 2>/dev/null || echo "")
        LOCAL_NET="(nårbar från ${LOCAL_IP} / ${SUBNET_MASK})"
        echo ""
        echo -e "  \033[36mNätverksinfo:\033[0m"
        echo "    Denna enhet:  ${LOCAL_IP}"
        echo "    Nätmask:      ${SUBNET_MASK}"
        if [[ -n "$NETWORK" ]]; then
            echo "    Nätverk:      ${NETWORK}/${PREFIX}"
            echo -e "    \033[90mKameror bör vara i samma nätverk (${NETWORK}.x)\033[0m"
        fi
    fi
fi

# Lägg till kameror interaktivt (om inte bara GPIO)
if [[ "$camera_choice" != "6" ]]; then
    echo ""
    echo "  Nu lägger vi till kameror. Skriv 'klar' när du är färdig."
    echo ""

    cam_count=0
    while true; do
        cam_count=$((cam_count + 1))
        echo "  ── Kamera ${cam_count} ──"
        echo "    Typ: axis, rtsp, usb, picamera, test"
        read -p "    Typ (eller 'klar'): " cam_type

        [[ "$cam_type" == "klar" ]] && break

        # Generera unikt kamera-ID baserat på Pi-namn
        DEFAULT_CAM_ID="${PI_NAME}-cam${cam_count}"
        read -p "    Kamera-ID [${DEFAULT_CAM_ID}]: " cam_id
        cam_id=${cam_id:-${DEFAULT_CAM_ID}}

        DEFAULT_CAM_NAME="${PI_NAME} Kamera ${cam_count}"
        read -p "    Visningsnamn [${DEFAULT_CAM_NAME}]: " cam_name
        cam_name=${cam_name:-${DEFAULT_CAM_NAME}}

        case $cam_type in
            axis)
                read -p "    IP-adress ${LOCAL_NET}: " cam_host
                read -p "    Användarnamn [root]: " cam_user
                cam_user=${cam_user:-root}
                read -p "    Lösenord: " cam_pass
                read -p "    Upplösning bredd [1280]: " cam_w
                cam_w=${cam_w:-1280}
                read -p "    Upplösning höjd [720]: " cam_h
                cam_h=${cam_h:-720}
                read -p "    FPS [10]: " cam_fps
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
                read -p "    RTSP URL ${LOCAL_NET}: " cam_rtsp
                read -p "    FPS [10]: " cam_fps
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
                read -p "    Enhet (/dev/video0 = 0) [0]: " cam_dev
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
                echo "    Okänd typ, hoppar över."
                cam_count=$((cam_count - 1))
                continue
                ;;
        esac

        echo "    ✓ ${cam_name} tillagd!"
        echo ""
    done
else
    # Bara GPIO – lägg till en dummy-kamera för att registrera Pi:n
    cat >> "${CONFIG_FILE}" << EOF
  - type: test
    camera_id: "${PI_NAME}-gpio"
    name: "${PI_NAME} (GPIO)"
    width: 1
    height: 1
    fps: 0
    quality: 1
    gpio_only: true
EOF
fi

# Lägg till rörelsedetektering baserat på benchmark
cat >> "${CONFIG_FILE}" << EOF

# ── Rörelsedetektering ────────────────────────────────────────────
# Auto-konfigurerad baserat på hårdvarudetektering (nivå ${RECOMMENDED_LEVEL})
detection_level: ${RECOMMENDED_LEVEL}

motion:
  enabled: $([ "$RECOMMENDED_LEVEL" -ge 1 ] && echo "true" || echo "false")
  sensitivity: 50
  min_area_percent: 1.0
  min_frames: 2
  cooldown: 5

# AI Objektdetektering
object_detection:
  enabled: $([ "$RECOMMENDED_LEVEL" -ge 3 ] && echo "true" || echo "false")
  model: "$([ "$RECOMMENDED_LEVEL" -ge 4 ] && echo "yolov8" || echo "mobilenet")"
  confidence: 0.5
  detect_classes:
    - person
    - car

# ── GPIO ──────────────────────────────────────────────────────────
# Konfigurera GPIO-pinnar nedan. Tillgängliga pinnar (BCM):
#   4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27
# Reserverade (undvik): 0,1 (I2C0), 2,3 (I2C1), 7-11 (SPI), 14,15 (UART)
#
# Exempel:
# gpio:
#   inputs:
#     - name: dorrsensor_entre
#       pin: 17
#       active_low: true
#       pull_up: true
#     - name: pir_garage
#       pin: 27
#       active_low: false
#   outputs:
#     - name: belysning_entre
#       pin: 18
#       active_low: false
#     - name: larm_siren
#       pin: 23
#       active_low: false
#   scenes:
#     - name: lamna_huset
#       label: "Lämna huset"
#       icon: lock
#       description: "Släck allt och aktivera larm"
#       actions:
#         - output: belysning_entre
#           state: false
#         - output: larm_siren
#           state: false
gpio:
  inputs: []
  outputs: []
  scenes: []

# Push-notiser (rekommendation: använd HA:s notis-system via MQTT istället)
notifications:
  enabled: false
EOF

echo ""
echo "  ✓ Konfiguration sparad i ${CONFIG_FILE}"

# ── Steg 6: Systemd-tjänst ─────────────────────────────────────────
echo ""
echo "[6/6] Installerar systemd-tjänst..."

PYTHON_PATH="$(which python3)"

sudo tee /etc/systemd/system/pi-camera.service > /dev/null << EOF
[Unit]
Description=Pi Camera Client – ${PI_NAME} (HA Add-on)
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
Environment=PI_CLIENT_NAME=${PI_NAME}

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
echo "  Pi-namn:    ${PI_NAME}"
echo "  Modell:     ${PI_MODEL}"
echo "  Server:     ${SERVER_URL}"
echo "  Detektering: Nivå ${RECOMMENDED_LEVEL}"
echo "  Konfig:     ${CONFIG_FILE}"
echo ""
echo "Kommandon:"
echo "  Starta:       sudo systemctl start pi-camera"
echo "  Stoppa:       sudo systemctl stop pi-camera"
echo "  Status:       sudo systemctl status pi-camera"
echo "  Loggar:       journalctl -u pi-camera -f"
echo "  Redigera:     nano ${CONFIG_FILE}"
echo "  Hårdvarutest: python3 hardware_detect.py"
echo ""
echo "Manuell start:"
echo "  python3 camera_client.py --config ${CONFIG_FILE}"
echo ""
echo "Tips:"
echo "  • Konfigurera GPIO-pinnar i cameras.yaml"
echo "  • Pinnarna kan döpas om och aktiveras/inaktiveras från viewer-appen"
echo "  • Rörelsedetektering skickas till HA via MQTT automatiskt"
echo ""
