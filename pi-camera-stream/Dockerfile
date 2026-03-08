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

# Copy run script
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

# Start directly (init: false in config.yaml)
CMD ["/app/run.sh"]

# Labels
LABEL \
    io.hass.name="Pi Camera Stream" \
    io.hass.description="Stream video from Pi cameras and Axis/RTSP network cameras" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"
