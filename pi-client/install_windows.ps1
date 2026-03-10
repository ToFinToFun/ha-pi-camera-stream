# ============================================================================
# Camera Client - Windows Installationsskript (PowerShell)
# ============================================================================
# Kor detta skript pa din Windows-dator for att installera kamera-klienten.
#
# Anvandning:
#   1. Oppna PowerShell som administratör
#   2. Kor: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   3. Kor: .\install_windows.ps1
#
# Krav: Python 3.9+ maste vara installerat
# ============================================================================

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Camera Client - Windows Installation" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- Kontrollera Python --
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 9) {
                $pythonCmd = $cmd
                Write-Host "  Python hittad: $ver ($cmd)" -ForegroundColor Green
                break
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "  FEL: Python 3.9+ kravs. Ladda ner fran https://python.org" -ForegroundColor Red
    Write-Host "  Se till att kryssa i 'Add Python to PATH' vid installation." -ForegroundColor Yellow
    exit 1
}

# -- Steg 1: Namnge denna klient --
Write-Host ""
Write-Host "[1/5] Namnge denna klient" -ForegroundColor Yellow
Write-Host ""

$hostname = $env:COMPUTERNAME
Write-Host "  Datornamn: $hostname"
Write-Host ""
Write-Host "  Varje klient i systemet behover ett unikt namn."
Write-Host "  Exempel: kontor, lager, butik-1"
Write-Host ""
$clientName = Read-Host "  Namn for denna klient [$hostname]"
if ([string]::IsNullOrWhiteSpace($clientName)) {
    $clientName = $hostname
}
Write-Host "  -> Denna klient heter: $clientName" -ForegroundColor Green

# -- Steg 2: Installera beroenden --
Write-Host ""
Write-Host "[2/5] Installerar Python-beroenden..." -ForegroundColor Yellow

& $pythonCmd -m pip install --upgrade pip 2>&1 | Out-Null
& $pythonCmd -m pip install websockets Pillow pyyaml requests numpy opencv-python-headless

Write-Host "  -> Beroenden installerade" -ForegroundColor Green

# -- Steg 3: Kamerakonfiguration --
Write-Host ""
Write-Host "[3/5] Kamerakonfiguration" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Vilka kameror ska denna klient hantera?"
Write-Host "    1) Axis natverkskameror (VAPIX/RTSP)"
Write-Host "    2) Andra RTSP-kameror (Hikvision, Dahua, etc.)"
Write-Host "    3) USB-kameror"
Write-Host "    4) Testlage (inga kameror)"
Write-Host ""
Write-Host "  OBS: GPIO ar inte tillgangligt pa Windows." -ForegroundColor DarkGray
Write-Host "       Pi Camera Module kravs Raspberry Pi." -ForegroundColor DarkGray
Write-Host ""
$cameraChoice = Read-Host "  Valj [1-4]"

# -- Steg 4: Serveranslutning --
Write-Host ""
Write-Host "[4/5] Serveranslutning" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Du behover:"
Write-Host "    1. Server-adressen (t.ex. wss://cam.dindomän.se:2053)"
Write-Host "    2. Kamerahemligheten (visas i viewer-appen -> sidopanel)"
Write-Host ""

$serverUrl = Read-Host "  Server-URL (t.ex. wss://cam.example.com:2053)"

# Validera URL
if (-not ($serverUrl -match "^wss?://")) {
    $useSsl = Read-Host "  Anvand SSL? [J/n]"
    if ($useSsl -eq "n" -or $useSsl -eq "N") {
        $serverUrl = "ws://$serverUrl"
    } else {
        $serverUrl = "wss://$serverUrl"
    }
    Write-Host "  -> URL: $serverUrl"
}

$secretKey = Read-Host "  Kamerahemlighet (secret)"

# -- Skapa cameras.yaml --
$installDir = Get-Location
$configFile = Join-Path $installDir "cameras.yaml"
$date = Get-Date -Format "yyyy-MM-dd"

$yamlContent = @"
# Camera Client - Konfiguration (Windows)
# Genererad av install_windows.ps1 $date
# Klient-namn: $clientName

# -- Serveranslutning --
server:
  url: "$serverUrl"
  secret: "$secretKey"
  reconnect_delay: 1
  max_reconnect_delay: 60

# -- Klient-identitet --
client_name: "$clientName"

# -- Kameror --
cameras:
"@

# Lagg till kameror interaktivt
Write-Host ""
Write-Host "  Nu lagger vi till kameror. Skriv 'klar' nar du ar fardig."
Write-Host ""

