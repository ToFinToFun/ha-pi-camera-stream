#!/usr/bin/env bash
# ==============================================================================
# Pi Camera Stream - Home Assistant Add-on Startup Script
# Uses init: false, so we need to find SUPERVISOR_TOKEN ourselves
# ==============================================================================

set -e

echo "[run.sh] Starting Pi Camera Stream add-on v5.2.0..."

# ─── Find SUPERVISOR_TOKEN ───
# With init: false, S6 with-contenv doesn't run, so SUPERVISOR_TOKEN
# might not be in the environment. We check multiple locations.

if [ -z "$SUPERVISOR_TOKEN" ]; then
    # Try S6 container environment files (written by HA Supervisor)
    for TOKEN_FILE in \
        /run/s6/container_environment/SUPERVISOR_TOKEN \
        /var/run/s6/container_environment/SUPERVISOR_TOKEN \
        /run/s6-rc/container_environment/SUPERVISOR_TOKEN \
        /etc/s6-overlay/s6-rc.d/SUPERVISOR_TOKEN \
        /var/run/s6-rc/container_environment/SUPERVISOR_TOKEN; do
        if [ -f "$TOKEN_FILE" ]; then
            SUPERVISOR_TOKEN=$(cat "$TOKEN_FILE")
            echo "[run.sh] Found SUPERVISOR_TOKEN in $TOKEN_FILE"
            export SUPERVISOR_TOKEN
            break
        fi
    done
fi

# Try HASSIO_TOKEN as fallback
if [ -z "$SUPERVISOR_TOKEN" ] && [ -n "$HASSIO_TOKEN" ]; then
    SUPERVISOR_TOKEN="$HASSIO_TOKEN"
    echo "[run.sh] Using HASSIO_TOKEN as SUPERVISOR_TOKEN"
    export SUPERVISOR_TOKEN
fi

# Last resort: scan all S6 environment files
if [ -z "$SUPERVISOR_TOKEN" ]; then
    echo "[run.sh] Scanning for token in S6 directories..."
    TOKEN_FOUND=""
    for DIR in /run/s6* /var/run/s6* /etc/s6*; do
        if [ -d "$DIR" ]; then
            FOUND=$(find "$DIR" -name "SUPERVISOR_TOKEN" -type f 2>/dev/null | head -1)
            if [ -n "$FOUND" ]; then
                SUPERVISOR_TOKEN=$(cat "$FOUND")
                echo "[run.sh] Found SUPERVISOR_TOKEN in $FOUND"
                export SUPERVISOR_TOKEN
                TOKEN_FOUND="yes"
                break
            fi
        fi
    done
    
    if [ -z "$TOKEN_FOUND" ]; then
        echo "[run.sh] DEBUG: Listing /run/ contents:"
        ls -la /run/ 2>/dev/null || echo "  /run/ not accessible"
        echo "[run.sh] DEBUG: Listing /var/run/ contents:"
        ls -la /var/run/ 2>/dev/null || echo "  /var/run/ not accessible"
        echo "[run.sh] DEBUG: Looking for any token files:"
        find / -name "*SUPERVISOR*" -o -name "*HASSIO*" 2>/dev/null | head -20 || echo "  No token files found"
        echo "[run.sh] DEBUG: All environment variables:"
        env | sort
    fi
fi

if [ -n "$SUPERVISOR_TOKEN" ]; then
    echo "[run.sh] SUPERVISOR_TOKEN available (${#SUPERVISOR_TOKEN} chars)"
else
    echo "[run.sh] WARNING: No SUPERVISOR_TOKEN found anywhere"
fi

# ─── Read configuration from HA options ───
CONFIG_PATH=/data/options.json

if [ ! -f "$CONFIG_PATH" ]; then
    echo "[run.sh] ERROR: Options file not found at $CONFIG_PATH"
    exit 1
fi

CAMERA_SECRET=$(jq -r '.camera_secret // ""' "$CONFIG_PATH")
JWT_SECRET=$(jq -r '.jwt_secret // ""' "$CONFIG_PATH")
DIRECT_WS_URL=$(jq -r '.direct_ws_url // ""' "$CONFIG_PATH")
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
        echo "[run.sh] Generated new camera secret: $CAMERA_SECRET"
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

# ─── Get MQTT credentials ───
# Priority: 1) Manual config  2) Supervisor API  3) Fallback to core-mosquitto
MQTT_HOST=""
MQTT_PORT=""
MQTT_USER=""
MQTT_PASS=""

# Read manual MQTT settings from config
MQTT_HOST_MANUAL=$(jq -r '.mqtt_host // ""' "$CONFIG_PATH")
MQTT_PORT_MANUAL=$(jq -r '.mqtt_port // 1883' "$CONFIG_PATH")
MQTT_USER_MANUAL=$(jq -r '.mqtt_username // ""' "$CONFIG_PATH")
MQTT_PASS_MANUAL=$(jq -r '.mqtt_password // ""' "$CONFIG_PATH")

