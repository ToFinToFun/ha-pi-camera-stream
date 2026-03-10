#!/usr/bin/env python3
"""
JPsecurity - Anslutningstest
=============================
Testar alla steg i anslutningskedjan:
  1. Server nåbar (HTTP/HTTPS)
  2. Kamerahemlighet giltig (WebSocket-registrering)
  3. Kamerainloggning (per kamera)
  4. Bildström (per kamera)

Användning:
  python connection_test.py                     # Testar med cameras.yaml
  python connection_test.py --config min.yaml   # Testar med annan config
  python connection_test.py --server-only       # Testar bara serveranslutning
"""

import sys
import os
import time
import json
import ssl
import asyncio
import argparse

# Färger för terminal
class C:
    OK = '\033[92m'      # Grön
    FAIL = '\033[91m'    # Röd
    WARN = '\033[93m'    # Gul
    INFO = '\033[96m'    # Cyan
    BOLD = '\033[1m'
    DIM = '\033[90m'
    END = '\033[0m'

    @staticmethod
    def supported():
        """Check if terminal supports colors"""
        if sys.platform == 'win32':
            try:
                os.system('')  # Enable ANSI on Windows 10+
                return True
            except:
                return False
        return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

# Disable colors if not supported
if not C.supported():
    C.OK = C.FAIL = C.WARN = C.INFO = C.BOLD = C.DIM = C.END = ''


def print_header():
    print(f"\n{C.BOLD}{'='*60}")
    print(f"  JPsecurity - Anslutningstest")
    print(f"{'='*60}{C.END}\n")


def print_test(name, status, detail=""):
    if status == "ok":
        icon = f"{C.OK}[OK]{C.END}"
    elif status == "fail":
        icon = f"{C.FAIL}[FEL]{C.END}"
    elif status == "warn":
        icon = f"{C.WARN}[!]{C.END}"
    elif status == "skip":
        icon = f"{C.DIM}[--]{C.END}"
    elif status == "test":
        icon = f"{C.INFO}[..]{C.END}"
        print(f"  {icon} {name}", end="", flush=True)
        return
    else:
        icon = f"[{status}]"

    if detail:
        print(f"  {icon} {name} {C.DIM}({detail}){C.END}")
    else:
        print(f"  {icon} {name}")


def print_section(title):
    print(f"\n{C.BOLD}{C.INFO}── {title} ──{C.END}")


def load_config(config_path):
    """Load and validate cameras.yaml"""
    try:
        import yaml
    except ImportError:
        print_test("PyYAML installerat", "fail", "pip install pyyaml")
        return None

    if not os.path.exists(config_path):
        print_test(f"Konfigurationsfil finns", "fail", f"{config_path} hittades inte")
        return None

    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        print_test("Konfigurationsfil laddad", "ok", config_path)
        return config
    except Exception as e:
        print_test("Konfigurationsfil giltig", "fail", str(e))
        return None


