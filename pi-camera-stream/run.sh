#!/usr/bin/env bash
# ==============================================================================
# Pi Camera Stream - Home Assistant Add-on Startup Script
# ==============================================================================

set -e

echo "[INFO] Starting Pi Camera Stream add-on..."

# ─── Read configuration from HA options ───
CONFIG_PATH=/data/options.json

if [ ! -f "$CONFIG_PATH" ]; then
    echo "[ERROR] Options file not found at $CONFIG_PATH"
    exit 1
fi

echo "[DEBUG] Options file content:"
cat "$CONFIG_PATH" | jq '.' 2>/dev/null || cat "$CONFIG_PATH"

CAMERA_SECRET=$(jq -r '.camera_secret // ""' "$CONFIG_PATH")
JWT_SECRET=$(jq -r '.jwt_secret // ""' "$CONFIG_PATH")
MQTT_ENABLED=$(jq -r '.mqtt_enabled // false' "$CONFIG_PATH")
MQTT_TOPIC_PREFIX=$(jq -r '.mqtt_topic_prefix // "pi_camera_stream"' "$CONFIG_PATH")
RECORDING_ENABLED=$(jq -r '.recording_enabled // true' "$CONFIG_PATH")
RECORDING_PATH=$(jq -r '.recording_path // "/media/pi-camera-recordings"' "$CONFIG_PATH")
MAX_RECORDING_SIZE_MB=$(jq -r '.max_recording_size_mb // 1000' "$CONFIG_PATH")
LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG_PATH")

# Generate secrets if not set
if [ -z "$CAMERA_SECRET" ] || [ "$CAMERA_SECRET" = "null" ] || [ "$CAMERA_SECRET" = "" ]; then
    if [ -f /data/camera_secret ]; then
        CAMERA_SECRET=$(cat /data/camera_secret)
    else
        CAMERA_SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
        echo "$CAMERA_SECRET" > /data/camera_secret
        echo "[INFO] Generated new camera secret. Configure your Pi clients with this secret."
        echo "[INFO] Camera secret: $CAMERA_SECRET"
    fi
fi

if [ -z "$JWT_SECRET" ] || [ "$JWT_SECRET" = "null" ] || [ "$JWT_SECRET" = "" ]; then
    if [ -f /data/jwt_secret ]; then
        JWT_SECRET=$(cat /data/jwt_secret)
    else
        JWT_SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
        echo "$JWT_SECRET" > /data/jwt_secret
    fi
fi

# ─── Get MQTT credentials from HA if available ───
MQTT_HOST=""
MQTT_PORT=""
MQTT_USER=""
MQTT_PASS=""

if [ "$MQTT_ENABLED" = "true" ]; then
    echo "[INFO] MQTT is enabled, looking for broker..."
    
    if [ -n "$SUPERVISOR_TOKEN" ]; then
        echo "[DEBUG] Supervisor token available, querying services/mqtt..."
        MQTT_RESPONSE=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            http://supervisor/services/mqtt 2>/dev/null || echo '{"result":"error"}')
        echo "[DEBUG] MQTT API response: $MQTT_RESPONSE"
        
        MQTT_RESULT=$(echo "$MQTT_RESPONSE" | jq -r '.result // "error"' 2>/dev/null || echo "error")
        
        if [ "$MQTT_RESULT" = "ok" ]; then
            MQTT_HOST=$(echo "$MQTT_RESPONSE" | jq -r '.data.host // ""' 2>/dev/null || echo "")
            MQTT_PORT=$(echo "$MQTT_RESPONSE" | jq -r '.data.port // "1883"' 2>/dev/null || echo "1883")
            MQTT_USER=$(echo "$MQTT_RESPONSE" | jq -r '.data.username // ""' 2>/dev/null || echo "")
            MQTT_PASS=$(echo "$MQTT_RESPONSE" | jq -r '.data.password // ""' 2>/dev/null || echo "")
            
            if [ -n "$MQTT_HOST" ] && [ "$MQTT_HOST" != "null" ] && [ "$MQTT_HOST" != "" ]; then
                echo "[INFO] MQTT broker found: ${MQTT_HOST}:${MQTT_PORT} (user: ${MQTT_USER})"
            else
                echo "[WARN] MQTT API returned OK but no host found. Disabling MQTT."
                MQTT_ENABLED="false"
            fi
        else
            echo "[WARN] MQTT service not available from Supervisor API (result: $MQTT_RESULT)"
            echo "[WARN] Make sure Mosquitto broker add-on is installed and running."
            echo "[WARN] Disabling MQTT to prevent reconnect loops."
            MQTT_ENABLED="false"
        fi
    else
        echo "[WARN] MQTT enabled but no Supervisor token available. Disabling MQTT."
        MQTT_ENABLED="false"
    fi
fi

# ─── Get ingress entry path ───
INGRESS_PATH=""
if [ -n "$SUPERVISOR_TOKEN" ]; then
    ADDON_INFO=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        http://supervisor/addons/self/info 2>/dev/null || echo "{}")
    INGRESS_PATH=$(echo "$ADDON_INFO" | jq -r '.data.ingress_entry // ""' 2>/dev/null || echo "")
    echo "[INFO] Ingress path: ${INGRESS_PATH}"
fi

# ─── Create directories ───
mkdir -p "${RECORDING_PATH}" 2>/dev/null || true
mkdir -p /data/db

# ─── Export environment variables ───
export CAMERA_SECRET
export JWT_SECRET
export PORT=8099
export INGRESS_PATH
export SUPERVISOR_TOKEN
export HA_ADDON=true
export HA_AUTH_API=true
export MQTT_ENABLED
export MQTT_HOST
export MQTT_PORT
export MQTT_USER
export MQTT_PASS
export MQTT_TOPIC_PREFIX
export RECORDING_ENABLED
export RECORDING_PATH
export MAX_RECORDING_SIZE_MB
export LOG_LEVEL
export DB_PATH=/data/db
export NODE_ENV=production

echo "[INFO] ============================================"
echo "[INFO] Configuration:"
echo "[INFO]   - Ingress path: ${INGRESS_PATH}"
echo "[INFO]   - MQTT enabled: ${MQTT_ENABLED}"
if [ "$MQTT_ENABLED" = "true" ]; then
    echo "[INFO]   - MQTT host: ${MQTT_HOST}:${MQTT_PORT}"
fi
echo "[INFO]   - Recording enabled: ${RECORDING_ENABLED}"
echo "[INFO]   - Recording path: ${RECORDING_PATH}"
echo "[INFO]   - Log level: ${LOG_LEVEL}"
echo "[INFO]   - Camera secret: ${CAMERA_SECRET:0:4}****"
echo "[INFO] ============================================"

# ─── Start the relay server ───
echo "[INFO] Starting relay server on port 8099..."
cd /app/server
exec node server.js
