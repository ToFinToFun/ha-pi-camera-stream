#!/usr/bin/env python3
"""
Camera Client v5.2
==================

Fångar video från en eller flera kameror och strömmar JPEG-frames
via WebSocket till relay-servern.

Plattformar: Raspberry Pi (alla modeller), Windows, Linux x86/ARM

Stöder:
- Axis nätverkskameror (via VAPIX HTTP API eller RTSP)
- Generiska RTSP/ONVIF nätverkskameror (via OpenCV)
- Raspberry Pi Camera Module (via picamera2) [bara Pi]
- USB-kameror (via OpenCV)
- GPIO digitala in/utgångar [bara Pi]
- Testläge med genererade bilder (för utveckling)

Plattformsspecifikt:
- GPIO: Aktiveras automatiskt på Raspberry Pi, inaktiveras på Windows/x86
- Pi Camera Module: Bara tillgängligt på Raspberry Pi
- Hårdvarudetektering: Anpassar sig automatiskt till plattformen
- Nätverkskameror (Axis/RTSP): Fungerar på alla plattformar
"""

import asyncio
import json
import logging
import signal
import sys
import time
import argparse
import io
import threading
import queue
import os
import platform
import base64
from datetime import datetime
from pathlib import Path

# Konfigurera logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('pi-camera')


# ============================================================================
# Konfiguration
# ============================================================================

def load_config(config_path):
    """Ladda konfiguration från YAML-fil."""
    try:
        import yaml
    except ImportError:
        logger.error("PyYAML behövs. Installera med: pip install pyyaml")
        sys.exit(1)

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    return config


DEFAULT_CONFIG = {
    'server': {
        'url': 'ws://localhost:8765',
        'secret': 'change-me-to-a-strong-secret',
        'reconnect_delay': 1,
        'max_reconnect_delay': 60,
    },
    'cameras': [],
    'detection': {
        'auto': True,       # Autodetektera hårdvara och välj nivå
        'level': None,      # Manuell override: 0-5
    },
    'recording': {
        'enabled': True,
        'storage_path': '/var/lib/pi-camera/recordings',
        'max_storage_mb': 5000,
        'max_age_days': 30,
        'pre_record_seconds': 5,
        'post_record_seconds': 10,
    },
    'notifications': {
        'enabled': False,
    },
    'adaptive_quality': {
        'enabled': False,
        'min_quality': 20,
        'max_quality': 90,
        'target_fps': 10,
        'bandwidth_limit_kbps': 0,
    },
}


# ============================================================================
# Hälsorapportering
# ============================================================================

class HealthReporter:
    """Samlar in och rapporterar systemhälsa."""

    def __init__(self, interval=30):
        self.interval = interval
        self.last_report = 0

    def get_health(self):
        health = {
            'uptime': self._get_uptime(),
            'cpu_usage': self._get_cpu_usage(),
            'memory_usage': self._get_memory_usage(),
            'disk_free': self._get_disk_free(),
            'cpu_temp': self._get_cpu_temp(),
        }
        return health

    def should_report(self):
        now = time.time()
        if now - self.last_report >= self.interval:
            self.last_report = now
            return True
        return False

    def _get_uptime(self):
        try:
            with open('/proc/uptime', 'r') as f:
                return float(f.read().split()[0])
        except Exception:
            return 0

    def _get_cpu_usage(self):
        try:
            load1, _, _ = os.getloadavg()
            return round(load1 * 100 / os.cpu_count(), 1)
        except Exception:
            return 0

    def _get_memory_usage(self):
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                parts = line.split()
                info[parts[0].rstrip(':')] = int(parts[1])
            total = info.get('MemTotal', 1)
            available = info.get('MemAvailable', total)
            return round((1 - available / total) * 100, 1)
        except Exception:
            return 0

    def _get_disk_free(self):
        try:
            stat = os.statvfs('/')
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            return round(free_gb, 2)
        except Exception:
            return 0

    def _get_cpu_temp(self):
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                return round(int(f.read().strip()) / 1000, 1)
        except Exception:
            return 0


# ============================================================================
# Adaptiv kvalitet
# ============================================================================

class AdaptiveQuality:
    """Justerar JPEG-kvalitet och FPS baserat på bandbredd."""

    def __init__(self, config=None):
        config = config or {}
        self.enabled = config.get('enabled', False)
        self.min_quality = config.get('min_quality', 20)
        self.max_quality = config.get('max_quality', 90)
        self.bandwidth_limit_kbps = config.get('bandwidth_limit_kbps', 0)
        self.current_quality = 70
        self.bytes_window = []
        self.fps_window = []

    def record_frame(self, frame_size):
        now = time.time()
        self.bytes_window.append((now, frame_size))
        self.fps_window.append(now)
        cutoff = now - 5
        self.bytes_window = [(t, s) for t, s in self.bytes_window if t > cutoff]
        self.fps_window = [t for t in self.fps_window if t > cutoff]

    def get_bandwidth_kbps(self):
        if len(self.bytes_window) < 2:
            return 0
        duration = self.bytes_window[-1][0] - self.bytes_window[0][0]
        if duration <= 0:
            return 0
        total_bytes = sum(s for _, s in self.bytes_window)
        return total_bytes / duration / 1024

    def get_actual_fps(self):
        if len(self.fps_window) < 2:
            return 0
        duration = self.fps_window[-1] - self.fps_window[0]
        if duration <= 0:
            return 0
        return (len(self.fps_window) - 1) / duration

    def adjust(self, camera):
        if not self.enabled:
            return
        bw = self.get_bandwidth_kbps()
        if self.bandwidth_limit_kbps > 0 and bw > self.bandwidth_limit_kbps:
            new_quality = max(self.min_quality, camera.quality - 5)
            if new_quality != camera.quality:
                camera.quality = new_quality
        elif self.bandwidth_limit_kbps > 0 and bw < self.bandwidth_limit_kbps * 0.7:
            new_quality = min(self.max_quality, camera.quality + 2)
            if new_quality != camera.quality:
                camera.quality = new_quality