def test_server_http(server_url):
    """Test 1: HTTP/HTTPS connectivity to server"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Convert ws/wss URL to http/https
    http_url = server_url.replace("wss://", "https://").replace("ws://", "http://")
    health_url = f"{http_url}/api/health"

    print_test(f"Server nåbar via HTTP", "test")
    try:
        r = requests.get(health_url, timeout=10, verify=False)
        try:
            data = r.json()
            version = data.get('version', '?')
            uptime = data.get('uptime', '?')
            if isinstance(uptime, (int, float)):
                uptime = f"{int(uptime)}s"
            print(f"\r  {C.OK}[OK]{C.END} Server nåbar via HTTP {C.DIM}(v{version}, uptime: {uptime}){C.END}")
            return True, data
        except ValueError:
            # Server svarar men inte med JSON (t.ex. Cloudflare WAF-sida)
            if 'cloudflare' in r.text.lower() or r.status_code in (403, 503):
                print(f"\r  {C.WARN}[!]{C.END} Server nåbar via HTTP {C.DIM}(Cloudflare blockerar HTTP, men WS kan fungera){C.END}")
                return 'cloudflare', None
            else:
                print(f"\r  {C.WARN}[!]{C.END} Server nåbar via HTTP {C.DIM}(svarar men inte JSON - HTTP {r.status_code}){C.END}")
                return 'partial', None
    except requests.exceptions.ConnectionError:
        print(f"\r  {C.FAIL}[FEL]{C.END} Server nåbar via HTTP {C.DIM}(Kan inte ansluta till {health_url}){C.END}")
        print(f"        {C.DIM}Kontrollera: URL korrekt? Port öppen i router? Server startad?{C.END}")
        return False, None
    except requests.exceptions.Timeout:
        print(f"\r  {C.FAIL}[FEL]{C.END} Server nåbar via HTTP {C.DIM}(Timeout efter 10s){C.END}")
        print(f"        {C.DIM}Kontrollera: Brandvägg? Cloudflare-inställningar?{C.END}")
        return False, None
    except Exception as e:
        print(f"\r  {C.FAIL}[FEL]{C.END} Server nåbar via HTTP {C.DIM}({e}){C.END}")
        return False, None


async def test_server_websocket(server_url, secret):
    """Test 2: WebSocket connection and authentication"""
    try:
        import websockets
    except ImportError:
        print_test("websockets installerat", "fail", "pip install websockets")
        return False

    print_test(f"WebSocket-anslutning", "test")

    ssl_context = None
    if server_url.startswith("wss://"):
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    try:
        async with websockets.connect(server_url, ssl=ssl_context, open_timeout=10) as ws:
            print(f"\r  {C.OK}[OK]{C.END} WebSocket-anslutning {C.DIM}(ansluten){C.END}")

            # Test authentication
            print_test(f"Kamerahemlighet giltig", "test")
            reg_msg = json.dumps({
                "type": "register_camera",
                "camera_id": "__connection_test__",
                "name": "Anslutningstest",
                "secret": secret,
                "capabilities": {"test": True}
            })
            await ws.send(reg_msg)

            try:
                response = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(response)

                if data.get("type") == "registered":
                    print(f"\r  {C.OK}[OK]{C.END} Kamerahemlighet giltig {C.DIM}(autentiserad){C.END}")

                    # Unregister cleanly
                    await ws.close()
                    return True
                elif data.get("type") == "error":
                    reason = data.get("message", "Okänt fel")
                    print(f"\r  {C.FAIL}[FEL]{C.END} Kamerahemlighet giltig {C.DIM}({reason}){C.END}")
                    print(f"        {C.DIM}Kontrollera att hemligheten i cameras.yaml matchar serverns.{C.END}")
                    return False
                else:
                    print(f"\r  {C.WARN}[!]{C.END} Kamerahemlighet giltig {C.DIM}(oväntat svar: {data.get('type', '?')}){C.END}")
                    return False

            except asyncio.TimeoutError:
                print(f"\r  {C.FAIL}[FEL]{C.END} Kamerahemlighet giltig {C.DIM}(inget svar från server inom 5s){C.END}")
                return False

    except Exception as e:
        error_msg = str(e)
        print(f"\r  {C.FAIL}[FEL]{C.END} WebSocket-anslutning {C.DIM}({error_msg}){C.END}")
        if "1006" in error_msg:
            print(f"        {C.DIM}WebSocket blockeras troligen av proxy. Prova direkt-URL (port 2053).{C.END}")
        elif "SSL" in error_msg or "certificate" in error_msg.lower():
            print(f"        {C.DIM}SSL-certifikatproblem. Kontrollera att servern har giltigt cert.{C.END}")
        return False


def test_camera_axis(cam):
    """Test Axis camera connectivity"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    host = cam.get('host', '')
    port = cam.get('port', 80)
    user = cam.get('username', 'root')
    password = cam.get('password', '')
    use_https = cam.get('use_https', False)
    protocol = "https" if use_https else "http"

    # Test 1: Can we reach the camera?
    print_test(f"Kamera nåbar ({host}:{port})", "test")
    try:
        r = requests.get(f"{protocol}://{host}:{port}/axis-cgi/param.cgi?action=list&group=Brand",
                        auth=(user, password), timeout=5, verify=False)
        if r.status_code == 200:
            # Extract model info
            brand_info = r.text.strip()
            model = ""
            for line in brand_info.split('\n'):
                if 'ProdShortName' in line:
                    model = line.split('=')[-1].strip()
            print(f"\r  {C.OK}[OK]{C.END} Kamera nåbar ({host}:{port}) {C.DIM}({model or 'Axis'}){C.END}")
        elif r.status_code == 401:
            print(f"\r  {C.FAIL}[FEL]{C.END} Kamera nåbar ({host}:{port}) {C.DIM}(Fel användarnamn/lösenord){C.END}")
            return False
        else:
            print(f"\r  {C.WARN}[!]{C.END} Kamera nåbar ({host}:{port}) {C.DIM}(HTTP {r.status_code}){C.END}")
    except requests.exceptions.ConnectionError:
        print(f"\r  {C.FAIL}[FEL]{C.END} Kamera nåbar ({host}:{port}) {C.DIM}(Kan inte ansluta - kontrollera IP och att kameran är på){C.END}")
        return False
    except requests.exceptions.Timeout:
        print(f"\r  {C.FAIL}[FEL]{C.END} Kamera nåbar ({host}:{port}) {C.DIM}(Timeout - kameran svarar inte){C.END}")
        return False
    except Exception as e:
        print(f"\r  {C.FAIL}[FEL]{C.END} Kamera nåbar ({host}:{port}) {C.DIM}({e}){C.END}")
        return False

    # Test 2: Can we get a snapshot?
    width = cam.get('width', 1280)
    height = cam.get('height', 720)
    print_test(f"Bildström ({width}x{height})", "test")
    try:
        snap_url = f"{protocol}://{host}:{port}/axis-cgi/jpg/image.cgi?resolution={width}x{height}&compression=30"
        r = requests.get(snap_url, auth=(user, password), timeout=10, verify=False)
        if r.status_code == 200 and len(r.content) > 1000:
            size_kb = len(r.content) / 1024
            print(f"\r  {C.OK}[OK]{C.END} Bildström ({width}x{height}) {C.DIM}(snapshot {size_kb:.0f} KB){C.END}")
            return True
        elif r.status_code == 401:
            print(f"\r  {C.FAIL}[FEL]{C.END} Bildström ({width}x{height}) {C.DIM}(Autentiseringsfel){C.END}")
            return False
        else:
            print(f"\r  {C.FAIL}[FEL]{C.END} Bildström ({width}x{height}) {C.DIM}(HTTP {r.status_code}, {len(r.content)} bytes){C.END}")
            return False
    except Exception as e:
        print(f"\r  {C.FAIL}[FEL]{C.END} Bildström ({width}x{height}) {C.DIM}({e}){C.END}")
        return False


