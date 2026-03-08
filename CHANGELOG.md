# Changelog

## [5.0.0] - 2026-03-08

### Added
- GPIO support: digital inputs (door sensors, PIR) and outputs (relays for lights, heating)
- Scenes: predefined GPIO combinations (e.g., "Stuga Vinter" = heating + lights)
- Two-way MQTT: GPIO inputs as binary_sensors, outputs as switches, scenes as buttons in HA
- Home Assistant can control GPIO outputs via MQTT
- Per-camera notification settings
- GPIO panel in viewer app with real-time status and scene buttons

### Changed
- Improved notification system with per-camera configuration
- Updated MQTT module with full bidirectional GPIO support

## [4.0.0] - 2026-03-08

### Added
- Local edge storage: recordings saved on Pi-client instead of HA server
- On-demand recording retrieval via viewer app
- Hardware auto-detection and benchmark at startup
- Auto-selection of optimal detection level based on hardware
- Remote detection level control from viewer app
- Storage statistics in dashboard

## [3.0.0] - 2026-03-08

### Added
- Home Assistant Add-on support with ingress
- MQTT integration for motion events as HA sensors
- JWT authentication with user roles (Admin/User/Guest)
- Docker Compose with Nginx + Let's Encrypt
- Motion detection and AI object detection (MobileNet/YOLOv8)
- Recording with timeline
- PTZ control for Axis cameras
- Adaptive quality based on bandwidth
- Push notifications (Telegram/Pushover)
- Dashboard with system health monitoring
- PWA support for mobile

## [2.0.0] - 2026-03-08

### Added
- Axis network camera support (VAPIX HTTP API)
- RTSP camera support
- Multi-camera per Pi-client
- Multi-camera grid view in viewer app
- YAML configuration file

## [1.0.0] - 2026-03-08

### Initial Release
- Basic WebSocket relay server
- Pi camera client with USB/test camera support
- Web viewer app
- Live video streaming without port forwarding
