# Pi Camera Stream - Home Assistant Add-on

**Version 5.0 (Smart Edge Edition)**

Ett komplett kamerasystem för Raspberry Pi med Home Assistant-integration. Stöder Axis nätverkskameror, RTSP-kameror, USB-kameror och Pi Camera Module. Kräver ingen port forwarding eller fast IP-adress.

---

## Arkitektur

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Home Assistant (hemma)                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Pi Camera Stream Add-on (relay-server)                      │   │
│  │  - WebSocket relay                                           │   │
│  │  - JWT-autentisering                                         │   │
│  │  - MQTT ↔ HA (sensorer, switchar, scener)                    │   │
│  │  - Viewer-app (ingress i HA:s sidopanel)                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  HTTPS/WSS (din befintliga HA-åtkomst)
            ┌─────────────┼─────────────┐
            │             │             │
     ┌──────┴──────┐ ┌───┴───┐ ┌───────┴──────┐
     │ Plats A:    │ │Plats B│ │  Din mobil    │
     │ RPi + Axis  │ │RPi +  │ │  (PWA-app)   │
     │ + GPIO      │ │kameror│ │              │
     └─────────────┘ └───────┘ └──────────────┘
```

## Funktioner

### Kamerastöd
- **Axis nätverkskameror** via VAPIX HTTP API (rekommenderas) eller RTSP
- **Generiska RTSP-kameror** (Hikvision, Dahua, Reolink, etc.)
- **USB-kameror** (Logitech, etc.)
- **Pi Camera Module** (v2/v3 via picamera2)
- **Multi-kamera** - en Pi kan hantera flera kameror samtidigt

### Detektering (auto-konfigureras efter hårdvara)
- **Nivå 0**: Ingen detektering
- **Nivå 1**: Enkel rörelsedetektering (alla enheter)
- **Nivå 2**: Avancerad rörelsedetektering med zoner (alla enheter)
- **Nivå 3**: AI Lite - MobileNet SSD (RPi 4+)
- **Nivå 4**: AI Full - YOLOv8 (RPi 5 / Orange Pi 5)
- **Nivå 5**: AI Accelererad - Coral TPU / NPU (dedikerad hårdvara)

Systemet kör automatiskt ett benchmark vid start och väljer den bästa nivån. Du kan ändra nivå per kamera via viewer-appen eller HA.

### Lokal lagring (Edge Storage)
- Inspelningar sparas lokalt på Pi-klienten (USB-minne/SSD)
- On-demand-hämtning via viewer-appen (sparar bandbredd)
- Automatisk rensning av gamla inspelningar
- Pre-record buffer (fångar sekunder innan rörelse)

### GPIO - Digitala in/utgångar
- **Ingångar**: Dörrsensorer, PIR-rörelsesensorer, larmknappar
- **Utgångar**: Reläer för belysning, sirener, värme, lås
- **Scener**: Fördefinierade kombinationer (t.ex. "Stuga Vinter" = värme + lampor)
- **Tvåvägs MQTT**: Alla GPIO-pins syns i HA som sensorer/switchar
- **HA kan styra utgångar**: Använd HA-automationer för att styra reläer via MQTT

### Home Assistant-integration (MQTT)
Följande entiteter skapas automatiskt i HA via MQTT Discovery:

| Entitetstyp | Exempel | Beskrivning |
|---|---|---|
| `binary_sensor` | `binary_sensor.entre_cam_motion` | Rörelsedetektering |
| `binary_sensor` | `binary_sensor.entre_cam_dorr_entre` | GPIO-ingång (dörrsensor) |
| `switch` | `switch.entre_cam_belysning_entre` | GPIO-utgång (reläer) |
| `button` | `button.entre_cam_stuga_vinter` | Scen-aktivering |
| `sensor` | `sensor.entre_cam_fps` | Kamera FPS |

### Viewer-app (PWA)
- **Live-video** från alla kameror i rutnät (1x1, 2x2, 3x3, spotlight)
- **Fullskärm** per kamera (dubbelklick)
- **Dashboard** med CPU-temp, minne, disk, bandbredd per Pi
- **Tidslinje** med inspelningar (on-demand från Pi-klienten)
- **GPIO-panel** med realtidsstatus, utgångsstyrning och scen-knappar
- **Admin-panel** för användare, kameror och detekteringsnivå
- **PWA** - installera på mobilen som en riktig app
- **Notis-inställningar** per kamera

### Säkerhet
- JWT-autentisering med roller (Admin/User/Guest)
- HA Ingress-stöd (automatisk inloggning via HA)
- Krypterad kommunikation via din befintliga HTTPS-setup
- Kamera-secret för Pi-klient-autentisering

---

## Installation

### 1. Home Assistant Add-on

1. Kopiera mappen `pi-camera-stream-addon` till din HA:s `addons`-mapp:
   ```bash
   # Via Samba
   cp -r pi-camera-stream-addon //homeassistant/addons/

   # Eller via SSH
   scp -r pi-camera-stream-addon root@homeassistant:/addons/
   ```

2. Gå till **Inställningar -> Tillägg -> Tillägg-butik** i HA

3. Klicka på de tre prickarna uppe till höger och välj **Ladda om**

4. Hitta "Pi Camera Stream" under **Lokala tillägg** och klicka **Installera**

5. Konfigurera tillägget:
   - `camera_secret`: Välj en stark hemlig nyckel (delas med Pi-klienterna)
   - `mqtt_host`: Din MQTT-brokers adress (vanligtvis `core-mosquitto`)
   - `max_viewers_per_camera`: Max antal tittare per kamera

6. Starta tillägget och aktivera **"Visa i sidopanelen"**

### 2. Raspberry Pi-klient

1. Kopiera mappen `pi-client` till din Raspberry Pi:
   ```bash
   scp -r pi-client pi@192.168.1.100:~/pi-camera/
   ```

2. Kör installationsskriptet:
   ```bash
   cd ~/pi-camera
   chmod +x install.sh
   ./install.sh
   ```

3. Skapa konfigurationsfil:
   ```bash
   cp cameras.example.yaml cameras.yaml
   nano cameras.yaml
   ```

4. Fyll i din konfiguration (se exempel nedan)

5. Starta tjänsten:
   ```bash
   sudo systemctl start pi-camera
   sudo systemctl enable pi-camera  # Autostart vid boot
   ```

---

## Konfigurationsexempel

### cameras.yaml - Komplett exempel

```yaml
server:
  url: "wss://din-ha-adress.se:8099"
  secret: "din-hemliga-nyckel"