def test_camera_rtsp(cam):
    """Test RTSP camera connectivity"""
    rtsp_url = cam.get('rtsp_url', '')

    print_test(f"RTSP-anslutning", "test")
    try:
        import cv2
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)

        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                print(f"\r  {C.OK}[OK]{C.END} RTSP-anslutning {C.DIM}(bild: {w}x{h}){C.END}")
                cap.release()
                return True
            else:
                print(f"\r  {C.FAIL}[FEL]{C.END} RTSP-anslutning {C.DIM}(ansluten men kan inte läsa bild){C.END}")
                cap.release()
                return False
        else:
            print(f"\r  {C.FAIL}[FEL]{C.END} RTSP-anslutning {C.DIM}(kan inte öppna ström){C.END}")
            # Sanitize URL for display (hide password)
            display_url = rtsp_url
            if '@' in display_url:
                display_url = 'rtsp://***@' + display_url.split('@', 1)[1]
            print(f"        {C.DIM}URL: {display_url}{C.END}")
            return False
    except ImportError:
        print(f"\r  {C.WARN}[!]{C.END} RTSP-anslutning {C.DIM}(opencv saknas - pip install opencv-python-headless){C.END}")
        return False
    except Exception as e:
        print(f"\r  {C.FAIL}[FEL]{C.END} RTSP-anslutning {C.DIM}({e}){C.END}")
        return False