# ============================================================================
# Kamera-backends
# ============================================================================

class CameraBackend:
    """Basklass för kamera-backends."""

    def __init__(self, camera_id, name='Camera', width=640, height=480, fps=15, quality=70, **kwargs):
        self.camera_id = camera_id
        self.name = name
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality

    def start(self):
        raise NotImplementedError

    def capture_frame(self) -> bytes:
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    @property
    def resolution_str(self):
        return f"{self.width}x{self.height}"


class AxisVapixBackend(CameraBackend):
    """Axis nätverkskameror via VAPIX HTTP API."""

    def __init__(self, host, username='root', password='', port=80,
                 use_https=False, camera_number=1, mode='snapshot', **kwargs):
        super().__init__(**kwargs)
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_https = use_https
        self.camera_number = camera_number
        self.mode = mode
        self.session = None
        self._mjpeg_thread = None
        self._frame_queue = queue.Queue(maxsize=2)
        self._running = False

    def _base_url(self):
        protocol = 'https' if self.use_https else 'http'
        return f"{protocol}://{self.host}:{self.port}"

    def start(self):
        import requests
        from requests.auth import HTTPDigestAuth

        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(self.username, self.password)

        try:
            test_url = f"{self._base_url()}/axis-cgi/jpg/image.cgi?resolution={self.width}x{self.height}&camera={self.camera_number}"
            resp = self.session.get(test_url, timeout=10, verify=False)

            if resp.status_code == 401:
                from requests.auth import HTTPBasicAuth
                self.session.auth = HTTPBasicAuth(self.username, self.password)
                resp = self.session.get(test_url, timeout=10, verify=False)

            if resp.status_code != 200:
                raise RuntimeError(f"Axis camera returned HTTP {resp.status_code}")

            logger.info(f"[{self.camera_id}] Axis camera connected: {self.host} ({self.mode} mode)")

            if self.mode == 'mjpeg':
                self._start_mjpeg_stream()

        except Exception as e:
            raise RuntimeError(f"Cannot connect to Axis camera at {self.host}: {e}")

    def _start_mjpeg_stream(self):
        self._running = True
        self._mjpeg_thread = threading.Thread(target=self._mjpeg_reader, daemon=True)
        self._mjpeg_thread.start()

    def _mjpeg_reader(self):
        url = f"{self._base_url()}/axis-cgi/mjpg/video.cgi?resolution={self.width}x{self.height}&camera={self.camera_number}&fps={self.fps}"
        try:
            resp = self.session.get(url, stream=True, timeout=30, verify=False)
            buffer = b''
            for chunk in resp.iter_content(chunk_size=4096):
                if not self._running:
                    break
                buffer += chunk
                while True:
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9', start + 2) if start >= 0 else -1
                    if start >= 0 and end >= 0:
                        frame = buffer[start:end + 2]
                        buffer = buffer[end + 2:]
                        try:
                            self._frame_queue.put_nowait(frame)
                        except queue.Full:
                            try:
                                self._frame_queue.get_nowait()
                            except queue.Empty:
                                pass
                            self._frame_queue.put_nowait(frame)
                    else:
                        break
        except Exception as e:
            if self._running:
                logger.error(f"[{self.camera_id}] MJPEG stream error: {e}")

    def capture_frame(self) -> bytes:
        if self.mode == 'mjpeg':
            try:
                return self._frame_queue.get(timeout=5)
            except queue.Empty:
                raise RuntimeError("No frame available from MJPEG stream")
        else:
            url = f"{self._base_url()}/axis-cgi/jpg/image.cgi?resolution={self.width}x{self.height}&compression={100-self.quality}&camera={self.camera_number}"
            resp = self.session.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                raise RuntimeError(f"Snapshot failed: HTTP {resp.status_code}")
            return resp.content

    def stop(self):
        self._running = False