$camCount = 0
while ($true) {
    $camCount++
    Write-Host "  -- Kamera $camCount --"

    $camTypes = @("axis", "rtsp", "usb", "test")
    if ($cameraChoice -ge 1 -and $cameraChoice -le 4) {
        $defaultType = $camTypes[$cameraChoice - 1]
    } else {
        $defaultType = "test"
    }

    $camType = Read-Host "    Typ (axis/rtsp/usb/test eller 'klar') [$defaultType]"
    if ([string]::IsNullOrWhiteSpace($camType)) { $camType = $defaultType }
    if ($camType -eq "klar") { break }

    $defaultId = "$clientName-cam$camCount"
    $camId = Read-Host "    Kamera-ID [$defaultId]"
    if ([string]::IsNullOrWhiteSpace($camId)) { $camId = $defaultId }

    $defaultName = "$clientName Kamera $camCount"
    $camName = Read-Host "    Visningsnamn [$defaultName]"
    if ([string]::IsNullOrWhiteSpace($camName)) { $camName = $defaultName }

    switch ($camType) {
        "axis" {
            $camHost = Read-Host "    IP-adress"
            $camUser = Read-Host "    Anvandarnamn [root]"
            if ([string]::IsNullOrWhiteSpace($camUser)) { $camUser = "root" }
            $camPass = Read-Host "    Losenord"
            $camW = Read-Host "    Upplosning bredd [1280]"
            if ([string]::IsNullOrWhiteSpace($camW)) { $camW = "1280" }
            $camH = Read-Host "    Upplosning hojd [720]"
            if ([string]::IsNullOrWhiteSpace($camH)) { $camH = "720" }
            $camFps = Read-Host "    FPS [10]"
            if ([string]::IsNullOrWhiteSpace($camFps)) { $camFps = "10" }

            $yamlContent += @"

  - type: axis
    camera_id: "$camId"
    name: "$camName"
    host: "$camHost"
    username: "$camUser"
    password: "$camPass"
    port: 80
    mode: "snapshot"
    width: $camW
    height: $camH
    fps: $camFps
    quality: 70
"@
        }
        "rtsp" {
            $camRtsp = Read-Host "    RTSP URL"
            $camFps = Read-Host "    FPS [10]"
            if ([string]::IsNullOrWhiteSpace($camFps)) { $camFps = "10" }

            $yamlContent += @"

  - type: rtsp
    camera_id: "$camId"
    name: "$camName"
    rtsp_url: "$camRtsp"
    width: 1280
    height: 720
    fps: $camFps
    quality: 70
"@
        }
        "usb" {
            $camDev = Read-Host "    Enhet (0 = forsta kameran) [0]"
            if ([string]::IsNullOrWhiteSpace($camDev)) { $camDev = "0" }

            $yamlContent += @"

  - type: usb
    camera_id: "$camId"
    name: "$camName"
    device: $camDev
    width: 640
    height: 480
    fps: 15
    quality: 70
"@
        }
        "test" {
            $yamlContent += @"

  - type: test
    camera_id: "$camId"
    name: "$camName"
    width: 640
    height: 480
    fps: 5
    quality: 70
"@
        }
        default {
            Write-Host "    Okand typ, hoppar over." -ForegroundColor Red
            $camCount--
            continue
        }
    }

    Write-Host "    -> $camName tillagd!" -ForegroundColor Green
    Write-Host ""
}

# Lagg till motion och notis-config
$yamlContent += @"

# -- Rorelsedetektering --
motion:
  enabled: false
  sensitivity: 50
  min_area_percent: 1.0
  min_frames: 2
  cooldown: 5

# AI Objektdetektering
object_detection:
  enabled: false
  model: "mobilenet"
  confidence: 0.5
  detect_classes:
    - person
    - car

# GPIO: Inte tillgangligt pa Windows
# gpio:
#   inputs: []
#   outputs: []
#   scenes: []

# Push-notiser
notifications:
  enabled: false
"@

# Skriv config-fil
$yamlContent | Out-File -FilePath $configFile -Encoding UTF8
Write-Host ""
Write-Host "  -> Konfiguration sparad i $configFile" -ForegroundColor Green

# -- Steg 5: Skapa startskript --
Write-Host ""
Write-Host "[5/5] Skapar startskript..." -ForegroundColor Yellow

# Skapa bat-fil i installationsmappen
$startScript = @"
@echo off
title JPsecurity - $clientName
echo.
echo  JPsecurity Camera Client ($clientName)
echo  ========================================
echo  Stanger du detta fonster stoppas kameran.
echo.
cd /d "$installDir"
$pythonCmd "$installDir\camera_client.py" --config "$configFile"
echo.
echo  Kameran har stoppats. Tryck valfri tangent for att stanga.
pause >nul
"@

