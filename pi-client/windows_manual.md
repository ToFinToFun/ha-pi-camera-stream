# JPsecurity - Installationsmanual för Windows

Denna guide beskriver hur du installerar och uppdaterar kamera-klienten på en Windows-dator. Klienten ansluter till dina nätverkskameror (t.ex. Axis) eller USB-kameror och strömmar videon säkert till din Home Assistant-server.

## Förberedelser

Innan du börjar behöver du:
1. **Python 3.9 eller nyare** installerat.
   - Ladda ner från [python.org](https://www.python.org/downloads/)
   - **Viktigt:** Kryssa i rutan *"Add Python to PATH"* under installationen.
2. **Server-adressen** till din Home Assistant (t.ex. `wss://cam.dindomän.se:2053`)
3. **Kamerahemligheten** (hittas i sidopanelen i viewer-appen i Home Assistant)

---

## Ny Installation

### 1. Ladda ner programvaran
Öppna PowerShell och kör följande kommandon för att ladda ner koden:

```powershell
cd C:\Users\$env:USERNAME
git clone https://github.com/ToFinToFun/ha-pi-camera-stream.git
cd ha-pi-camera-stream\pi-client
```

### 2. Uppdatera PowerShell (Rekommenderas)
För bästa kompatibilitet rekommenderas PowerShell 7. Installationsskriptet kommer automatiskt att fråga om du vill uppgradera om du kör en äldre version.
Svara `j` på frågan, följ installationsguiden som öppnas, och starta sedan om skriptet i den nya "PowerShell 7"-appen.

### 3. Kör installationsskriptet
Kör följande kommandon i PowerShell (i mappen `pi-client`):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_windows.ps1
```

### 4. Följ installationsguiden
Skriptet guidar dig genom 6 steg:

1. **Namnge klienten:** Ge datorn ett unikt namn i systemet (t.ex. "Stugan" eller "Kontoret").
2. **Beroenden:** Skriptet installerar automatiskt nödvändiga Python-paket.
3. **Kamerakonfiguration:** Välj vilken typ av kamera du vill lägga till (Axis, RTSP, USB eller Test). Du kan lägga till flera kameror.
4. **Serveranslutning:** Ange din Server-URL och Kamerahemlighet.
5. **Autostart:** Välj om kameran ska starta automatiskt när du loggar in.
   - *Nyhet:* Autostart använder nu Windows Startup-mappen och kräver **inte** administratörsrättigheter.
6. **Anslutningstest:** Skriptet testar automatiskt att servern kan nås, att lösenordet stämmer och att kameran fungerar.

När installationen är klar skapas en genväg på ditt skrivbord: **"JPsecurity - [Ditt Namn].bat"**.

---

## Uppdatera befintlig installation

När det finns nya funktioner eller buggfixar kan du enkelt uppdatera din befintliga installation utan att behöva skriva in alla inställningar igen.

### 1. Hämta senaste koden
Öppna PowerShell, gå till mappen där du installerade programmet och hämta den senaste koden:

```powershell
cd C:\Users\$env:USERNAME\ha-pi-camera-stream\pi-client
git pull
```

### 2. Kör installationsskriptet
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_windows.ps1
```

### 3. Välj uppdateringsmetod
Skriptet upptäcker automatiskt din befintliga konfiguration (`cameras.yaml`) och visar tre alternativ:

```text
  Vad vill du göra?
    1) Snabbuppdatering - behåll config, uppdatera beroenden och skript
    2) Ändra konfigurationen - gå igenom stegen (Enter = behåll värde)
    3) Ny installation - börja om från början
```

**Rekommendation:** Välj **1** för en snabbuppdatering. Detta uppdaterar alla bakomliggande filer och autostart-skript, men rör inte dina kamerainställningar.

### 4. Starta om klienten
Efter uppdateringen, dubbelklicka på genvägen på skrivbordet för att starta om kamera-klienten med den nya koden.

---

## Ändra kameror eller inställningar

Om du vill lägga till en ny kamera eller ändra IP-adressen på en befintlig, kör du installationsskriptet igen och väljer alternativ **2 (Ändra konfigurationen)**.

När du kommer till kamerasektionen får du fyra val:

```text
  Vill du behålla dessa kameror eller konfigurera om?
    1) Behåll befintliga kameror (Enter)
    2) Redigera befintliga kameror (ändra värden)
    3) Börja om med nya kameror
    4) Behåll befintliga + lägg till fler kameror
```

- Välj **2** om en kamera har bytt IP-adress. Skriptet visar de gamla värdena inom hakparenteser `[192.168.1.4]`. Tryck bara Enter för att behålla ett värde, eller skriv in ett nytt.
- Välj **4** om du har köpt en ny kamera och vill lägga till den utan att röra de befintliga.

---

## Felsökning

**Kameran syns inte i Home Assistant?**
1. Dubbelklicka på genvägen på skrivbordet för att se loggfönstret.
2. Leta efter felmeddelanden (t.ex. "Connection refused" eller "Authentication failed").
3. Kör anslutningstestet manuellt för att hitta felet:
   ```powershell
   python connection_test.py --config cameras.yaml
   ```

**Autostart fungerar inte?**
Autostart-genvägen ligger i din personliga Startup-mapp. Du kan kontrollera den genom att trycka `Win + R`, skriva `shell:startup` och trycka Enter. Där ska det finnas en genväg som heter "JPsecurity - [Ditt Namn]".

**Behöver jag öppna portar i min router?**
Nej. Kamera-klienten gör endast utgående anslutningar till din Home Assistant-server via WebSocket. Inga portar behöver öppnas på den plats där kameran är installerad.