class RTSPBackend(CameraBackend):
    """RTSP-kameror via OpenCV."""

    def __init__(self, rtsp_url, **kwargs):
        super().__init__(**kwargs)
        self.rtsp_url = rtsp_url
        self.cap = None
        self.cv2 = None

    def start(self):
        try:
            import cv2
            self.cv2 = cv2
        except ImportError:
            raise RuntimeError("OpenCV behövs. Installera med: pip install opencv-python-headless")

        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
        self.cap = self.cv2.VideoCapture(self.rtsp_url, self.cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open RTSP stream: {self.rtsp_url}")

        self.cap.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
        actual_w = int(self.cap.get(self.cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(self.cv2.CAP_PROP_FRAME_HEIGHT))
        if actual_w > 0:
            self.width = actual_w
        if actual_h > 0:
            self.height = actual_h
        logger.info(f"[{self.camera_id}] RTSP connected: {self.rtsp_url} ({self.resolution_str})")

    def capture_frame(self) -> bytes:
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("Failed to capture RTSP frame")
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = self.cv2.resize(frame, (self.width, self.height))
        _, buffer = self.cv2.imencode('.jpg', frame, [self.cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buffer.tobytes()

    def stop(self):
        if self.cap:
            self.cap.release()


class PiCameraBackend(CameraBackend):
    """Raspberry Pi Camera Module via picamera2."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.camera = None

    def start(self):
        try:
            from picamera2 import Picamera2
        except ImportError:
            raise RuntimeError("picamera2 behövs. Installera med: sudo apt install python3-picamera2")

        self.camera = Picamera2()
        config = self.camera.create_still_configuration(
            main={"size": (self.width, self.height), "format": "RGB888"}
        )
        self.camera.configure(config)
        self.camera.start()
        logger.info(f"[{self.camera_id}] Pi Camera started at {self.resolution_str}")

    def capture_frame(self) -> bytes:
        import cv2
        frame = self.camera.capture_array()
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buffer.tobytes()

    def stop(self):
        if self.camera:
            self.camera.stop()
            self.camera.close()


class USBCameraBackend(CameraBackend):
    """USB-kameror via OpenCV."""

    def __init__(self, device=0, **kwargs):
        super().__init__(**kwargs)
        self.device = device
        self.cap = None
        self.cv2 = None

    def start(self):
        try:
            import cv2
            self.cv2 = cv2
        except ImportError:
            raise RuntimeError("OpenCV behövs. Installera med: pip install opencv-python-headless")

        self.cap = self.cv2.VideoCapture(self.device)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open USB camera {self.device}")

        self.cap.set(self.cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(self.cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(self.cv2.CAP_PROP_FPS, self.fps)
        self.width = int(self.cap.get(self.cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(self.cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"[{self.camera_id}] USB Camera started at {self.resolution_str}")

    def capture_frame(self) -> bytes:
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("Failed to capture frame")
        _, buffer = self.cv2.imencode('.jpg', frame, [self.cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buffer.tobytes()

    def stop(self):
        if self.cap:
            self.cap.release()


class TestBackend(CameraBackend):
    """Testbackend som genererar testbilder."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.frame_count = 0

    def start(self):
        try:
            from PIL import Image, ImageDraw
            self.Image = Image
            self.ImageDraw = ImageDraw
        except ImportError:
            raise RuntimeError("Pillow behövs. Installera med: pip install Pillow")
        logger.info(f"[{self.camera_id}] Test camera started at {self.resolution_str}")

    def capture_frame(self) -> bytes:
        import math
        self.frame_count += 1

        img = self.Image.new('RGB', (self.width, self.height), color=(30, 30, 50))
        draw = self.ImageDraw.Draw(img)

        for x in range(0, self.width, 40):
            draw.line([(x, 0), (x, self.height)], fill=(50, 50, 70), width=1)
        for y in range(0, self.height, 40):
            draw.line([(0, y), (self.width, y)], fill=(50, 50, 70), width=1)

        hue = hash(self.camera_id) % 360
        r = int(128 + 127 * math.sin(math.radians(hue)))
        g = int(128 + 127 * math.sin(math.radians(hue + 120)))
        b = int(128 + 127 * math.sin(math.radians(hue + 240)))

        cx = int(self.width / 2 + (self.width / 4) * math.sin(self.frame_count * 0.05))
        cy = int(self.height / 2 + (self.height / 4) * math.cos(self.frame_count * 0.03))
        draw.ellipse([cx-20, cy-20, cx+20, cy+20], fill=(r, g, b))

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        draw.text((10, 10), f"Camera: {self.name}", fill=(255, 255, 255))
        draw.text((10, 30), f"ID: {self.camera_id}", fill=(200, 200, 200))
        draw.text((10, 50), f"Frame: {self.frame_count}", fill=(200, 200, 200))
        draw.text((10, 70), f"Time: {timestamp}", fill=(200, 200, 200))
        draw.text((10, 90), f"Res: {self.resolution_str}", fill=(200, 200, 200))

        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=self.quality)
        return buffer.getvalue()

    def stop(self):
        logger.info(f"[{self.camera_id}] Test camera stopped")


# ============================================================================
# Kamera-factory
# ============================================================================

def create_camera_backend(cam_config):
    common_kwargs = {
        'camera_id': cam_config.get('camera_id', 'camera-1'),
        'name': cam_config.get('name', 'Camera'),
        'width': cam_config.get('width', 640),
        'height': cam_config.get('height', 480),
        'fps': cam_config.get('fps', 15),
        'quality': cam_config.get('quality', 70),
    }

    backend_type = cam_config.get('type', 'test')

    if backend_type == 'axis':
        return AxisVapixBackend(
            host=cam_config['host'],
            username=cam_config.get('username', 'root'),
            password=cam_config.get('password', ''),
            port=cam_config.get('port', 80),
            use_https=cam_config.get('use_https', False),
            camera_number=cam_config.get('camera_number', 1),
            mode=cam_config.get('mode', 'snapshot'),
            **common_kwargs,
        )
    elif backend_type == 'rtsp':
        return RTSPBackend(rtsp_url=cam_config['rtsp_url'], **common_kwargs)
    elif backend_type == 'picamera':
        return PiCameraBackend(**common_kwargs)
    elif backend_type == 'usb':
        return USBCameraBackend(device=cam_config.get('device', 0), **common_kwargs)
    elif backend_type == 'test':
        return TestBackend(**common_kwargs)
    else:
        raise ValueError(f"Unknown camera type: {backend_type}")


# ============================================================================
# CameraStreamClient – en kamera per WebSocket
# ============================================================================

class CameraStreamClient:
    """Hanterar WebSocket-anslutningen för en enskild kamera."""

    def __init__(self, server_url, secret, camera_backend, motion_config=None,
                 notification_manager=None, object_detector=None,
                 adaptive_config=None, ptz_config=None, recorder=None,
                 detection_level=1, hardware_summary=None, gpio_controller=None,
                 client_name=None):
        self.server_url = server_url
        self.secret = secret
        self.camera = camera_backend
        self.camera_id = camera_backend.camera_id
        self.camera_name = camera_backend.name
        self.client_name = client_name or platform.node()
        self.running = False
        self.ws = None
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60
        self.frames_sent = 0
        self.bytes_sent = 0
        self.last_stats_time = time.time()
        self.log = logging.getLogger(f'stream.{self.camera_id}')

        # Detektering
        self.detection_level = detection_level
        self.hardware_summary = hardware_summary or {}
        self.motion_detector = None
        self.object_detector = object_detector
        self.notification_manager = notification_manager

        # Lokal inspelning
        self.recorder = recorder

        # Adaptiv kvalitet
        self.adaptive = AdaptiveQuality(adaptive_config) if adaptive_config else None

        # Hälsa
        self.health = HealthReporter()

        # PTZ
        self.ptz = None
        if ptz_config and ptz_config.get('enabled'):
            from ptz_controller import AxisPTZController
            self.ptz = AxisPTZController(
                host=ptz_config.get('host', ''),
                username=ptz_config.get('username', 'root'),
                password=ptz_config.get('password', ''),
                port=ptz_config.get('port', 80),
                use_https=ptz_config.get('use_https', False),
            )
            self.ptz.start()

        # Initiera rörelsedetektering baserat på nivå
        self._init_detection(motion_config)

        # GPIO
        self.gpio = gpio_controller

        # Senaste frame
        self._last_frame = None

    def _init_detection(self, motion_config):
        """Initiera detektering baserat på aktuell nivå."""
        if self.detection_level >= 1 and motion_config and motion_config.get('enabled'):
            try:
                from motion_detector import MotionDetector
                self.motion_detector = MotionDetector(motion_config)
                self.log.info(f"Motion detection enabled (level {self.detection_level})")
            except ImportError:
                self.log.warning("motion_detector module not available")

    def set_detection_level(self, level, config=None):
        """Ändra detekteringsnivå dynamiskt (fjärrstyrning)."""
        old_level = self.detection_level
        self.detection_level = level
        self.log.info(f"Detection level changed: {old_level} -> {level}")

        # Uppdatera moduler
        if level == 0:
            self.motion_detector = None
            if self.object_detector:
                self.object_detector.enabled = False

        elif level >= 1 and level <= 2:
            if not self.motion_detector:
                try:
                    from motion_detector import MotionDetector
                    self.motion_detector = MotionDetector(config or {'enabled': True, 'sensitivity': 50})
                except ImportError:
                    pass
            if self.object_detector:
                self.object_detector.enabled = False

        elif level >= 3:
            if not self.motion_detector:
                try:
                    from motion_detector import MotionDetector
                    self.motion_detector = MotionDetector(config or {'enabled': True, 'sensitivity': 50})
                except ImportError:
                    pass
            if self.object_detector:
                self.object_detector.enabled = True

    async def connect(self):
        try:
            import websockets
        except ImportError:
            self.log.error("websockets behövs. pip install websockets")
            return

        self.running = True

        while self.running:
            try:
                self.log.info(f"Connecting to {self.server_url}...")
                async with websockets.connect(
                    self.server_url,
                    max_size=10 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=5,
                ) as ws:
                    self.ws = ws
                    self.reconnect_delay = 1
                    await self._register(ws)
                    await self._stream(ws)

            except Exception as e:
                self.log.error(f"Connection error: {e}")
                if not self.running:
                    break
                self.log.info(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    async def _register(self, ws):
        capabilities = {
            'ptz': self.ptz is not None,
            'motion_detection': self.motion_detector is not None,
            'object_detection': self.object_detector is not None and self.object_detector.enabled,
            'local_recording': self.recorder is not None,
            'detection_level': self.detection_level,
            'hardware_summary': self.hardware_summary,
        }

        # Lägg till GPIO-capabilities om GPIO är konfigurerat
        if self.gpio:
            capabilities.update(self.gpio.get_capabilities())

        registration = {
            "type": "register_camera",
            "secret": self.secret,
            "camera_id": self.camera_id,
            "name": self.camera_name,
            "client_name": self.client_name,
            "resolution": self.camera.resolution_str,
            "capabilities": capabilities,
        }
        await ws.send(json.dumps(registration))

        response = await asyncio.wait_for(ws.recv(), timeout=10)
        msg = json.loads(response)

        if msg.get('type') == 'registered':
            self.log.info(f"Camera registered: {msg.get('camera_id')}")
        elif msg.get('type') == 'error':
            raise RuntimeError(f"Registration failed: {msg.get('message')}")

    async def _stream(self, ws):
        frame_interval = 1.0 / self.camera.fps
        self.log.info(f"Streaming at {self.camera.fps} FPS (detection level: {self.detection_level})")

        receive_task = asyncio.create_task(self._receive_messages(ws))
        post_timer_task = asyncio.create_task(self._post_timer_loop())

        # Koppla GPIO event-callback till WebSocket
        if self.gpio:
            gpio_event_queue = asyncio.Queue()
            def gpio_event_handler(event):
                try:
                    gpio_event_queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
            self.gpio.set_event_callback(gpio_event_handler)
            gpio_task = asyncio.create_task(self._gpio_event_loop(ws, gpio_event_queue))
        else:
            gpio_task = None

        try:
            while self.running:
                start_time = time.time()

                try:
                    frame_data = self.camera.capture_frame()
                    self._last_frame = frame_data

                    # Buffra frame i lokal recorder (alltid, för pre-record)
                    if self.recorder:
                        self.recorder.buffer_frame(self.camera_id, frame_data)

                    # Rörelsedetektering
                    if self.motion_detector:
                        result = self.motion_detector.analyze_frame(frame_data)
                        if result['event'] == 'start':
                            await ws.send(json.dumps({
                                'type': 'motion_start',
                                'intensity': result['intensity'],
                            }))
                            # Starta lokal inspelning
                            if self.recorder:
                                self.recorder.motion_event(self.camera_id, 'start', {
                                    'intensity': result['intensity'],
                                })
                            # Push-notis
                            if self.notification_manager:
                                snapshot = frame_data if self.notification_manager.include_snapshot else None
                                self.notification_manager.notify_motion(
                                    self.camera_id, self.camera_name, snapshot
                                )
                        elif result['event'] == 'end':
                            await ws.send(json.dumps({'type': 'motion_end'}))
                            if self.recorder:
                                self.recorder.motion_event(self.camera_id, 'end')

                    # AI-objektdetektering (bara om nivå >= 3)
                    if self.detection_level >= 3 and self.object_detector and self.object_detector.enabled:
                        detections = self.object_detector.detect(frame_data)
                        if detections and self.object_detector.should_alert(detections):
                            classes_str = ', '.join(set(d['class'] for d in detections))
                            await ws.send(json.dumps({
                                'type': 'motion_start',
                                'intensity': 100,
                                'zones': [d['class'] for d in detections],
                                'ai_detections': detections,
                            }))
                            if self.recorder:
                                self.recorder.motion_event(self.camera_id, 'start', {
                                    'ai_detections': [d['class'] for d in detections],
                                })
                            if self.notification_manager:
                                self.notification_manager.notify_motion(
                                    self.camera_id,
                                    f"{self.camera_name} ({classes_str})",
                                    frame_data,
                                )

                    # Skicka frame
                    await ws.send(frame_data)
                    self.frames_sent += 1
                    self.bytes_sent += len(frame_data)

                    # Adaptiv kvalitet
                    if self.adaptive:
                        self.adaptive.record_frame(len(frame_data))
                        self.adaptive.adjust(self.camera)

                    # Hälsorapportering
                    if self.health.should_report():
                        health_data = self.health.get_health()
                        health_data['fps'] = self.camera.fps
                        health_data['bandwidth'] = round(self.adaptive.get_bandwidth_kbps(), 1) if self.adaptive else 0
                        health_data['detection_level'] = self.detection_level

                        # Lagringsstatus
                        if self.recorder:
                            storage_stats = self.recorder.get_storage_stats()
                            health_data['storage'] = storage_stats

                        await ws.send(json.dumps({
                            'type': 'health',
                            **health_data,
                        }))

                    # Statistik
                    now = time.time()
                    if now - self.last_stats_time >= 30:
                        elapsed = now - self.last_stats_time
                        fps = self.frames_sent / elapsed if elapsed > 0 else 0
                        bps = self.bytes_sent / elapsed if elapsed > 0 else 0
                        self.log.info(f"Stats: {fps:.1f} FPS, {bps/1024:.1f} KB/s, level={self.detection_level}")
                        self.frames_sent = 0
                        self.bytes_sent = 0
                        self.last_stats_time = now

                except RuntimeError as e:
                    self.log.warning(f"Frame capture error: {e}")
                    await asyncio.sleep(0.5)
                    continue

                elapsed = time.time() - start_time
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        finally:
            receive_task.cancel()
            post_timer_task.cancel()
            if gpio_task:
                gpio_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
            try:
                await post_timer_task
            except asyncio.CancelledError:
                pass
            if gpio_task:
                try:
                    await gpio_task
                except asyncio.CancelledError:
                    pass

    async def _post_timer_loop(self):
        """Periodiskt kolla post-record timers."""
        try:
            while self.running:
                if self.recorder:
                    self.recorder.check_post_timers()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _gpio_event_loop(self, ws, event_queue):
        """Skicka GPIO-events från kön till servern via WebSocket."""
        try:
            while self.running:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                    if ws.open:
                        event['camera_id'] = self.camera_id
                        await ws.send(json.dumps(event))
                        self.log.debug(f"GPIO event sent: {event.get('pin_name', event.get('scene_name', '?'))}")
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.log.error(f"GPIO event send error: {e}")
        except asyncio.CancelledError:
            pass

    async def _receive_messages(self, ws):
        try:
            async for message in ws:
                try:
                    msg = json.loads(message)
                    await self._handle_message(msg, ws)
                except json.JSONDecodeError:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log.error(f"Receive error: {e}")

    async def _handle_message(self, msg, ws):
        msg_type = msg.get('type')

        if msg_type == 'viewer_command':
            command = msg.get('command')
            params = msg.get('params', {})

            if command == 'set_quality':
                self.camera.quality = max(10, min(100, params.get('quality', 70)))
                self.log.info(f"Quality set to {self.camera.quality}")

            elif command == 'set_fps':
                self.camera.fps = max(1, min(30, params.get('fps', 15)))
                self.log.info(f"FPS set to {self.camera.fps}")

            elif command == 'set_detection_level':
                new_level = params.get('level', self.detection_level)
                self.set_detection_level(new_level, params.get('config'))
                # Bekräfta tillbaka
                await ws.send(json.dumps({
                    'type': 'detection_level_changed',
                    'camera_id': self.camera_id,
                    'level': self.detection_level,
                }))

            elif command == 'get_recordings':
                # On-demand: lista inspelningar
                if self.recorder:
                    recs = self.recorder.list_recordings(
                        camera_id=params.get('camera_id', self.camera_id),
                        date=params.get('date'),
                        limit=params.get('limit', 50),
                    )
                    await ws.send(json.dumps({
                        'type': 'recordings_list',
                        'camera_id': self.camera_id,
                        'recordings': recs,
                        'request_id': msg.get('request_id'),
                    }))

            elif command == 'get_recording_clip':
                # On-demand: hämta ett klipp (skicka frames som base64)
                if self.recorder:
                    clip_path = params.get('clip_path')
                    if clip_path:
                        frames = []
                        for ts, frame_data in self.recorder.get_recording_frames(
                            clip_path,
                            start_frame=params.get('start_frame', 0),
                            max_frames=params.get('max_frames', 100),
                        ):
                            frames.append({
                                'timestamp': ts,
                                'data': base64.b64encode(frame_data).decode('ascii'),
                            })
                        await ws.send(json.dumps({
                            'type': 'recording_clip',
                            'camera_id': self.camera_id,
                            'clip_path': clip_path,
                            'frames': frames,
                            'request_id': msg.get('request_id'),
                        }))

            elif command == 'get_thumbnail':
                # On-demand: hämta thumbnail
                if self.recorder:
                    clip_path = params.get('clip_path')
                    if clip_path:
                        thumb = self.recorder.get_thumbnail(clip_path)
                        if thumb:
                            await ws.send(json.dumps({
                                'type': 'recording_thumbnail',
                                'camera_id': self.camera_id,
                                'clip_path': clip_path,
                                'data': base64.b64encode(thumb).decode('ascii'),
                                'request_id': msg.get('request_id'),
                            }))

            elif command == 'get_storage_stats':
                if self.recorder:
                    stats = self.recorder.get_storage_stats()
                    await ws.send(json.dumps({
                        'type': 'storage_stats',
                        'camera_id': self.camera_id,
                        'stats': stats,
                        'request_id': msg.get('request_id'),
                    }))

        elif msg_type == 'gpio_command':
            if self.gpio:
                command = msg.get('command')
                params = msg.get('params', {})

                if command == 'set_output':
                    name = params.get('name')
                    state = params.get('state', False)
                    success = self.gpio.set_output(name, state)
                    await ws.send(json.dumps({
                        'type': 'gpio_output_changed',
                        'camera_id': self.camera_id,
                        'name': name,
                        'state': state,
                        'success': success,
                    }))

                elif command == 'toggle_output':
                    name = params.get('name')
                    success = self.gpio.toggle_output(name)
                    pin = self.gpio.pins.get(name)
                    await ws.send(json.dumps({
                        'type': 'gpio_output_changed',
                        'camera_id': self.camera_id,
                        'name': name,
                        'state': pin.state if pin else False,
                        'success': success,
                    }))

                elif command == 'activate_scene':
                    scene_name = params.get('scene')
                    success = self.gpio.activate_scene(scene_name)
                    await ws.send(json.dumps({
                        'type': 'scene_activated',
                        'camera_id': self.camera_id,
                        'scene': scene_name,
                        'success': success,
                    }))

                elif command == 'get_gpio_states':
                    states = self.gpio.get_all_states()
                    await ws.send(json.dumps({
                        'type': 'gpio_states',
                        'camera_id': self.camera_id,
                        'states': states,
                        'request_id': msg.get('request_id'),
                    }))

                elif command == 'set_pin_enabled':
                    pin_name = params.get('pin_name')
                    enabled = params.get('enabled', True)
                    success = self.gpio.set_pin_enabled(pin_name, enabled)
                    await ws.send(json.dumps({
                        'type': 'gpio_pin_enabled',
                        'camera_id': self.camera_id,
                        'pin_name': pin_name,
                        'enabled': enabled,
                        'success': success,
                    }))

                elif command == 'rename_pin_label':
                    pin_name = params.get('pin_name')
                    label = params.get('label', '')
                    success = self.gpio.rename_pin_label(pin_name, label)
                    await ws.send(json.dumps({
                        'type': 'gpio_pin_renamed',
                        'camera_id': self.camera_id,
                        'pin_name': pin_name,
                        'label': label,
                        'success': success,
                    }))

                elif command == 'get_notification_config':
                    if self.notification_manager:
                        await ws.send(json.dumps({
                            'type': 'notification_config',
                            'camera_id': self.camera_id,
                            'config': self.notification_manager.get_config_summary(),
                            'request_id': msg.get('request_id'),
                        }))

                elif command == 'update_notification_config':
                    if self.notification_manager:
                        new_config = params.get('config', {})
                        self.notification_manager.update_config(new_config)
                        await ws.send(json.dumps({
                            'type': 'notification_config_updated',
                            'camera_id': self.camera_id,
                            'success': True,
                        }))

        elif msg_type == 'ptz_command':
            if self.ptz:
                action = msg.get('action')
                params = msg.get('params', {})
                if action == 'move':
                    self.ptz.move(pan=params.get('pan', 0), tilt=params.get('tilt', 0), speed=params.get('speed', 50))
                elif action == 'zoom':
                    self.ptz.zoom(direction=params.get('direction', 'in'), speed=params.get('speed', 50))
                elif action == 'preset':
                    self.ptz.go_to_preset(params.get('name', 'home'))
                elif action == 'home':
                    self.ptz.go_home()
                elif action == 'stop':
                    self.ptz.stop()

    def stop(self):
        self.log.info("Stopping...")
        self.running = False


# ============================================================================
# Multi-kamera manager
# ============================================================================

class MultiCameraManager:
    """Hanterar flera kameror och deras WebSocket-anslutningar."""

    def __init__(self, config):
        self.config = config
        self.cameras = []
        self.clients = []
        self.running = False
        self.client_name = config.get('client_name', os.environ.get('PI_CLIENT_NAME', platform.node()))
        logger.info(f"Pi client name: {self.client_name}")

        # Globala moduler
        self.notification_manager = None
        self.object_detector = None
        self.recorder = None
        self.detection_config = None
        self.gpio_controller = None

        # Initiera notiser
        notif_config = config.get('notifications', {})
        if notif_config.get('enabled'):
            try:
                from notifications import NotificationManager
                self.notification_manager = NotificationManager(notif_config)
            except ImportError:
                logger.warning("notifications module not available")

        # Hårdvarudetektering och auto-konfiguration
        detection_cfg = config.get('detection', {'auto': True})
        if detection_cfg.get('auto', True) and detection_cfg.get('level') is None:
            logger.info("Running hardware auto-detection...")
            try:
                from hardware_detect import auto_configure_detection
                self.detection_config = auto_configure_detection(
                    config_dir=config.get('recording', {}).get('storage_path', '/var/lib/pi-camera')
                )
                logger.info(
                    f"Auto-detected level: {self.detection_config['detection_level']} "
                    f"({self.detection_config['detection_level_name']})"
                )
            except Exception as e:
                logger.warning(f"Hardware detection failed: {e}, using level 1")
                self.detection_config = {
                    'detection_level': 1,
                    'detection_level_name': 'basic_motion',
                    'motion': {'enabled': True, 'sensitivity': 50},
                    'object_detection': {'enabled': False},
                    'hardware_summary': {},
                }
        else:
            # Manuell nivå
            level = detection_cfg.get('level', 1)
            from hardware_detect import DETECTION_LEVELS, auto_configure_detection
            try:
                self.detection_config = auto_configure_detection(
                    config_dir=config.get('recording', {}).get('storage_path', '/var/lib/pi-camera')
                )
                self.detection_config['detection_level'] = level
                self.detection_config['detection_level_name'] = DETECTION_LEVELS.get(level, {}).get('name', 'unknown')
            except Exception:
                self.detection_config = {
                    'detection_level': level,
                    'detection_level_name': 'manual',
                    'motion': {'enabled': level >= 1, 'sensitivity': 50},
                    'object_detection': {'enabled': level >= 3},
                    'hardware_summary': {},
                }

        # Initiera objektdetektering om nivå >= 3
        if self.detection_config.get('detection_level', 0) >= 3:
            od_config = self.detection_config.get('object_detection', {})
            if od_config.get('enabled'):
                try:
                    from object_detector import ObjectDetector
                    self.object_detector = ObjectDetector(od_config)
                    self.object_detector.initialize()
                except ImportError:
                    logger.warning("object_detector module not available")

        # Initiera lokal inspelning
        rec_config = config.get('recording', DEFAULT_CONFIG['recording'])
        if rec_config.get('enabled', True):
            try:
                from local_recorder import LocalRecorder
                self.recorder = LocalRecorder(rec_config)
            except ImportError:
                logger.warning("local_recorder module not available")

        # Initiera GPIO (bara tillgängligt på Raspberry Pi, simuleringsläge på andra plattformar)
        gpio_config = config.get('gpio', {})
        is_windows = sys.platform == 'win32'
        if gpio_config.get('inputs') or gpio_config.get('outputs'):
            try:
                from gpio_controller import GPIOController
                self.gpio_controller = GPIOController(gpio_config)
                self.gpio_controller.start()
                from gpio_controller import GPIO_AVAILABLE
                mode = 'simulering' if is_windows or not GPIO_AVAILABLE else 'hårdvara'
                logger.info(f"GPIO Controller started ({mode}): {len(self.gpio_controller.pins)} pins, "
                            f"{len(self.gpio_controller.scenes)} scenes")
            except Exception as e:
                logger.warning(f"GPIO initialization failed: {e}")
        elif is_windows:
            logger.info("GPIO: Inaktiverat (Windows-plattform, GPIO kräver Raspberry Pi)")

    def setup(self):
        server_config = self.config.get('server', DEFAULT_CONFIG['server'])
        motion_config = self.detection_config.get('motion', {})
        adaptive_config = self.config.get('adaptive_quality', {})
        detection_level = self.detection_config.get('detection_level', 1)
        hw_summary = self.detection_config.get('hardware_summary', {})

        for cam_config in self.config.get('cameras', []):
            try:
                camera = create_camera_backend(cam_config)
                camera.start()

                # PTZ-konfiguration
                ptz_config = None
                if cam_config.get('ptz', {}).get('enabled') and cam_config.get('type') == 'axis':
                    ptz_config = {
                        'enabled': True,
                        'host': cam_config['host'],
                        'username': cam_config.get('username', 'root'),
                        'password': cam_config.get('password', ''),
                        'port': cam_config.get('port', 80),
                        'use_https': cam_config.get('use_https', False),
                    }

                client = CameraStreamClient(
                    server_url=server_config['url'],
                    secret=server_config['secret'],
                    camera_backend=camera,
                    motion_config=motion_config,
                    notification_manager=self.notification_manager,
                    object_detector=self.object_detector,
                    adaptive_config=adaptive_config,
                    ptz_config=ptz_config,
                    recorder=self.recorder,
                    detection_level=detection_level,
                    hardware_summary=hw_summary,
                    gpio_controller=self.gpio_controller,
                    client_name=self.client_name,
                )

                self.cameras.append(camera)
                self.clients.append(client)
                logger.info(f"Camera '{camera.name}' ({camera.camera_id}) ready")

            except Exception as e:
                logger.error(f"Failed to setup camera '{cam_config.get('name', 'unknown')}': {e}")

        if not self.clients:
            logger.error("No cameras were successfully initialized")
            sys.exit(1)

        logger.info(f"Initialized {len(self.clients)} camera(s) at detection level {detection_level}")

    async def run(self):
        self.running = True
        tasks = [asyncio.create_task(client.connect()) for client in self.clients]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    def stop(self):
        self.running = False
        for client in self.clients:
            client.stop()
        for camera in self.cameras:
            try:
                camera.stop()
            except Exception as e:
                logger.error(f"Error stopping camera: {e}")
        if self.recorder:
            self.recorder.stop_all()
        if self.gpio_controller:
            self.gpio_controller.stop()


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='Pi Camera Streaming Client v4.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXEMPEL:
  # Med konfigurationsfil (rekommenderat)
  python camera_client.py --config cameras.yaml

  # Axis nätverkskamera
  python camera_client.py --server ws://relay:8765 --secret KEY \\
      --type axis --host 192.168.1.100 --username root --password pass \\
      --camera-id axis-entre --name "Entré"

  # Testläge
  python camera_client.py --server ws://relay:8765 --secret KEY \\
      --type test --camera-id test-1 --name "Test"

  # Kör hårdvarudetektering
  python camera_client.py --detect-hardware
        """
    )

    parser.add_argument('--config', type=str, default=None, help='YAML-konfigurationsfil')
    parser.add_argument('--server', default='ws://localhost:8765', help='WebSocket URL')
    parser.add_argument('--secret', default='change-me-to-a-strong-secret', help='Hemlig nyckel')
    parser.add_argument('--type', choices=['axis', 'rtsp', 'picamera', 'usb', 'test'],
                        default='test', help='Kameratyp')
    parser.add_argument('--camera-id', default='camera-1', help='Unikt kamera-ID')
    parser.add_argument('--name', default='Camera', help='Visningsnamn')
    parser.add_argument('--host', help='Axis kamerans IP/hostname')
    parser.add_argument('--username', default='root', help='Användarnamn')
    parser.add_argument('--password', default='', help='Lösenord')
    parser.add_argument('--axis-port', type=int, default=80, help='Axis HTTP-port')
    parser.add_argument('--axis-https', action='store_true', help='Använd HTTPS')
    parser.add_argument('--axis-camera-number', type=int, default=1, help='Kameranummer')
    parser.add_argument('--axis-mode', choices=['snapshot', 'mjpeg'], default='snapshot')
    parser.add_argument('--rtsp-url', help='RTSP-URL')
    parser.add_argument('--device', type=int, default=0, help='USB-kamera enhetsnummer')
    parser.add_argument('--width', type=int, default=640, help='Bildbredd')
    parser.add_argument('--height', type=int, default=480, help='Bildhöjd')
    parser.add_argument('--fps', type=int, default=15, help='FPS')
    parser.add_argument('--quality', type=int, default=70, help='JPEG-kvalitet 1-100')
    parser.add_argument('--detection-level', type=int, default=None,
                        help='Detekteringsnivå 0-5 (None = auto)')
    parser.add_argument('--detect-hardware', action='store_true',
                        help='Kör bara hårdvarudetektering och avsluta')
    parser.add_argument('--no-recording', action='store_true', help='Stäng av lokal inspelning')

    return parser.parse_args()


def build_config_from_args(args):
    cam = {
        'type': args.type,
        'camera_id': args.camera_id,
        'name': args.name,
        'width': args.width,
        'height': args.height,
        'fps': args.fps,
        'quality': args.quality,
    }

    if args.type == 'axis':
        if not args.host:
            logger.error("--host krävs för Axis-kameror")
            sys.exit(1)
        cam.update({
            'host': args.host,
            'username': args.username,
            'password': args.password,
            'port': args.axis_port,
            'use_https': args.axis_https,
            'camera_number': args.axis_camera_number,
            'mode': args.axis_mode,
        })
    elif args.type == 'rtsp':
        if not args.rtsp_url:
            logger.error("--rtsp-url krävs för RTSP-kameror")
            sys.exit(1)
        cam['rtsp_url'] = args.rtsp_url
    elif args.type == 'usb':
        cam['device'] = args.device

    config = {
        'server': {
            'url': args.server,
            'secret': args.secret,
        },
        'cameras': [cam],
        'detection': {
            'auto': args.detection_level is None,
            'level': args.detection_level,
        },
        'recording': {
            'enabled': not args.no_recording,
        },
    }

    return config


def main():
    args = parse_args()

    # Bara hårdvarudetektering
    if args.detect_hardware:
        from hardware_detect import auto_configure_detection
        config = auto_configure_detection()
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return

    if args.config:
        config = load_config(args.config)
        # Tillåt override via kommandoradsargument
        if args.server != 'ws://localhost:8765':
            config.setdefault('server', {})['url'] = args.server
        if args.secret != 'change-me-to-a-strong-secret':
            config.setdefault('server', {})['secret'] = args.secret
        if args.detection_level is not None:
            config.setdefault('detection', {})['level'] = args.detection_level
            config['detection']['auto'] = False
    else:
        config = build_config_from_args(args)

    logger.info(f"Starting Pi Camera Client v5.0 with {len(config.get('cameras', []))} camera(s)")

    manager = MultiCameraManager(config)

    def signal_handler(sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        manager.stop()

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)

    try:
        manager.setup()
        asyncio.run(manager.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        manager.stop()
        logger.info("All cameras stopped")


if __name__ == '__main__':
    main()
