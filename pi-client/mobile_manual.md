# JPsecurity - Använd mobilen som övervakningskamera

Denna guide beskriver hur du använder en mobiltelefon, surfplatta eller bärbar dator som en extra övervakningskamera i ditt JPsecurity-system. 

Du behöver inte installera någon app från App Store eller Google Play. Systemet använder en så kallad PWA (Progressive Web App) som körs direkt i webbläsaren och kan sparas på hemskärmen som en vanlig app.

---

## Förberedelser

Innan du börjar behöver du:
1. **En mobil enhet** (iOS eller Android) med fungerande kamera.
2. **Server-adressen** till din Home Assistant (t.ex. `wss://cam.dindomän.se:2053`).
3. **Kamerahemligheten** (hittas i sidopanelen i viewer-appen i Home Assistant).

---

## Steg 1: Öppna webbappen

1. Öppna webbläsaren på din mobil (Safari på iPhone/iPad, Chrome på Android).
2. Gå till adressen för din viewer-app, men lägg till `/webcam.html` på slutet.
   - Exempel: `https://cam.dindomän.se:2053/viewer/webcam.html`
3. Sidan "Webbkamera - JPsecurity" kommer nu att laddas.

---

## Steg 2: Spara som app på hemskärmen (Rekommenderas)

För att kameran ska fungera bäst, inte stängas ner av misstag och se ut som en riktig app, bör du spara den på din hemskärm.

**På iPhone/iPad (Safari):**
1. Tryck på **Dela-knappen** (fyrkanten med en pil som pekar uppåt) i botten av skärmen.
2. Skrolla ner och välj **"Lägg till på hemskärmen"** (Add to Home Screen).
3. Tryck på **"Lägg till"** uppe till höger.
4. Stäng Safari och öppna den nya appen "JPsecurity" från din hemskärm.

**På Android (Chrome):**
1. Tryck på de **tre prickarna** uppe till höger i webbläsaren.
2. Välj **"Lägg till på startskärmen"** (Add to Home screen) eller "Installera app".
3. Följ instruktionerna på skärmen.
4. Stäng Chrome och öppna den nya appen "JPsecurity" från din hemskärm.

---

## Steg 3: Konfigurera kameran

När du har öppnat appen från hemskärmen möts du av en inställningsskärm. Fyll i följande:

1. **Server-URL:** Ange din WebSocket-adress (t.ex. `wss://cam.dindomän.se:2053`).
2. **Kamerahemlighet:** Ange din hemliga nyckel.
3. **Kameranamn:** Ge enheten ett namn som kommer att synas i systemet (t.ex. "Gammal iPhone" eller "Surfplatta Hallen").
4. **Kvalitet & FPS:** 
   - *Kvalitet:* "Medium" rekommenderas för bra balans mellan bild och bandbredd.
   - *FPS (Bilder per sekund):* "5 fps" är standard och räcker gott för övervakning.
5. **Kamera:** Välj om du vill använda bakkameran (Environment) eller framkameran/selfie (User).

Tryck sedan på den blå knappen **"Starta kamera"**.

*Notera: Första gången du gör detta kommer din telefon att fråga om appen får tillgång till kameran. Du måste svara **Ja/Tillåt**.*

---

## Steg 4: Kameran är igång!

När anslutningen lyckas ser du kamerabilden på skärmen och en röd "LIVE"-ikon. 

**Under tiden kameran är igång kan du:**
- **Byta kamera:** Använd rullgardinsmenyn för att växla mellan fram- och bakkamera i realtid.
- **Ändra kvalitet:** Justera FPS och bildkvalitet utan att behöva starta om.
- **Se statistik:** Längst ner ser du hur mycket data som skickats och aktuell FPS.
- **Se loggar:** Tryck på pappersikonen (📄) bredvid kameravalet för att se tekniska loggar om anslutningen skulle brytas.

**Viktigt att tänka på:**
- Skärmen måste vara igång och appen måste vara öppen på skärmen för att kameran ska fortsätta strömma. Om du låser telefonen eller byter till en annan app kommer videoströmmen att pausas av telefonens operativsystem.
- Om du ska använda enheten som en permanent kamera, rekommenderas att du ansluter en laddare och stänger av telefonens automatiska skärmlås (skärmsläckare).

---

## Felsökning

**"Kamerafel: NotAllowedError" eller svart skärm?**
Du har råkat neka appen tillgång till kameran. 
- *iPhone:* Gå till Inställningar -> Safari -> Kamera och välj "Fråga" eller "Tillåt".
- *Android:* Gå till Inställningar -> Appar -> Chrome -> Behörigheter och tillåt Kamera.

**Status står på "Ansluter..." men inget händer?**
1. Kontrollera att du har skrivit in exakt rätt Server-URL (glöm inte `wss://` i början och portnumret på slutet).
2. Kontrollera att du har internetuppkoppling på telefonen.
3. Tryck på pappersikonen (📄) för att se loggen. Står det "Authentication failed" har du skrivit in fel Kamerahemlighet.

**Bilden laggar eller är försenad?**
Sänk "Kvalitet" till Låg och "FPS" till 2 eller 5. Detta minskar mängden data som behöver skickas över ditt WiFi.