cameras:
  # Axis nätverkskamera (VAPIX - rekommenderas)
  - type: axis
    camera_id: "axis-entre"
    name: "Entré"
    host: "192.168.1.100"
    username: "root"
    password: "ditt-lösenord"
    port: 80
    mode: snapshot
    width: 1280
    height: 720
    fps: 10
    ptz:
      enabled: true

  # Generisk RTSP-kamera
  - type: rtsp
    camera_id: "hikvision-garage"
    name: "Garage"
    rtsp_url: "rtsp://admin:pass@192.168.1.101:554/Streaming/Channels/101"
    width: 1280
    height: 720
    fps: 10

  # USB-kamera
  - type: usb
    camera_id: "usb-kontor"
    name: "Kontoret"
    device: 0
    width: 640
    height: 480
    fps: 15

# GPIO-konfiguration
gpio:
  enabled: true
  inputs:
    - pin: 17
      name: "dorr_entre"
      active_low: true
      pull_up: true
      debounce_ms: 200
    - pin: 27
      name: "pir_baksida"
      active_low: false
      debounce_ms: 500
  outputs:
    - pin: 18
      name: "belysning_entre"
      active_low: false
      default_state: false
    - pin: 23
      name: "varme_stuga"
      active_low: false
      default_state: false
  scenes:
    - name: "stuga_vinter"
      label: "Stuga Vinter"
      icon: "snowflake"
      description: "Slå på värme och belysning"
      actions:
        - output: "varme_stuga"
          state: true
        - output: "belysning_entre"
          state: true
    - name: "allt_av"
      label: "Allt Av"
      icon: "power"
      description: "Stäng av allt"
      actions:
        - output: "varme_stuga"
          state: false
        - output: "belysning_entre"
          state: false

# Detektering (auto = automatiskt val baserat på hårdvara)
detection:
  auto: true
  # level: 2  # Manuellt override (0-5)

# Lokal inspelning
recording:
  enabled: true
  storage_path: "/var/lib/pi-camera/recordings"
  max_storage_mb: 5000
  max_age_days: 30
  pre_record_seconds: 5
  post_record_seconds: 10

