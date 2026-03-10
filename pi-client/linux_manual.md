# JPsecurity - Installationsmanual för Linux

Denna guide beskriver hur du installerar och uppdaterar kamera-klienten på en vanlig Linux-dator (t.ex. Ubuntu, Debian, Linux Mint). Klienten ansluter till dina nätverkskameror (t.ex. Axis) eller USB-kameror och strömmar videon säkert till din Home Assistant-server.

## Förberedelser

Innan du börjar behöver du:
1. **En Linux-dator** med internetuppkoppling.
2. **Server-adressen** till din Home Assistant (t.ex. `wss://cam.dindomän.se:2053`)
3. **Kamerahemligheten** (hittas i sidopanelen i viewer-appen i Home Assistant)

---

## Ny Installation

### 1. Ladda ner programvaran
Öppna en terminal och kör följande kommandon för att ladda ner koden:

```bash
cd ~
git clone https://github.com/ToFinToFun/ha-pi-camera-stream.git
cd ha-pi-camera-stream/pi-client
```

### 2. Kör installationsskriptet
Gör skriptet körbart och starta det:

```bash
chmod +x install.sh
./install.sh
```

### 3. Följ installationsguiden
Skriptet guidar dig genom följande steg:

1. **Namnge klienten:** Ge datorn ett unikt namn i systemet (t.ex. "Server-rum" eller "Kontoret").
2. **Beroenden:** Skriptet installerar automatiskt nödvändiga paket via `apt` och `pip`.
3. **Kamerakonfiguration:** Välj vilken typ av kamera du vill lägga till (Axis, RTSP, USB eller Test). Du kan lägga till flera kameror.
4. **Hårdvarudetektering:** Skriptet testar din dators prestanda för att rekommendera rätt nivå av AI-detektering.
5. **Serveranslutning:** Ange din Server-URL och Kamerahemlighet.
6. **Systemd-tjänst:** Skriptet skapar en bakgrundstjänst (`pi-camera.service`) så att kameran startar automatiskt när datorn startas.
7. **Anslutningstest:** Skriptet testar automatiskt att servern kan nås, att lösenordet stämmer och att kameran fungerar.

---

## Hantera tjänsten (Autostart)

Installationsskriptet skapar en systemd-tjänst som körs i bakgrunden. Du kan hantera den med följande kommandon:

**Starta kameran:**
```bash
sudo systemctl start pi-camera
```

**Stoppa kameran:**
```bash
sudo systemctl stop pi-camera
```

**Se status:**
```bash
sudo systemctl status pi-camera
```

**Se loggar (felsökning):**
```bash
journalctl -u pi-camera -f
```

---

## Uppdatera befintlig installation

När det finns nya funktioner eller buggfixar kan du enkelt uppdatera din befintliga installation.

### 1. Hämta senaste koden
Öppna en terminal, gå till mappen där du installerade programmet och hämta den senaste koden:

```bash
cd ~/ha-pi-camera-stream/pi-client
git pull
```

### 2. Starta om tjänsten
Eftersom konfigurationen (`cameras.yaml`) redan finns kvar, behöver du bara starta om bakgrundstjänsten för att den ska börja använda den nya koden:

```bash
sudo systemctl restart pi-camera
```

---

## Ändra kameror eller inställningar

Om du vill lägga till en ny kamera eller ändra IP-adressen på en befintlig, redigerar du konfigurationsfilen manuellt.

### 1. Öppna konfigurationsfilen
```bash
cd ~/ha-pi-camera-stream/pi-client
nano cameras.yaml
```

### 2. Redigera filen
Filen är i YAML-format. Leta upp sektionen `cameras:` och ändra värdena. För att lägga till en ny kamera, kopiera ett befintligt block och ändra `camera_id`, `name` och IP-adress.

Exempel på en Axis-kamera:
```yaml
cameras:
  - type: axis
    camera_id: "axis-entre"
    name: "Entré"
    host: "192.168.1.100"
    username: "root"
    password: "ditt-lösenord"
    port: 80
    mode: "snapshot"
    width: 1280
    height: 720
    fps: 10
    quality: 70
```

Spara filen genom att trycka `Ctrl+O`, `Enter`, och sedan `Ctrl+X` för att avsluta nano.

### 3. Starta om tjänsten
För att de nya inställningarna ska börja gälla måste du starta om tjänsten:
```bash
sudo systemctl restart pi-camera
```

---

## Felsökning

**Kameran syns inte i Home Assistant?**
1. Kontrollera loggarna för att se om det finns några felmeddelanden:
   ```bash
   journalctl -u pi-camera -f
   ```
2. Leta efter fel som "Connection refused" eller "Authentication failed".
3. Kör anslutningstestet manuellt för att hitta felet:
   ```bash
   cd ~/ha-pi-camera-stream/pi-client
   python3 connection_test.py --config cameras.yaml
   ```

**Behöver jag öppna portar i min router?**
Nej. Kamera-klienten gör endast utgående anslutningar till din Home Assistant-server via WebSocket. Inga portar behöver öppnas på den plats där kameran är installerad.

**Hög CPU-användning?**
Om datorn blir varm eller långsam kan du sänka detekteringsnivån. Öppna `cameras.yaml` och ändra `detection_level` till `1` (enkel rörelse) eller `0` (avstängd), och starta sedan om tjänsten.