$startFile = Join-Path $installDir "start_camera.bat"
$startScript | Out-File -FilePath $startFile -Encoding ASCII
Write-Host "  -> start_camera.bat skapad i $installDir" -ForegroundColor Green

# Kopiera bat-filen till skrivbordet
$desktopPath = [Environment]::GetFolderPath('Desktop')
if ($desktopPath -and (Test-Path $desktopPath)) {
    $desktopBat = Join-Path $desktopPath "JPsecurity - $clientName.bat"
    Copy-Item $startFile $desktopBat -Force
    Write-Host "  -> Genvag skapad pa skrivbordet: $desktopBat" -ForegroundColor Green
} else {
    Write-Host "  -> Kunde inte hitta skrivbordet. Kopiera start_camera.bat manuellt." -ForegroundColor Yellow
}

# -- Fraga om autostart --
Write-Host ""
Write-Host "  Vill du att kameran startar automatiskt nar datorn startar?" -ForegroundColor Yellow
Write-Host "  (Anvander Windows Aktivitetsschemalagare - inga extra verktyg behovs)" -ForegroundColor DarkGray
Write-Host ""
$installAutostart = Read-Host "  Starta automatiskt vid inloggning? [J/n]"

if ($installAutostart -ne "n" -and $installAutostart -ne "N") {
    $taskName = "JPsecurity-$clientName"

    # Skapa ett VBS-skript som startar Python utan synligt fonster
    $vbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$installDir"
WshShell.Run "$pythonCmd ""$installDir\camera_client.py"" --config ""$configFile""", 0, False
"@
    $vbsFile = Join-Path $installDir "start_hidden.vbs"
    $vbsContent | Out-File -FilePath $vbsFile -Encoding ASCII

    # Ta bort eventuell gammal uppgift
    schtasks /Delete /TN $taskName /F 2>$null | Out-Null

    # Skapa schemalagd uppgift som kors vid inloggning
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    schtasks /Create /TN $taskName /TR "wscript.exe `"$vbsFile`"" /SC ONLOGON /RL HIGHEST /F /RU $currentUser

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  -> Autostart installerad!" -ForegroundColor Green
        Write-Host "  -> Kameran startar automatiskt nar du loggar in." -ForegroundColor Green
        Write-Host "" 
        Write-Host "  Hantera autostart:" -ForegroundColor DarkGray
        Write-Host "    Stoppa:        schtasks /End /TN $taskName" -ForegroundColor DarkGray
        Write-Host "    Starta:        schtasks /Run /TN $taskName" -ForegroundColor DarkGray
        Write-Host "    Avinstallera:  schtasks /Delete /TN $taskName /F" -ForegroundColor DarkGray

        # Starta direkt nu ocksa
        Write-Host ""
        $startNow = Read-Host "  Starta kameran nu? [J/n]"
        if ($startNow -ne "n" -and $startNow -ne "N") {
            schtasks /Run /TN $taskName
            Write-Host "  -> Kameran startar i bakgrunden!" -ForegroundColor Green
        }
    } else {
        Write-Host "  -> Kunde inte skapa schemalagd uppgift." -ForegroundColor Red
        Write-Host "  -> Prova att kora PowerShell som administrator och kor skriptet igen." -ForegroundColor Yellow
        Write-Host "  -> Du kan alltid starta manuellt via bat-filen pa skrivbordet." -ForegroundColor Yellow
    }
} else {
    Write-Host "  -> Hoppar over autostart." -ForegroundColor DarkGray
    Write-Host "  -> Dubbelklicka pa 'JPsecurity - $clientName.bat' pa skrivbordet for att starta manuellt." -ForegroundColor White
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Installation klar!" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Klient-namn:  $clientName" -ForegroundColor White
Write-Host "  Server:       $serverUrl" -ForegroundColor White
Write-Host "  Konfig:       $configFile" -ForegroundColor White
Write-Host "  Autostart:    $(if ($installAutostart -ne 'n' -and $installAutostart -ne 'N') { 'Ja (vid inloggning)' } else { 'Nej' })" -ForegroundColor White
Write-Host "  GPIO:         Ej tillgangligt (Windows)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Kommandon:" -ForegroundColor Yellow
Write-Host "  Starta manuellt:  Dubbelklicka 'JPsecurity - $clientName.bat' pa skrivbordet"
Write-Host "  Redigera config:  notepad cameras.yaml"
Write-Host "  Visa logg:        $pythonCmd camera_client.py --config cameras.yaml"
Write-Host ""
Write-Host "Tips:" -ForegroundColor Yellow
Write-Host "  * Natverkskameror (Axis/RTSP) fungerar identiskt som pa Pi"
Write-Host "  * USB-kameror fungerar via OpenCV"
Write-Host "  * GPIO ar inte tillgangligt pa Windows"
Write-Host ""
