#!/usr/bin/with-contenv bashio
# ==============================================================================
# Pi Camera Stream - Home Assistant Add-on Service Script
# ==============================================================================

bashio::log.info "Starting Pi Camera Stream add-on..."

# ─── Read configuration from HA options ───
CONFIG_PATH=/data/options.json

CAMERA_SECRET=$(bashio::config 'camera_secret')
JWT_SECRET=$(bashio::config 'jwt_secret')
MQTT_ENABLED=$(bashio::config 'mqtt_enabled')
MQTT_TOPIC_PREFIX=$(bashio::config 'mqtt_topic_prefix')
RECORDING_ENABLED=$(bashio::config 'recording_enabled')
RECORDING_PATH=$(bashio::config 'recording_path')
MAX_RECORDING_SIZE_MB=$(bashio::config 'max_recording_size_mb')
LOG_LEVEL=$(bashio::config 'log_level')

# Generate secrets if not set
if [ -z "$CAMERA_SECRET" ] || [ "$CAMERA_SECRET" = "null" ]; then
    if [ -f /data/camera_secret ]; then
        CAMERA_SECRET=$(cat /data/camera_secret)
    else
        CAMERA_SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
        echo "$CAMERA_SECRET" > /data/camera_secret
        bashio::log.info "Generated new camera secret. Configure your Pi clients with this secret."
        bashio::log.info "Camera secret: $CAMERA_SECRET"
    fi
fi

if [ -z "$JWT_SECRET" ] || [ "$JWT_SECRET" = "null" ]; then
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

if bashio::config.true 'mqtt_enabled'; then
    if bashio::services.available "mqtt"; then
        MQTT_HOST=$(bashio::services mqtt "host")
        MQTT_PORT=$(bashio::services mqtt "port")
        MQTT_USER=$(bashio::services mqtt "username")
        MQTT_PASS=$(bashio::services mqtt "password")
        bashio::log.info "MQTT broker found: ${MQTT_HOST}:${MQTT_PORT}"
    else
        bashio::log.warning "MQTT enabled but no MQTT broker found in Home Assistant"
    fi
fi

# ─── Get ingress entry path ───
INGRESS_PATH=$(bashio::addon.ingress_entry)
bashio::log.info "Ingress path: ${INGRESS_PATH}"

# ─── Get Supervisor token for HA API access ───
SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"

# ─── Create recording directory ───
mkdir -p "${RECORDING_PATH}"
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

bashio::log.info "Configuration loaded:"
bashio::log.info "  - Ingress path: ${INGRESS_PATH}"
bashio::log.info "  - MQTT enabled: ${MQTT_ENABLED}"
bashio::log.info "  - Recording enabled: ${RECORDING_ENABLED}"
bashio::log.info "  - Recording path: ${RECORDING_PATH}"
bashio::log.info "  - Log level: ${LOG_LEVEL}"

# ─── Start the relay server ───
bashio::log.info "Starting relay server on port 8099..."
cd /app/server
exec node server.js
