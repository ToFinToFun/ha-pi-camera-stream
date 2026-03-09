# Camera Client

Kamera-klient som strömmar video från nätverkskameror och USB-kameror till relay-servern via WebSocket. Fungerar på **Raspberry Pi**, **Windows** och **Linux x86/ARM**.

## Plattformsstöd

| Funktion | Raspberry Pi | Windows | Linux x86 |
|---|---|---|---|
| Axis nätverkskameror (VAPIX) | Ja | Ja | Ja |
| RTSP-kameror (Hikvision, Dahua, etc.) | Ja | Ja | Ja |
| USB-kameror (OpenCV) | Ja | Ja | Ja |
| Pi Camera Module (picamera2) | Ja | Nej | Nej |
| GPIO in/utgångar | Ja | Nej (simulering) | Nej (simulering) |
| GPIO scener | Ja | Nej | Nej |
| Rörelsedetektering | Ja | Ja | Ja |
| AI-objektdetektering | Ja | Ja | Ja |
| Lokal inspelning | Ja | Ja | Ja |
| PTZ-styrning (Axis) | Ja | Ja | Ja |
| Hårdvarudetektering | Ja | Ja | Ja |
| MQTT-events till HA | Ja (via server) | Ja (via server) | Ja (via server) |

## Installation

### Raspberry Pi

```bash
git clone https://github.com/ToFinToFun/ha-pi-camera-stream.git
cd ha-pi-camera-stream/pi-client
chmod +x install.sh
./install.sh
```

Installationsskriptet guidar dig interaktivt genom:
1. Namnge din Pi (unikt namn i systemet)
2. Välj kameratyp
3. Hårdvarudetektering och benchmark
4. Serveranslutning
5. Kamerakonfiguration
6. Systemd-tjänst (autostart)

### Windows

```powershell
git clone https://github.com/ToFinToFun/ha-pi-camera-stream.git
cd ha-pi-camera-stream\pi-client
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_windows.ps1
```

**Krav:** Python 3.9+ måste vara installerat. Ladda ner från [python.org](https://python.org) och kryssa i "Add Python to PATH".

Installationsskriptet guidar dig genom:
1. Namnge din klient
2. Installera Python-beroenden
3. Kamerakonfiguration
4. Serveranslutning
5. Startskript och Windows-tjänst

### Manuell installation

```bash
pip install websockets Pillow pyyaml requests numpy opencv-python-headless
python camera_client.py --config cameras.yaml
```

## Konfiguration

All konfiguration görs i `cameras.yaml`. Se `cameras.example.yaml` för fullständigt exempel.

### Serveranslutning

```yaml
server:
  url: "wss://cam.example.com:2053"
  secret: "din-hemliga-nyckel"

client_name: "garage"
```

### Kameror

```yaml
cameras:
  - type: axis
    camera_id: "garage-cam1"
    name: "Garage Kamera 1"
    host: "192.168.1.100"
    username: "root"
    password: "pass"
    port: 80
    mode: "snapshot"
    width: 1280
    height: 720
    fps: 10
    quality: 70
```

### GPIO (bara Raspberry Pi)

```yaml
gpio:
  inputs:
    - name: dorrsensor
      pin: 17
      active_low: true
      pull_up: true
  outputs:
    - name: belysning
      pin: 18
      active_low: false
  scenes:
    - name: lamna_huset
      label: "Lämna huset"
      actions:
        - output: belysning
          state: false
```

## Kommandon

### Raspberry Pi (systemd)

```bash
sudo systemctl start pi-camera    # Starta
sudo systemctl stop pi-camera     # Stoppa
sudo systemctl status pi-camera   # Status
journalctl -u pi-camera -f        # Loggar
```

### Windows

```powershell
.\start_camera.bat                 # Starta
# Eller manuellt:
python camera_client.py --config cameras.yaml
```

**Windows-tjänst** (körs i bakgrunden):
1. Ladda ner [NSSM](https://nssm.cc/download)
2. `nssm install CameraClient "python" "C:\path\to\camera_client.py" --config "C:\path\to\cameras.yaml"`
3. `nssm start CameraClient`

## Hårdvarudetektering

Kör standalone:
```bash
python hardware_detect.py
```

Visar:
- Pi-modell / Windows-version
- CPU, RAM, GPU
- Tillgängliga GPIO-pinnar (bara Pi)
- Rekommenderad detekteringsnivå
- Benchmark-resultat
