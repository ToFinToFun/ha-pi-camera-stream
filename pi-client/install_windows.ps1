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
# Vid uppdatering (git pull):
#   Skriptet hittar befintlig cameras.yaml och visar nuvarande varden.
#   Tryck Enter for att behalla, eller skriv nytt varde for att andra.
#
# Krav: Python 3.9+ maste vara installerat
# ============================================================================

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Camera Client - Windows Installation" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- Kontrollera PowerShell-version --
$psVer = $PSVersionTable.PSVersion
Write-Host "  PowerShell-version: $($psVer.Major).$($psVer.Minor).$($psVer.Build)" -ForegroundColor Gray

if ($psVer.Major -lt 7) {
    Write-Host ""
    Write-Host "  OBS: Du kor PowerShell $($psVer.Major) (aldre version)." -ForegroundColor Yellow
    Write-Host "  PowerShell 7+ rekommenderas for basta kompatibilitet." -ForegroundColor Yellow
    Write-Host "  Installationen fungerar anda, men uppgradering rekommenderas." -ForegroundColor Yellow
    Write-Host ""
    $upgrade = Read-Host "  Vill du uppgradera PowerShell nu? [j/N]"
    if ($upgrade -eq 'j' -or $upgrade -eq 'J' -or $upgrade -eq 'y' -or $upgrade -eq 'Y') {
        Write-Host ""
        Write-Host "  Laddar ner PowerShell 7..." -ForegroundColor Cyan
        try {
            $latestRelease = Invoke-RestMethod -Uri "https://api.github.com/repos/PowerShell/PowerShell/releases/latest"
            $msiAsset = $latestRelease.assets | Where-Object { $_.name -match "PowerShell-.*-win-x64\.msi$" } | Select-Object -First 1

            if ($msiAsset) {
                $downloadPath = Join-Path $env:TEMP $msiAsset.name
                Write-Host "  Laddar ner: $($msiAsset.name)" -ForegroundColor Gray
                Invoke-WebRequest -Uri $msiAsset.browser_download_url -OutFile $downloadPath -UseBasicParsing

                Write-Host "  Startar installationen..." -ForegroundColor Cyan
                Write-Host "  Folj installationsguiden som oppnas." -ForegroundColor Yellow
                Start-Process msiexec.exe -ArgumentList "/i `"$downloadPath`" /passive ADD_EXPLORER_CONTEXT_MENU_OPENPOWERSHELL=1 ADD_FILE_CONTEXT_MENU_RUNPOWERSHELL=1 ENABLE_PSREMOTING=0 REGISTER_MANIFEST=1" -Wait

                Write-Host ""
                Write-Host "  PowerShell 7 installerad!" -ForegroundColor Green
                Write-Host "  Starta om detta skript i 'PowerShell 7' (sok i startmenyn)." -ForegroundColor Yellow
                Write-Host ""
                Read-Host "  Tryck Enter for att avsluta"
                exit 0
            } else {
                Write-Host "  Kunde inte hitta ratt installationsfil." -ForegroundColor Red
                Write-Host "  Ladda ner manuellt fran: https://github.com/PowerShell/PowerShell/releases/latest" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  Nedladdning misslyckades: $_" -ForegroundColor Red
            Write-Host "  Ladda ner manuellt fran: https://github.com/PowerShell/PowerShell/releases/latest" -ForegroundColor Yellow
        }
        Write-Host ""
        $cont = Read-Host "  Vill du fortsatta installationen med PowerShell $($psVer.Major) anda? [J/n]"
        if ($cont -eq 'n' -or $cont -eq 'N') {
            exit 0
        }
    }
    Write-Host ""
}

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

# ============================================================================
# DETEKTERA BEFINTLIG KONFIGURATION
# ============================================================================
$installDir = Get-Location
$configFile = Join-Path $installDir "cameras.yaml"
$existingConfig = $null
$isUpdate = $false
$updateChoice = "3"

if (Test-Path $configFile) {
    Write-Host ""
    Write-Host "  ================================================" -ForegroundColor Green
    Write-Host "  Befintlig konfiguration hittad!" -ForegroundColor Green
    Write-Host "  ================================================" -ForegroundColor Green
    Write-Host ""

    # Lasa in befintlig YAML med Python (sakrare an PowerShell-parsning)
    $parseScript = @"
import yaml, json, sys
try:
    raw = open(r'$configFile', 'rb').read()
    if raw[:3] == b'\xef\xbb\xbf':
        raw = raw[3:]
    data = yaml.safe_load(raw.decode('utf-8'))
    print(json.dumps(data))
except Exception as e:
    print(json.dumps({"error": str(e)}), file=sys.stderr)
    sys.exit(1)
"@
    $parseFile = Join-Path $env:TEMP "parse_yaml.py"
    [System.IO.File]::WriteAllText($parseFile, $parseScript, (New-Object System.Text.UTF8Encoding $false))

    try {
        $jsonOutput = & $pythonCmd $parseFile 2>&1
        $existingConfig = $jsonOutput | ConvertFrom-Json
        if ($existingConfig -and -not $existingConfig.error) {
            $isUpdate = $true

            # Hamta befintliga varden
            $exClientName = $existingConfig.client_name
            $exServerUrl = if ($existingConfig.server) { $existingConfig.server.url } else { "" }
            $exSecret = if ($existingConfig.server) { $existingConfig.server.secret } else { "" }
            $exCameras = if ($existingConfig.cameras) { $existingConfig.cameras } else { @() }

            Write-Host "  Nuvarande konfiguration:" -ForegroundColor Cyan
            Write-Host "    Klient-namn:  $exClientName" -ForegroundColor White
            Write-Host "    Server:       $exServerUrl" -ForegroundColor White
            if ($exSecret.Length -gt 8) {
                Write-Host "    Hemlighet:    $($exSecret.Substring(0, 8))..." -ForegroundColor White
            } else {
                Write-Host "    Hemlighet:    $exSecret" -ForegroundColor White
            }
            Write-Host "    Kameror:      $($exCameras.Count) st" -ForegroundColor White

            foreach ($cam in $exCameras) {
                $camInfo = "      - $($cam.name) ($($cam.type))"
                if ($cam.host) { $camInfo += " @ $($cam.host)" }
                if ($cam.camera_id) { $camInfo += " [ID: $($cam.camera_id)]" }
                Write-Host $camInfo -ForegroundColor DarkGray
            }

            Write-Host ""
            Write-Host "  Vad vill du gora?" -ForegroundColor Yellow
            Write-Host "    1) Snabbuppdatering - behall config, uppdatera beroenden och skript" -ForegroundColor White
            Write-Host "    2) Andra konfigurationen - ga igenom stegen (Enter = behall varde)" -ForegroundColor White
            Write-Host "    3) Ny installation - borja om fran borjan" -ForegroundColor White
            Write-Host ""
            $updateChoice = Read-Host "  Valj [1/2/3] (standard: 1)"
            if ([string]::IsNullOrWhiteSpace($updateChoice)) { $updateChoice = "1" }
        } else {
            Write-Host "  Varning: Kunde inte lasa cameras.yaml korrekt." -ForegroundColor Yellow
            Write-Host "  Fortsatter med ny installation." -ForegroundColor Yellow
            $isUpdate = $false
            $updateChoice = "3"
        }
    } catch {
        Write-Host "  Varning: Kunde inte tolka cameras.yaml: $_" -ForegroundColor Yellow
        Write-Host "  Fortsatter med ny installation." -ForegroundColor Yellow
        $isUpdate = $false
        $updateChoice = "3"
    }
}

# ============================================================================
# ALTERNATIV 1: SNABBUPPDATERING (behall allt, bara uppdatera beroenden)
# ============================================================================
if ($updateChoice -eq "1" -and $isUpdate) {
    Write-Host ""
    Write-Host "[1/3] Uppdaterar Python-beroenden..." -ForegroundColor Yellow

    & $pythonCmd -m pip install --upgrade pip 2>&1 | Out-Null
    & $pythonCmd -m pip install --upgrade websockets Pillow pyyaml requests numpy opencv-python-headless

    Write-Host "  -> Beroenden uppdaterade" -ForegroundColor Green

    $clientName = $exClientName
    $serverUrl = $exServerUrl

    # Uppdatera startskript (kan ha andrats i ny version)
    Write-Host ""
    Write-Host "[2/3] Uppdaterar startskript..." -ForegroundColor Yellow

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
    Write-Host "  -> start_camera.bat uppdaterad" -ForegroundColor Green

    # Uppdatera skrivbordsgenväg
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    if ($desktopPath -and (Test-Path $desktopPath)) {
        $desktopBat = Join-Path $desktopPath "JPsecurity - $clientName.bat"
        Copy-Item $startFile $desktopBat -Force
        Write-Host "  -> Skrivbordsgenväg uppdaterad" -ForegroundColor Green
    }

    # Uppdatera autostart VBS
    $vbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$installDir"
WshShell.Run "$pythonCmd ""$installDir\camera_client.py"" --config ""$configFile""", 0, False
"@
    $vbsFile = Join-Path $installDir "start_hidden.vbs"
    $vbsContent | Out-File -FilePath $vbsFile -Encoding ASCII
    Write-Host "  -> Autostart-skript uppdaterat" -ForegroundColor Green

    # Migrera fran schtasks till Startup-mappen om det behovs
    $startupFolder = [Environment]::GetFolderPath('Startup')
    if ($startupFolder -and (Test-Path $startupFolder)) {
        $shortcutPath = Join-Path $startupFolder "JPsecurity - $clientName.lnk"
        if (-not (Test-Path $shortcutPath)) {
            # Skapa genvag i Startup-mappen
            $WScriptShell = New-Object -ComObject WScript.Shell
            $shortcut = $WScriptShell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = "wscript.exe"
            $shortcut.Arguments = "`"$vbsFile`""
            $shortcut.WorkingDirectory = "$installDir"
            $shortcut.Description = "JPsecurity Camera Client - $clientName"
            $shortcut.Save()
            Write-Host "  -> Autostart-genvag skapad i Startup-mappen" -ForegroundColor Green
        }
        # Ta bort gammal schtasks om den finns
        $taskName = "JPsecurity-$clientName"
        schtasks /Delete /TN $taskName /F 2>$null | Out-Null
    }

    # Anslutningstest
    Write-Host ""
    Write-Host "[3/3] Anslutningstest..." -ForegroundColor Yellow
    Write-Host ""
    $runTest = Read-Host "  Kora anslutningstest nu? [J/n]"
    if ($runTest -ne "n" -and $runTest -ne "N") {
        & $pythonCmd (Join-Path $installDir "connection_test.py") --config $configFile
    }

    Write-Host ""
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  Uppdatering klar!" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Klient-namn:  $clientName" -ForegroundColor White
    Write-Host "  Server:       $serverUrl" -ForegroundColor White
    Write-Host "  Konfig:       $configFile (oforandrad)" -ForegroundColor White
    Write-Host ""
    Write-Host "  Starta om kamera-klienten for att anvanda uppdaterad kod:" -ForegroundColor Yellow
    Write-Host "    Dubbelklicka 'JPsecurity - $clientName.bat' pa skrivbordet" -ForegroundColor White
    Write-Host ""

    exit 0
}

# ============================================================================
# ALTERNATIV 3: Ny installation (rensa befintlig config)
# ============================================================================
if ($updateChoice -eq "3") {
    $isUpdate = $false
    $existingConfig = $null
}

# ============================================================================
# ALTERNATIV 2 eller 3: Full konfiguration (med eller utan defaults)
# ============================================================================

# -- Steg 1: Namnge denna klient --
Write-Host ""
Write-Host "[1/5] Namnge denna klient" -ForegroundColor Yellow
Write-Host ""

$hostname = $env:COMPUTERNAME

if ($isUpdate -and $exClientName) {
    $defaultClientName = $exClientName
    Write-Host "  Nuvarande namn: $exClientName" -ForegroundColor DarkGray
} else {
    $defaultClientName = $hostname
    Write-Host "  Datornamn: $hostname"
}

Write-Host ""
Write-Host "  Varje klient i systemet behover ett unikt namn."
Write-Host "  Exempel: kontor, lager, butik-1"
Write-Host ""
$clientName = Read-Host "  Namn for denna klient [$defaultClientName]"
if ([string]::IsNullOrWhiteSpace($clientName)) {
    $clientName = $defaultClientName
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

$camAction = "3"

if ($isUpdate -and $exCameras.Count -gt 0) {
    Write-Host "  Befintliga kameror:" -ForegroundColor Cyan
    $camIdx = 1
    foreach ($cam in $exCameras) {
        $camInfo = "    $camIdx) $($cam.name) ($($cam.type))"
        if ($cam.host) { $camInfo += " @ $($cam.host)" }
        Write-Host $camInfo -ForegroundColor White
        $camIdx++
    }
    Write-Host ""
    Write-Host "  Vill du behalla dessa kameror eller konfigurera om?" -ForegroundColor Yellow
    Write-Host "    1) Behall befintliga kameror (Enter)" -ForegroundColor White
    Write-Host "    2) Redigera befintliga kameror (andra varden)" -ForegroundColor White
    Write-Host "    3) Borja om med nya kameror" -ForegroundColor White
    Write-Host "    4) Behall befintliga + lagg till fler kameror" -ForegroundColor White
    Write-Host ""
    $camAction = Read-Host "  Valj [1/2/3/4] (standard: 1)"
    if ([string]::IsNullOrWhiteSpace($camAction)) { $camAction = "1" }
} else {
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
}

# -- Steg 4: Serveranslutning --
Write-Host ""
Write-Host "[4/5] Serveranslutning" -ForegroundColor Yellow
Write-Host ""

if ($isUpdate -and $exServerUrl) {
    Write-Host "  Nuvarande server: $exServerUrl" -ForegroundColor DarkGray
    Write-Host "  Tryck Enter for att behalla, eller skriv ny adress." -ForegroundColor DarkGray
    Write-Host ""
    $serverUrl = Read-Host "  Server-URL [$exServerUrl]"
    if ([string]::IsNullOrWhiteSpace($serverUrl)) {
        $serverUrl = $exServerUrl
    }
} else {
    Write-Host "  Du behover:"
    Write-Host "    1. Server-adressen (t.ex. wss://cam.dindomän.se:2053)"
    Write-Host "    2. Kamerahemligheten (visas i viewer-appen -> sidopanel)"
    Write-Host ""
    $serverUrl = Read-Host "  Server-URL (t.ex. wss://cam.example.com:2053)"
}

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

if ($isUpdate -and $exSecret) {
    if ($exSecret.Length -gt 8) {
        $maskedSecret = $exSecret.Substring(0, 8) + "..."
    } else {
        $maskedSecret = $exSecret
    }
    Write-Host ""
    Write-Host "  Nuvarande hemlighet: $maskedSecret" -ForegroundColor DarkGray
    Write-Host "  Tryck Enter for att behalla, eller skriv ny." -ForegroundColor DarkGray
    $secretKey = Read-Host "  Kamerahemlighet [$maskedSecret]"
    if ([string]::IsNullOrWhiteSpace($secretKey)) {
        $secretKey = $exSecret
    }
} else {
    $secretKey = Read-Host "  Kamerahemlighet (secret)"
}

# -- Skapa cameras.yaml --
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

# ============================================================================
# KAMERA-HANTERING
# ============================================================================

$addNewCameras = $false

if ($camAction -eq "1" -and $isUpdate -and $exCameras.Count -gt 0) {
    # Behall befintliga kameror exakt som de ar
    foreach ($cam in $exCameras) {
        $yamlContent += "`n  - type: `"$($cam.type)`""
        $yamlContent += "`n    camera_id: `"$($cam.camera_id)`""
        $yamlContent += "`n    name: `"$($cam.name)`""

        if ($cam.host) { $yamlContent += "`n    host: `"$($cam.host)`"" }
        if ($cam.username) { $yamlContent += "`n    username: `"$($cam.username)`"" }
        if ($cam.password) { $yamlContent += "`n    password: `"$($cam.password)`"" }
        if ($cam.port) { $yamlContent += "`n    port: $($cam.port)" }
        if ($cam.mode) { $yamlContent += "`n    mode: `"$($cam.mode)`"" }
        if ($cam.rtsp_url) { $yamlContent += "`n    rtsp_url: `"$($cam.rtsp_url)`"" }
        if ($cam.device -ne $null) { $yamlContent += "`n    device: $($cam.device)" }
        if ($cam.width) { $yamlContent += "`n    width: $($cam.width)" }
        if ($cam.height) { $yamlContent += "`n    height: $($cam.height)" }
        if ($cam.fps) { $yamlContent += "`n    fps: $($cam.fps)" }
        if ($cam.quality) { $yamlContent += "`n    quality: $($cam.quality)" }
    }
    Write-Host ""
    Write-Host "  -> $($exCameras.Count) befintliga kameror behallna" -ForegroundColor Green

} elseif ($camAction -eq "4" -and $isUpdate -and $exCameras.Count -gt 0) {
    # Behall befintliga kameror OCH lagg till nya
    foreach ($cam in $exCameras) {
        $yamlContent += "`n  - type: `"$($cam.type)`""
        $yamlContent += "`n    camera_id: `"$($cam.camera_id)`""
        $yamlContent += "`n    name: `"$($cam.name)`""

        if ($cam.host) { $yamlContent += "`n    host: `"$($cam.host)`"" }
        if ($cam.username) { $yamlContent += "`n    username: `"$($cam.username)`"" }
        if ($cam.password) { $yamlContent += "`n    password: `"$($cam.password)`"" }
        if ($cam.port) { $yamlContent += "`n    port: $($cam.port)" }
        if ($cam.mode) { $yamlContent += "`n    mode: `"$($cam.mode)`"" }
        if ($cam.rtsp_url) { $yamlContent += "`n    rtsp_url: `"$($cam.rtsp_url)`"" }
        if ($cam.device -ne $null) { $yamlContent += "`n    device: $($cam.device)" }
        if ($cam.width) { $yamlContent += "`n    width: $($cam.width)" }
        if ($cam.height) { $yamlContent += "`n    height: $($cam.height)" }
        if ($cam.fps) { $yamlContent += "`n    fps: $($cam.fps)" }
        if ($cam.quality) { $yamlContent += "`n    quality: $($cam.quality)" }
    }
    Write-Host ""
    Write-Host "  -> $($exCameras.Count) befintliga kameror behallna" -ForegroundColor Green
    Write-Host "  Nu lagger vi till fler kameror..." -ForegroundColor Yellow
    $addNewCameras = $true

} elseif ($camAction -eq "2" -and $isUpdate -and $exCameras.Count -gt 0) {
    # Redigera befintliga kameror - visa nuvarande varden som defaults
    Write-Host ""
    Write-Host "  Redigera kameror (tryck Enter for att behalla nuvarande varde):" -ForegroundColor Yellow
    Write-Host ""

    $localNet = ""
    try {
        $adapter = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1
        if ($adapter) { $localNet = "(denna dator: $($adapter.IPAddress))" }
    } catch {}

    foreach ($cam in $exCameras) {
        Write-Host "  -- $($cam.name) --" -ForegroundColor Cyan

        $newId = Read-Host "    Kamera-ID [$($cam.camera_id)]"
        if ([string]::IsNullOrWhiteSpace($newId)) { $newId = $cam.camera_id }

        $newName = Read-Host "    Visningsnamn [$($cam.name)]"
        if ([string]::IsNullOrWhiteSpace($newName)) { $newName = $cam.name }

        $newType = Read-Host "    Typ [$($cam.type)]"
        if ([string]::IsNullOrWhiteSpace($newType)) { $newType = $cam.type }

        switch ($newType) {
            "axis" {
                $defHost = if ($cam.host) { $cam.host } else { "" }
                $defUser = if ($cam.username) { $cam.username } else { "root" }
                $defPass = if ($cam.password) { $cam.password } else { "" }
                $defW = if ($cam.width) { $cam.width } else { "1280" }
                $defH = if ($cam.height) { $cam.height } else { "720" }
                $defFps = if ($cam.fps) { $cam.fps } else { "10" }

                $newHost = Read-Host "    IP-adress $localNet [$defHost]"
                if ([string]::IsNullOrWhiteSpace($newHost)) { $newHost = $defHost }
                $newUser = Read-Host "    Anvandarnamn [$defUser]"
                if ([string]::IsNullOrWhiteSpace($newUser)) { $newUser = $defUser }
                $newPass = Read-Host "    Losenord [$defPass]"
                if ([string]::IsNullOrWhiteSpace($newPass)) { $newPass = $defPass }
                $newW = Read-Host "    Bredd [$defW]"
                if ([string]::IsNullOrWhiteSpace($newW)) { $newW = $defW }
                $newH = Read-Host "    Hojd [$defH]"
                if ([string]::IsNullOrWhiteSpace($newH)) { $newH = $defH }
                $newFps = Read-Host "    FPS [$defFps]"
                if ([string]::IsNullOrWhiteSpace($newFps)) { $newFps = $defFps }

                $yamlContent += @"

  - type: axis
    camera_id: "$newId"
    name: "$newName"
    host: "$newHost"
    username: "$newUser"
    password: "$newPass"
    port: 80
    mode: "snapshot"
    width: $newW
    height: $newH
    fps: $newFps
    quality: 70
"@
            }
            "rtsp" {
                $defRtsp = if ($cam.rtsp_url) { $cam.rtsp_url } else { "" }
                $defFps = if ($cam.fps) { $cam.fps } else { "10" }

                $newRtsp = Read-Host "    RTSP-URL [$defRtsp]"
                if ([string]::IsNullOrWhiteSpace($newRtsp)) { $newRtsp = $defRtsp }
                $newFps = Read-Host "    FPS [$defFps]"
                if ([string]::IsNullOrWhiteSpace($newFps)) { $newFps = $defFps }

                $yamlContent += @"

  - type: rtsp
    camera_id: "$newId"
    name: "$newName"
    rtsp_url: "$newRtsp"
    width: 1280
    height: 720
    fps: $newFps
    quality: 70
"@
            }
            "usb" {
                $defDev = if ($cam.device -ne $null) { $cam.device } else { "0" }

                $newDev = Read-Host "    Enhet [$defDev]"
                if ([string]::IsNullOrWhiteSpace($newDev)) { $newDev = $defDev }

                $yamlContent += @"

  - type: usb
    camera_id: "$newId"
    name: "$newName"
    device: $newDev
    width: 640
    height: 480
    fps: 15
    quality: 70
"@
            }
            "test" {
                $yamlContent += @"

  - type: test
    camera_id: "$newId"
    name: "$newName"
    width: 640
    height: 480
    fps: 5
    quality: 70
"@
            }
        }
        Write-Host "    -> $newName uppdaterad!" -ForegroundColor Green
        Write-Host ""
    }

    # Fraga om fler kameror
    Write-Host "  Vill du lagga till fler kameror?" -ForegroundColor Yellow
    $addMore = Read-Host "  Lagg till fler? [j/N]"
    if ($addMore -eq "j" -or $addMore -eq "J") {
        $addNewCameras = $true
    }

} else {
    # Ny installation - lagg till kameror fran borjan
    $addNewCameras = $true
}