if [ "$MQTT_ENABLED" = "true" ]; then
    echo "[run.sh] MQTT is enabled, looking for broker..."
    
    # Method 1: Manual configuration
    if [ -n "$MQTT_HOST_MANUAL" ] && [ "$MQTT_HOST_MANUAL" != "null" ] && [ "$MQTT_HOST_MANUAL" != "" ]; then
        MQTT_HOST="$MQTT_HOST_MANUAL"
        MQTT_PORT="$MQTT_PORT_MANUAL"
        MQTT_USER="$MQTT_USER_MANUAL"
        MQTT_PASS="$MQTT_PASS_MANUAL"
        echo "[run.sh] Using manual MQTT config: ${MQTT_HOST}:${MQTT_PORT}"
    
    # Method 2: Supervisor API
    elif [ -n "$SUPERVISOR_TOKEN" ]; then
        echo "[run.sh] Querying Supervisor for MQTT service..."
        MQTT_RESPONSE=$(curl -s -f -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            http://supervisor/services/mqtt 2>&1 || echo '{"result":"error"}')
        echo "[run.sh] MQTT API response: $MQTT_RESPONSE"
        
        MQTT_RESULT=$(echo "$MQTT_RESPONSE" | jq -r '.result // "error"' 2>/dev/null || echo "error")
        
        if [ "$MQTT_RESULT" = "ok" ]; then
            MQTT_HOST=$(echo "$MQTT_RESPONSE" | jq -r '.data.host // ""' 2>/dev/null || echo "")
            MQTT_PORT=$(echo "$MQTT_RESPONSE" | jq -r '.data.port // "1883"' 2>/dev/null || echo "1883")
            MQTT_USER=$(echo "$MQTT_RESPONSE" | jq -r '.data.username // ""' 2>/dev/null || echo "")
            MQTT_PASS=$(echo "$MQTT_RESPONSE" | jq -r '.data.password // ""' 2>/dev/null || echo "")
            
            if [ -n "$MQTT_HOST" ] && [ "$MQTT_HOST" != "null" ]; then
                echo "[run.sh] MQTT broker found via API: ${MQTT_HOST}:${MQTT_PORT} (user: ${MQTT_USER})"
            else
                echo "[run.sh] MQTT API returned OK but no host. Trying fallback..."
                MQTT_HOST=""
            fi
        else
            echo "[run.sh] MQTT service not found via API. Trying fallback..."
        fi
    fi
    
    # Method 3: Fallback - try core-mosquitto (HA internal DNS name)
    if [ -z "$MQTT_HOST" ] || [ "$MQTT_HOST" = "null" ]; then
        echo "[run.sh] Trying fallback: core-mosquitto:1883..."
        if curl -s --connect-timeout 3 core-mosquitto:1883 >/dev/null 2>&1 || \
           nc -z -w3 core-mosquitto 1883 2>/dev/null; then
            MQTT_HOST="core-mosquitto"
            MQTT_PORT="1883"
            echo "[run.sh] Fallback success: core-mosquitto is reachable"
        else
            echo "[run.sh] Fallback failed: core-mosquitto not reachable. Disabling MQTT."
            MQTT_ENABLED="false"
        fi
    fi
fi

# ─── Get ingress entry path ───
INGRESS_PATH=""
if [ -n "$SUPERVISOR_TOKEN" ]; then
    ADDON_INFO=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        http://supervisor/addons/self/info 2>/dev/null || echo "{}")
    INGRESS_PATH=$(echo "$ADDON_INFO" | jq -r '.data.ingress_entry // ""' 2>/dev/null || echo "")
    echo "[run.sh] Ingress path: ${INGRESS_PATH}"
fi

# ─── Create directories ───
mkdir -p "${RECORDING_PATH}" 2>/dev/null || true
mkdir -p /data/db

# ─── Export environment variables ───
export CAMERA_SECRET
export JWT_SECRET
export DIRECT_WS_URL
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

echo "[run.sh] ============================================"
echo "[run.sh] Configuration:"
echo "[run.sh]   - Ingress path: ${INGRESS_PATH}"
echo "[run.sh]   - MQTT enabled: ${MQTT_ENABLED}"
if [ "$MQTT_ENABLED" = "true" ]; then
    echo "[run.sh]   - MQTT host: ${MQTT_HOST}:${MQTT_PORT}"
fi
echo "[run.sh]   - Recording enabled: ${RECORDING_ENABLED}"
echo "[run.sh]   - Recording path: ${RECORDING_PATH}"
echo "[run.sh]   - Log level: ${LOG_LEVEL}"
echo "[run.sh]   - Camera secret: ${CAMERA_SECRET:0:4}****"
if [ -n "$DIRECT_WS_URL" ] && [ "$DIRECT_WS_URL" != "null" ] && [ "$DIRECT_WS_URL" != "" ]; then
    echo "[run.sh]   - Direct WS URL: ${DIRECT_WS_URL}"
fi
echo "[run.sh] ============================================"

# ─── Start the relay server ───
echo "[run.sh] Starting relay server on port 8099..."
cd /app/server
exec node server.js