def test_camera_usb(cam):
    """Test USB camera connectivity"""
    device = cam.get('device', 0)

    print_test(f"USB-kamera (enhet {device})", "test")
    try:
        import cv2
        cap = cv2.VideoCapture(device)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                print(f"\r  {C.OK}[OK]{C.END} USB-kamera (enhet {device}) {C.DIM}(bild: {w}x{h}){C.END}")
                cap.release()
                return True
            else:
                print(f"\r  {C.FAIL}[FEL]{C.END} USB-kamera (enhet {device}) {C.DIM}(öppen men kan inte läsa bild){C.END}")
                cap.release()
                return False
        else:
            print(f"\r  {C.FAIL}[FEL]{C.END} USB-kamera (enhet {device}) {C.DIM}(ingen kamera hittad på enhet {device}){C.END}")
            return False
    except ImportError:
        print(f"\r  {C.WARN}[!]{C.END} USB-kamera (enhet {device}) {C.DIM}(opencv saknas - pip install opencv-python-headless){C.END}")
        return False
    except Exception as e:
        print(f"\r  {C.FAIL}[FEL]{C.END} USB-kamera (enhet {device}) {C.DIM}({e}){C.END}")
        return False


def test_camera(cam):
    """Test a single camera based on its type"""
    cam_type = cam.get('type', 'unknown')
    cam_name = cam.get('name', cam.get('camera_id', '?'))

    print(f"\n  {C.BOLD}Kamera: {cam_name}{C.END} {C.DIM}(typ: {cam_type}){C.END}")

    if cam_type == 'axis':
        return test_camera_axis(cam)
    elif cam_type == 'rtsp':
        return test_camera_rtsp(cam)
    elif cam_type == 'usb':
        return test_camera_usb(cam)
    elif cam_type == 'test':
        print_test("Testkamera", "ok", "kräver ingen extern anslutning")
        return True
    elif cam_type == 'picamera':
        print_test("Pi Camera Module", "skip", "kan bara testas på Raspberry Pi")
        return True
    else:
        print_test(f"Okänd kameratyp: {cam_type}", "warn")
        return False


def test_gpio(config):
    """Test GPIO configuration"""
    gpio_config = config.get('gpio', {})
    if not gpio_config.get('enabled', False):
        print_test("GPIO", "skip", "inaktiverat i konfigurationen")
        return True

    inputs = gpio_config.get('inputs', [])
    outputs = gpio_config.get('outputs', [])
    scenes = gpio_config.get('scenes', [])

    print_test(f"GPIO konfigurerat", "ok",
               f"{len(inputs)} ingångar, {len(outputs)} utgångar, {len(scenes)} scener")

    # Check if we're on a Pi
    try:
        import RPi.GPIO
        print_test("RPi.GPIO tillgängligt", "ok", "hårdvaru-GPIO")
    except ImportError:
        try:
            import gpiozero
            print_test("gpiozero tillgängligt", "ok", "hårdvaru-GPIO")
        except ImportError:
            if sys.platform == 'win32':
                print_test("GPIO-hårdvara", "warn", "Windows - GPIO körs i simuleringsläge")
            else:
                print_test("GPIO-hårdvara", "warn", "Inget GPIO-bibliotek - körs i simuleringsläge")

    return True