# -- Lagg till nya kameror interaktivt --
if ($addNewCameras) {
    # Detektera lokal IP och natmask
    $localNet = ""
    try {
        $adapter = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1
        if ($adapter) {
            $localIP = $adapter.IPAddress
            $prefix = $adapter.PrefixLength
            $maskBits = ([Math]::Pow(2, $prefix) - 1) * [Math]::Pow(2, 32 - $prefix)
            $maskBytes = [BitConverter]::GetBytes([UInt32]$maskBits)
            [Array]::Reverse($maskBytes)
            $subnetMask = ($maskBytes | ForEach-Object { $_.ToString() }) -join '.'
            $ipParts = $localIP.Split('.')
            $maskParts = $subnetMask.Split('.')
            $netParts = for ($i = 0; $i -lt 4; $i++) { [int]$ipParts[$i] -band [int]$maskParts[$i] }
            $networkAddr = $netParts -join '.'
            $localNet = "(narbar fran $localIP / $subnetMask)"
            Write-Host ""
            Write-Host "  Natverksinfo:" -ForegroundColor Cyan
            Write-Host "    Denna dator:  $localIP" -ForegroundColor White
            Write-Host "    Natmask:      $subnetMask" -ForegroundColor White
            Write-Host "    Natverk:      $networkAddr/$prefix" -ForegroundColor White
            Write-Host "    Kameror bor vara i samma natverk ($networkAddr.x)" -ForegroundColor DarkGray
        }
    } catch {}

    if (-not $cameraChoice) {
        Write-Host ""
        Write-Host "  Vilka kameror ska denna klient hantera?"
        Write-Host "    1) Axis natverkskameror (VAPIX/RTSP)"
        Write-Host "    2) Andra RTSP-kameror (Hikvision, Dahua, etc.)"
        Write-Host "    3) USB-kameror"
        Write-Host "    4) Testlage (inga kameror)"
        Write-Host ""
        $cameraChoice = Read-Host "  Valj [1-4]"
    }

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
            $defaultType = "axis"
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
                $camHost = Read-Host "    IP-adress $localNet"
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
                $camRtsp = Read-Host "    RTSP-URL (t.ex. rtsp://user:pass@192.168.1.x:554/stream)"
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

# Skriv config-fil utan BOM
try {
    [System.IO.File]::WriteAllText($configFile, $yamlContent, (New-Object System.Text.UTF8Encoding $false))
} catch {
    $yamlContent | Out-File -FilePath $configFile -Encoding ASCII
}
Write-Host ""
Write-Host "  -> Konfiguration sparad i $configFile" -ForegroundColor Green

# -- Steg 5: Skapa startskript --
Write-Host ""
Write-Host "[5/5] Skapar startskript..." -ForegroundColor Yellow

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
Write-Host "  (Lagger en genvag i Windows Startup-mappen - kraver INTE administrator)" -ForegroundColor DarkGray
Write-Host ""
$installAutostart = Read-Host "  Starta automatiskt vid inloggning? [J/n]"

if ($installAutostart -ne "n" -and $installAutostart -ne "N") {
    # Skapa VBS-skript som startar Python utan synligt fonster
    $vbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$installDir"
WshShell.Run "$pythonCmd ""$installDir\camera_client.py"" --config ""$configFile""", 0, False
"@
    $vbsFile = Join-Path $installDir "start_hidden.vbs"
    $vbsContent | Out-File -FilePath $vbsFile -Encoding ASCII

    # Lagg i Windows Startup-mappen (kraver INTE admin)
    $startupFolder = [Environment]::GetFolderPath('Startup')
    if ($startupFolder -and (Test-Path $startupFolder)) {
        # Ta bort eventuell gammal genvag
        $oldLinks = Get-ChildItem $startupFolder -Filter "JPsecurity*" -ErrorAction SilentlyContinue
        foreach ($old in $oldLinks) { Remove-Item $old.FullName -Force }

        # Skapa genvag (.lnk) i Startup-mappen
        $shortcutPath = Join-Path $startupFolder "JPsecurity - $clientName.lnk"
        $WScriptShell = New-Object -ComObject WScript.Shell
        $shortcut = $WScriptShell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = "wscript.exe"
        $shortcut.Arguments = "`"$vbsFile`""
        $shortcut.WorkingDirectory = "$installDir"
        $shortcut.Description = "JPsecurity Camera Client - $clientName"
        $shortcut.Save()

        Write-Host "  -> Autostart installerad!" -ForegroundColor Green
        Write-Host "  -> Kameran startar automatiskt nar du loggar in." -ForegroundColor Green
        Write-Host ""
        Write-Host "  Startup-mapp: $startupFolder" -ForegroundColor DarkGray
        Write-Host "  Ta bort genvagen dar for att avaktivera autostart." -ForegroundColor DarkGray

        # Ta bort eventuell gammal schtasks-uppgift (fran tidigare version)
        $taskName = "JPsecurity-$clientName"
        schtasks /Delete /TN $taskName /F 2>$null | Out-Null

        Write-Host ""
        $startNow = Read-Host "  Starta kamera-klienten nu? [J/n]"
        if ($startNow -ne "n" -and $startNow -ne "N") {
            Start-Process "wscript.exe" -ArgumentList "`"$vbsFile`"" -WorkingDirectory "$installDir"
            Write-Host "  -> Kamera-klienten startar i bakgrunden!" -ForegroundColor Green
        }
    } else {
        Write-Host "  -> Kunde inte hitta Startup-mappen." -ForegroundColor Red
        Write-Host "  -> Du kan kopiera start_hidden.vbs manuellt till:" -ForegroundColor Yellow
        Write-Host "     $env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" -ForegroundColor Yellow
    }
} else {
    Write-Host "  -> Hoppar over autostart." -ForegroundColor DarkGray
    Write-Host "  -> Dubbelklicka pa 'JPsecurity - $clientName.bat' pa skrivbordet for att starta manuellt." -ForegroundColor White
}

# -- Steg 6: Anslutningstest --
Write-Host ""
Write-Host "[6/6] Anslutningstest..." -ForegroundColor Yellow
Write-Host ""
$runTest = Read-Host "  Kora anslutningstest nu? [J/n]"
if ($runTest -ne "n" -and $runTest -ne "N") {
    & $pythonCmd (Join-Path $installDir "connection_test.py") --config $configFile
} else {
    Write-Host "  -> Hoppar over test. Du kan kora det senare med:" -ForegroundColor DarkGray
    Write-Host "     python connection_test.py" -ForegroundColor DarkGray
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
Write-Host "  * Vid uppdatering: git pull && .\install_windows.ps1 (valj 1 for snabbuppdatering)"
Write-Host ""
