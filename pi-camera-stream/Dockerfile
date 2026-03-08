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

# Copy run script to S6-overlay services directory
COPY run.sh /etc/s6-overlay/s6-rc.d/pi-camera-stream/run
RUN chmod a+x /etc/s6-overlay/s6-rc.d/pi-camera-stream/run

# Register the service with S6
RUN echo "longrun" > /etc/s6-overlay/s6-rc.d/pi-camera-stream/type
RUN mkdir -p /etc/s6-overlay/s6-rc.d/user/contents.d
RUN touch /etc/s6-overlay/s6-rc.d/user/contents.d/pi-camera-stream

# Labels
LABEL \
    io.hass.name="Pi Camera Stream" \
    io.hass.description="Stream video from Pi cameras and Axis/RTSP network cameras" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"
