ARG BUILD_FROM
FROM $BUILD_FROM

# Install Node.js and dependencies
RUN apk add --no-cache \
    nodejs \
    npm \
    bash \
    curl \
    jq

# Create app directory
WORKDIR /app

# Copy package files and install dependencies
COPY server/package.json ./server/
RUN cd server && npm install --production

# Copy application files
COPY server/ ./server/
COPY viewer/ ./viewer/

# Copy run.sh as S6 legacy service (gets with-contenv and SUPERVISOR_TOKEN)
COPY run.sh /etc/services.d/pi-camera-stream/run
RUN chmod a+x /etc/services.d/pi-camera-stream/run

# Labels
LABEL \
    io.hass.name="Pi Camera Stream" \
    io.hass.description="Stream video from Pi cameras and Axis/RTSP network cameras" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"