async def run_tests(config_path, server_only=False):
    """Run all tests"""
    print_header()

    results = {'passed': 0, 'failed': 0, 'warnings': 0, 'skipped': 0}

    # Load config
    print_section("Konfiguration")
    config = load_config(config_path)
    if config is None:
        results['failed'] += 1
        print_summary(results)
        return False

    server_url = config.get('server', {}).get('url', '')
    secret = config.get('server', {}).get('secret', '')
    client_name = config.get('client_name', 'okänd')

    print_test(f"Klientnamn", "ok", client_name)

    if not server_url:
        print_test("Server-URL konfigurerad", "fail", "saknas i cameras.yaml")
        results['failed'] += 1
        print_summary(results)
        return False
    print_test("Server-URL", "ok", server_url)

    if not secret:
        print_test("Kamerahemlighet konfigurerad", "fail", "saknas i cameras.yaml")
        results['failed'] += 1
        print_summary(results)
        return False
    print_test("Kamerahemlighet", "ok", f"{secret[:4]}...{secret[-4:]}")

    # Test 1: Server HTTP
    print_section("Steg 1: Serveranslutning")
    http_ok, health_data = test_server_http(server_url)
    if http_ok is True:
        results['passed'] += 1
    elif http_ok in ('cloudflare', 'partial'):
        results['warnings'] += 1
    else:
        results['failed'] += 1

    # Test 2: WebSocket + Auth
    print_section("Steg 2: WebSocket & Autentisering")
    if http_ok is not False:  # True, 'cloudflare', or 'partial' - try WS anyway
        ws_ok = await test_server_websocket(server_url, secret)
        if ws_ok:
            results['passed'] += 2  # WS + Auth
        else:
            results['failed'] += 1
    else:
        print_test("WebSocket-anslutning", "skip", "server ej nåbar")
        print_test("Kamerahemlighet", "skip", "server ej nåbar")
        results['skipped'] += 2

    if server_only:
        print_summary(results)
        return results['failed'] == 0

    # Test 3: Cameras
    cameras = config.get('cameras', [])
    if cameras:
        print_section(f"Steg 3: Kameror ({len(cameras)} st)")
        for cam in cameras:
            if test_camera(cam):
                results['passed'] += 1
            else:
                results['failed'] += 1
    else:
        print_section("Steg 3: Kameror")
        print_test("Kameror konfigurerade", "skip", "inga kameror i cameras.yaml")
        results['skipped'] += 1

    # Test 4: GPIO
    print_section("Steg 4: GPIO")
    if test_gpio(config):
        results['passed'] += 1
    else:
        results['failed'] += 1

    print_summary(results)
    return results['failed'] == 0


def print_summary(results):
    """Print test summary"""
    total = results['passed'] + results['failed'] + results['warnings'] + results['skipped']
    print(f"\n{C.BOLD}{'='*60}")
    print(f"  Resultat")
    print(f"{'='*60}{C.END}")

    if results['passed'] > 0:
        print(f"  {C.OK}Godkända:  {results['passed']}{C.END}")
    if results['failed'] > 0:
        print(f"  {C.FAIL}Misslyckade: {results['failed']}{C.END}")
    if results['warnings'] > 0:
        print(f"  {C.WARN}Varningar:   {results['warnings']}{C.END}")
    if results['skipped'] > 0:
        print(f"  {C.DIM}Överhoppade: {results['skipped']}{C.END}")

    print()
    if results['failed'] == 0:
        print(f"  {C.OK}{C.BOLD}Alla tester godkända! Systemet är redo.{C.END}")
    else:
        print(f"  {C.FAIL}{C.BOLD}Vissa tester misslyckades. Se detaljer ovan.{C.END}")
    print()


def main():
    parser = argparse.ArgumentParser(description='JPsecurity - Anslutningstest')
    parser.add_argument('--config', '-c', default='cameras.yaml',
                       help='Sökväg till cameras.yaml (default: cameras.yaml)')
    parser.add_argument('--server-only', '-s', action='store_true',
                       help='Testa bara serveranslutning (hoppa över kameror)')
    args = parser.parse_args()

    # Find config file
    config_path = args.config
    if not os.path.isabs(config_path):
        # Try relative to script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(script_dir, config_path)):
            config_path = os.path.join(script_dir, config_path)

    try:
        success = asyncio.run(run_tests(config_path, args.server_only))
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n\n  {C.WARN}Avbrutet av användaren.{C.END}\n")
        sys.exit(130)


if __name__ == '__main__':
    main()
