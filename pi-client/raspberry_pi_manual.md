# JPsecurity - Komplett Installationsmanual för Raspberry Pi

Denna guide beskriver hur du sätter upp en helt ny Raspberry Pi från grunden, installerar kamera-klienten, konfigurerar kameror och GPIO (in/utgångar), samt hur du uppdaterar systemet.

Klienten ansluter till dina nätverkskameror (t.ex. Axis), USB-kameror eller Pi Camera Module och strömmar videon säkert till din Home Assistant-server.

---

## Del 1: Förbered Raspberry Pi (OS-installation)

Om du redan har en Raspberry Pi med Raspberry Pi OS installerat och anslutet till nätverket, kan du hoppa direkt till **Del 2**.

### 1. Ladda ner Raspberry Pi Imager
Ladda ner och installera [Raspberry Pi Imager](https://www.raspberrypi.com/software/) på din vanliga dator (Windows/Mac/Linux).

### 2. Skriv operativsystemet till SD-kortet
1. Sätt in ett MicroSD-kort i din dator.
2. Öppna Raspberry Pi Imager.
3. Klicka på **Välj OS** (Choose OS) -> **Raspberry Pi OS (other)** -> **Raspberry Pi OS Lite (64-bit)**.
   *(Lite-versionen rekommenderas eftersom vi inte behöver ett grafiskt gränssnitt, vilket sparar resurser).*
4. Klicka på **Välj lagring** (Choose Storage) och välj ditt SD-kort.
5. Klicka på **Nästa** (Next).

### 3. Konfigurera inställningar (Viktigt!)
När Imager frågar om du vill använda OS-anpassningsinställningar, klicka på **Redigera inställningar** (Edit settings).

Under fliken **Allmänt** (General):
- Sätt ett värdnamn (t.ex. `pikam-stugan`).
- Aktivera SSH (använd lösenordsautentisering).
- Skapa ett användarnamn och lösenord (t.ex. användarnamn `pi` och ett säkert lösenord).
- Konfigurera trådlöst nätverk (ange ditt WiFi-namn och lösenord).
- Ställ in din lokala tidszon (t.ex. `Europe/Stockholm`) och tangentbordslayout (`se`).

Klicka på **Spara** och sedan **Ja** för att skriva till SD-kortet.

### 4. Starta din Raspberry Pi
1. Sätt in SD-kortet i din Raspberry Pi.
2. Anslut strömkabeln.
3. Vänta ett par minuter medan den startar upp och ansluter till ditt WiFi.

### 5. Anslut via SSH
Öppna en terminal (Mac/Linux) eller PowerShell (Windows) på din dator och anslut till din Pi:

```bash
ssh pi@pikam-stugan.local
```
*(Byt ut `pi` och `pikam-stugan` mot det användarnamn och värdnamn du valde i steg 3).*

---

## Del 2: Installera Kamera-klienten

Nu när du är inloggad på din Raspberry Pi via SSH, är det dags att installera programvaran.

### 1. Ladda ner programvaran
Kör följande kommandon för att ladda ner koden:

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

1. **Namnge klienten:** Ge din Pi ett unikt namn i systemet (t.ex. "Stugan" eller "Garage").
2. **Beroenden:** Skriptet installerar automatiskt nödvändiga paket.
3. **Kamerakonfiguration:** Välj vilken typ av kamera du vill lägga till (Axis, RTSP, USB, Pi Camera Module eller Test). Du kan lägga till flera kameror.
4. **Hårdvarudetektering:** Skriptet testar din Pi:s prestanda för att rekommendera rätt nivå av AI-detektering (t.ex. nivå 3 för Pi 4, nivå 4 för Pi 5).
5. **Serveranslutning:** Ange din Server-URL (t.ex. `wss://cam.dindomän.se:2053`) och Kamerahemlighet (hittas i Home Assistant).
6. **Systemd-tjänst:** Skriptet skapar en bakgrundstjänst (`pi-camera.service`) så att kameran startar automatiskt när din Pi startas.
7. **Anslutningstest:** Skriptet testar automatiskt att servern kan nås, att lösenordet stämmer och att kameran fungerar.

---

## Del 3: Konfigurera GPIO (In/Utgångar)

En av de stora fördelarna med att använda en Raspberry Pi är möjligheten att ansluta fysiska sensorer och reläer via GPIO-pinnarna.

### 1. Öppna konfigurationsfilen
```bash
cd ~/ha-pi-camera-stream/pi-client
nano cameras.yaml
```

### 2. Aktivera och konfigurera GPIO
Leta upp sektionen `gpio:` i filen. Ändra `enabled: false` till `enabled: true`.

Här är ett exempel på hur du konfigurerar en dörrsensor (ingång) och ett relä för belysning (utgång):

```yaml
gpio:
  enabled: true
  
  inputs:
    - pin: 17
      name: "dorr_entre"
      active_low: true          # Magnetkontakt som bryter kretsen när dörren öppnas
      pull_up: true             # Använd Pi:ns inbyggda pull-up resistor
      debounce_ms: 200
      
  outputs:
    - pin: 18
      name: "belysning_ute"
      active_low: false         # HIGH = PÅ (vanligt för relämoduler)
      default_state: false      # Avstängd när Pi:n startar
      
  scenes:
    - name: "tänd_allt"
      label: "Tänd Utebelysning"
      icon: "lightbulb"
      description: "Tänder lampan vid entrén"
      actions:
        - output: "belysning_ute"
          state: true
```

*(Obs: Pin-nummer använder BCM-numrering, inte den fysiska pin-positionen på kortet. Se [pinout.xyz](https://pinout.xyz/) för en karta).*

Spara filen genom att trycka `Ctrl+O`, `Enter`, och sedan `Ctrl+X`.

### 3. Starta om tjänsten
För att GPIO-inställningarna ska börja gälla:
```bash
sudo systemctl restart pi-camera
```

Nu kommer dina GPIO-pinnar automatiskt att dyka upp i Home Assistant som sensorer och switchar!

---

## Del 4: Uppdatera systemet

När det finns nya funktioner eller buggfixar kan du enkelt uppdatera din installation.

### 1. Hämta senaste koden
Logga in på din Pi via SSH och kör:

```bash
cd ~/ha-pi-camera-stream/pi-client
git pull
```

### 2. Starta om tjänsten
Eftersom din konfiguration (`cameras.yaml`) redan finns kvar, behöver du bara starta om bakgrundstjänsten för att den ska börja använda den nya koden:

```bash
sudo systemctl restart pi-camera
```

### 3. Uppdatera Raspberry Pi OS (Rekommenderas ibland)
Det är också bra att hålla själva operativsystemet uppdaterat för säkerhetens skull:

```bash
sudo apt update
sudo apt upgrade -y
```

---

## Hantera tjänsten (Kommandon)

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

**Hög CPU-användning eller Pi:n blir väldigt varm?**
Om din Pi kämpar med prestandan kan du sänka AI-detekteringsnivån. Öppna `cameras.yaml` och ändra `detection_level` till `1` (enkel rörelse) eller `0` (avstängd), och starta sedan om tjänsten.

**GPIO fungerar inte?**
1. Kontrollera att du angett rätt BCM-pinnummer.
2. Kontrollera att `gpio: enabled: true` är satt i `cameras.yaml`.
3. Titta i loggarna (`journalctl -u pi-camera -f`) när tjänsten startar för att se om det uppstod några fel vid initiering av GPIO-pinnarna.