# Push-notiser
notifications:
  enabled: true
  telegram:
    enabled: true
    bot_token: "din-bot-token"
    chat_id: "ditt-chat-id"
  pushover:
    enabled: false
    user_key: ""
    api_token: ""
```

---

## Home Assistant Automationer

### Exempel: Tänd lampor vid rörelse nattetid

```yaml
automation:
  - alias: "Tänd entre-belysning vid rörelse"
    trigger:
      - platform: state
        entity_id: binary_sensor.entre_cam_motion
        to: "on"
    condition:
      - condition: time
        after: "22:00:00"
        before: "06:00:00"
    action:
      - service: switch.turn_on
        entity_id: switch.entre_cam_belysning_entre
      - delay: "00:05:00"
      - service: switch.turn_off
        entity_id: switch.entre_cam_belysning_entre
```

### Exempel: Aktivera stuga-scen via HA

```yaml
automation:
  - alias: "Värm stugan före ankomst"
    trigger:
      - platform: zone
        entity_id: device_tracker.min_telefon
        zone: zone.stuga_nara
        event: enter
    action:
      - service: button.press
        entity_id: button.stuga_cam_stuga_vinter
```

### Exempel: Skicka notis vid dörröppning

```yaml
automation:
  - alias: "Dörr öppnad - notis"
    trigger:
      - platform: state
        entity_id: binary_sensor.entre_cam_dorr_entre
        to: "on"
    action:
      - service: notify.mobile_app_min_telefon
        data:
          title: "Dörr öppnad"
          message: "Entrédörren öppnades {{ now().strftime('%H:%M') }}"
```

---

## Felsökning

### Pi-klient loggar
```bash
sudo journalctl -u pi-camera -f
```

### Kör hårdvarudetektering manuellt
```bash
python3 camera_client.py --detect-hardware
```

### Testa anslutning
```bash
python3 camera_client.py --server wss://din-ha.se:8099 --secret din-nyckel --type test --camera-id test-1
```

### Kameran dyker inte upp i HA?
- Kontrollera att `camera_secret` matchar exakt
- Kontrollera Pi-klientens logg: `journalctl -u pi-camera -f`
- Kontrollera att port 8099 är öppen i HA-tilläggets konfiguration

### Hög CPU-användning på Pi?
- Sänk detekteringsnivån via Admin-fliken i viewer-appen
- Sänk FPS eller upplösning i `cameras.yaml`

### GPIO fungerar inte?
- Kontrollera att `gpio.enabled: true` i `cameras.yaml`
- Kontrollera att RPi.GPIO eller gpiozero är installerat: `pip3 install RPi.GPIO`
- Kontrollera pin-numrering (BCM-numrering, inte fysisk pin)

---

## Filstruktur

```
ha-pi-camera-stream/
├── pi-camera-stream-addon/          # HA Add-on
│   ├── config.yaml                  # HA add-on konfiguration
│   ├── build.yaml                   # Multi-arch build
│   ├── Dockerfile                   # Container-definition
│   ├── run.sh                       # Startskript
│   ├── translations/                # Språkfiler (en/sv)
│   ├── server/                      # Relay-server (Node.js)
│   │   ├── server.js                # Huvudserver
│   │   ├── auth.js                  # JWT-autentisering
│   │   ├── recordings.js            # Inspelningshantering
│   │   ├── mqtt_ha.js               # MQTT ↔ HA integration
│   │   └── package.json
│   └── viewer/                      # Viewer-webapp
│       ├── index.html               # Komplett SPA
│       ├── manifest.json            # PWA manifest
│       └── sw.js                    # Service worker
│
├── pi-client/                       # Raspberry Pi-klient
│   ├── camera_client.py             # Huvudklient
│   ├── motion_detector.py           # Rörelsedetektering
│   ├── object_detector.py           # AI-objektdetektering
│   ├── hardware_detect.py           # Hårdvaru-autodetektering
│   ├── local_recorder.py            # Lokal inspelning
│   ├── gpio_controller.py           # GPIO in/utgångar
│   ├── ptz_controller.py            # PTZ-styrning (Axis)
│   ├── notifications.py             # Push-notiser
│   ├── cameras.example.yaml         # Exempelkonfiguration
│   ├── requirements.txt             # Python-beroenden
│   ├── install.sh                   # Installationsskript
│   └── pi-camera.service            # systemd service
│
└── README.md                        # Denna fil
```

---

## Licens

MIT License - Fritt att använda, modifiera och distribuera.
