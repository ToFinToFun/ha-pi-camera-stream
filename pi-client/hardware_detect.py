"""
Hardware Detection & Auto-Configuration Module
===============================================

Detekterar hårdvarukapacitet och väljer automatiskt rätt
detekteringsnivå. Kör benchmark vid första start och sparar
resultaten för framtida användning.

Detekteringsnivåer:
  Level 0 - NONE:     Ingen detektering, bara streaming
  Level 1 - BASIC:    Enkel rörelsedetektering (frame diff, ~5% CPU)
  Level 2 - ADVANCED: Rörelsedetektering + zoner + histogram (~15% CPU)
  Level 3 - AI_LITE:  MobileNet SSD objektdetektering (~50% CPU)
  Level 4 - AI_FULL:  YOLOv8 Nano objektdetektering (~80% CPU)
  Level 5 - AI_ACCEL: AI med hårdvaruaccelerator (Coral/NPU, ~10% CPU)
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger('hardware')

# Detekteringsnivåer
DETECTION_LEVELS = {
    0: {
        'name': 'none',
        'label': 'Ingen detektering',
        'description': 'Bara videoström, ingen analys',
        'min_cpu_score': 0,
        'min_ram_mb': 128,
    },
    1: {
        'name': 'basic_motion',
        'label': 'Enkel rörelsedetektering',
        'description': 'Jämför frames, detekterar förändringar. Mycket lätt.',
        'min_cpu_score': 50,
        'min_ram_mb': 256,
    },
    2: {
        'name': 'advanced_motion',
        'label': 'Avancerad rörelsedetektering',
        'description': 'Rörelsedetektering med zoner, histogram och brusfiltrering.',
        'min_cpu_score': 150,
        'min_ram_mb': 384,
    },
    3: {
        'name': 'ai_lite',
        'label': 'AI Lite (MobileNet)',
        'description': 'Objektdetektering med MobileNet SSD. Identifierar personer, bilar etc.',
        'min_cpu_score': 500,
        'min_ram_mb': 512,
    },
    4: {
        'name': 'ai_full',
        'label': 'AI Full (YOLOv8)',
        'description': 'Avancerad objektdetektering med YOLOv8 Nano. Bäst precision.',
        'min_cpu_score': 1000,
        'min_ram_mb': 1024,
    },
    5: {
        'name': 'ai_accelerated',
        'label': 'AI Accelererad (Coral/NPU)',
        'description': 'AI med hårdvaruaccelerator. Snabb och CPU-effektiv.',
        'min_cpu_score': 100,  # Lågt CPU-krav tack vare accelerator
        'min_ram_mb': 512,
        'requires_accelerator': True,
    },
}


# ── GPIO Pin-map per Pi-modell ──────────────────────────────────────────────
# Alla moderna Pi:ar (3B+, 4, 5, Zero 2W) har samma 40-pin header.
# Dessa är de GPIO-pinnar som är säkra att använda (undviker I2C, SPI, UART defaults).

GPIO_PIN_MAP = {
    # BCM-nummer: {'name': default-namn, 'physical': fysisk pin, 'notes': ''}
    # Säkra generella GPIO-pinnar (inga speciella funktioner som default)
    4:  {'name': 'gpio_4',  'physical': 7,  'notes': ''},
    5:  {'name': 'gpio_5',  'physical': 29, 'notes': ''},
    6:  {'name': 'gpio_6',  'physical': 31, 'notes': ''},
    12: {'name': 'gpio_12', 'physical': 32, 'notes': 'PWM0'},
    13: {'name': 'gpio_13', 'physical': 33, 'notes': 'PWM1'},
    16: {'name': 'gpio_16', 'physical': 36, 'notes': ''},
    17: {'name': 'gpio_17', 'physical': 11, 'notes': ''},
    18: {'name': 'gpio_18', 'physical': 12, 'notes': 'PCM_CLK'},
    19: {'name': 'gpio_19', 'physical': 35, 'notes': ''},
    20: {'name': 'gpio_20', 'physical': 38, 'notes': ''},
    21: {'name': 'gpio_21', 'physical': 40, 'notes': ''},
    22: {'name': 'gpio_22', 'physical': 15, 'notes': ''},
    23: {'name': 'gpio_23', 'physical': 16, 'notes': ''},
    24: {'name': 'gpio_24', 'physical': 18, 'notes': ''},
    25: {'name': 'gpio_25', 'physical': 22, 'notes': ''},
    26: {'name': 'gpio_26', 'physical': 37, 'notes': ''},
    27: {'name': 'gpio_27', 'physical': 13, 'notes': ''},
}

# Pinnar reserverade för speciella funktioner (inte i standard-listan)
GPIO_RESERVED_PINS = {
    0:  'I2C0 SDA',
    1:  'I2C0 SCL',
    2:  'I2C1 SDA',
    3:  'I2C1 SCL',
    7:  'SPI0 CE1',
    8:  'SPI0 CE0',
    9:  'SPI0 MISO',
    10: 'SPI0 MOSI',
    11: 'SPI0 SCLK',
    14: 'UART TX',
    15: 'UART RX',
}

# Pi-modellspecifika begränsningar
GPIO_MODEL_NOTES = {
    'RPi 5': {
        'total_gpio': 28,
        'notes': 'RP1 southbridge, ny GPIO-arkitektur. gpiozero rekommenderas.',
        'library': 'gpiozero',
    },
    'RPi 4': {
        'total_gpio': 28,
        'notes': 'BCM2711. Stodjer RPi.GPIO och gpiozero.',
        'library': 'RPi.GPIO',
    },
    'RPi 3': {
        'total_gpio': 28,
        'notes': 'BCM2837. Stodjer RPi.GPIO och gpiozero.',
        'library': 'RPi.GPIO',
    },
    'RPi Zero 2': {
        'total_gpio': 28,
        'notes': 'BCM2710A1. Samma pinout som Pi 3, men lagre prestanda.',
        'library': 'RPi.GPIO',
    },
    'RPi Zero': {
        'total_gpio': 28,
        'notes': 'BCM2835. 40-pin header (samma layout). Begransad CPU.',
        'library': 'RPi.GPIO',
    },
}


def get_available_gpio_pins(model_short=None):
    """Returnera tillgangliga GPIO-pinnar for given Pi-modell.
    
    Alla moderna Pi:ar har samma 40-pin header, sa pin-mappen ar densamma.
    Skillnaden ar rekommenderat GPIO-bibliotek.
    """
    pins = []
    for bcm, info in sorted(GPIO_PIN_MAP.items()):
        pins.append({
            'bcm': bcm,
            'physical': info['physical'],
            'name': info['name'],
            'notes': info['notes'],
        })
    
    model_info = GPIO_MODEL_NOTES.get(model_short, {})
    
    return {
        'pins': pins,
        'pin_count': len(pins),
        'recommended_library': model_info.get('library', 'gpiozero'),
        'model_notes': model_info.get('notes', ''),
        'reserved': {bcm: reason for bcm, reason in GPIO_RESERVED_PINS.items()},
    }


def get_device_name():
    """Generera ett default-namn baserat pa hostname och Pi-modell."""
    import socket
    hostname = socket.gethostname()
    
    # Forsok lasa Pi-modell
    model_short = ''
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip('\x00').strip()
            if 'Pi 5' in model:
                model_short = 'rpi5'
            elif 'Pi 4' in model:
                model_short = 'rpi4'
            elif 'Pi 3' in model:
                model_short = 'rpi3'
            elif 'Pi Zero 2' in model:
                model_short = 'rpizero2'
            elif 'Pi Zero' in model:
                model_short = 'rpizero'
    except FileNotFoundError:
        pass
    
    if model_short and hostname in ('raspberrypi', 'localhost'):
        return f"{model_short}-{hostname}"
    return hostname


class HardwareDetector:
    """
    Detekterar hårdvara, kör benchmark och rekommenderar detekteringsnivå.
    """

    def __init__(self, config_dir=None):
        self.config_dir = Path(config_dir or '/var/lib/pi-camera')
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.benchmark_file = self.config_dir / 'hardware_benchmark.json'
        self.hardware_info = {}
        self.benchmark_results = {}
        self.recommended_level = 1
        self.available_levels = []

    def detect_all(self):
        """Kör fullständig hårdvarudetektering."""
        logger.info("Detecting hardware capabilities...")

        self.hardware_info = {
            'platform': self._detect_platform(),
            'cpu': self._detect_cpu(),
            'memory': self._detect_memory(),
            'gpu': self._detect_gpu(),
            'accelerators': self._detect_accelerators(),
            'storage': self._detect_storage(),
            'software': self._detect_software(),
        }

        # Lägg till GPIO-info baserat på detekterad modell
        model_short = self.hardware_info['platform'].get('device_model_short', '')
        self.hardware_info['gpio'] = get_available_gpio_pins(model_short)
        self.hardware_info['device_name'] = get_device_name()

        # Kör eller ladda benchmark
        if self.benchmark_file.exists():
            try:
                with open(self.benchmark_file) as f:
                    saved = json.load(f)
                # Använd sparad benchmark om den inte är äldre än 7 dagar
                if time.time() - saved.get('timestamp', 0) < 7 * 86400:
                    self.benchmark_results = saved
                    logger.info("Using cached benchmark results")
                else:
                    self._run_benchmark()
            except Exception:
                self._run_benchmark()
        else:
            self._run_benchmark()

        # Bestäm rekommenderad nivå
        self._calculate_recommendation()

        return self.get_full_report()

    # ========================================================================
    # Plattformsdetektering
    # ========================================================================

    def _detect_platform(self):
        """Detektera plattform och enhet."""
        info = {
            'system': platform.system(),
            'machine': platform.machine(),
            'processor': platform.processor(),
            'python_version': platform.python_version(),
            'hostname': platform.node(),
            'device_type': 'unknown',
            'device_model': '',
        }

        # Raspberry Pi-detektering
        try:
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().strip('\x00').strip()
                info['device_model'] = model
                if 'Raspberry Pi' in model:
                    info['device_type'] = 'raspberry_pi'
                    if 'Pi 5' in model:
                        info['device_model_short'] = 'RPi 5'
                    elif 'Pi 4' in model:
                        info['device_model_short'] = 'RPi 4'
                    elif 'Pi 3' in model:
                        info['device_model_short'] = 'RPi 3'
                    elif 'Pi Zero 2' in model:
                        info['device_model_short'] = 'RPi Zero 2'
                    elif 'Pi Zero' in model:
                        info['device_model_short'] = 'RPi Zero'
                    else:
                        info['device_model_short'] = 'RPi'
        except FileNotFoundError:
            pass

        # Orange Pi / andra SBC:er
        if info['device_type'] == 'unknown':
            try:
                with open('/etc/armbian-release', 'r') as f:
                    content = f.read()
                    info['device_type'] = 'armbian_sbc'
                    for line in content.split('\n'):
                        if line.startswith('BOARD_NAME='):
                            info['device_model'] = line.split('=', 1)[1].strip('"')
            except FileNotFoundError:
                pass

        # x86-dator
        if info['machine'] in ('x86_64', 'AMD64', 'i686'):
            info['device_type'] = 'x86_pc'

        # Jetson
        try:
            result = subprocess.run(['cat', '/etc/nv_tegra_release'], capture_output=True, text=True)
            if result.returncode == 0:
                info['device_type'] = 'nvidia_jetson'
        except Exception:
            pass

        return info

    def _detect_cpu(self):
        """Detektera CPU-information."""
        info = {
            'cores': os.cpu_count() or 1,
            'model': '',
            'max_freq_mhz': 0,
            'architecture': platform.machine(),
        }

        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('model name') or line.startswith('Model'):
                        info['model'] = line.split(':', 1)[1].strip()
                    elif line.startswith('Hardware'):
                        if not info['model']:
                            info['model'] = line.split(':', 1)[1].strip()
        except Exception:
            pass

        # Max frekvens
        try:
            with open('/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq', 'r') as f:
                info['max_freq_mhz'] = int(f.read().strip()) // 1000
        except Exception:
            pass

        return info

    def _detect_memory(self):
        """Detektera minne."""
        info = {'total_mb': 0, 'available_mb': 0}
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split()
                    key = parts[0].rstrip(':')
                    val_kb = int(parts[1])
                    if key == 'MemTotal':
                        info['total_mb'] = val_kb // 1024
                    elif key == 'MemAvailable':
                        info['available_mb'] = val_kb // 1024
        except Exception:
            pass
        return info

    def _detect_gpu(self):
        """Detektera GPU/VideoCore."""
        info = {'type': 'none', 'model': ''}

        # VideoCore (Raspberry Pi)
        try:
            result = subprocess.run(['vcgencmd', 'get_mem', 'gpu'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                info['type'] = 'videocore'
                info['model'] = 'Broadcom VideoCore'
                info['gpu_mem'] = result.stdout.strip()
        except Exception:
            pass

        # NVIDIA
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                info['type'] = 'nvidia'
                info['model'] = result.stdout.strip()
        except Exception:
            pass

        return info

    def _detect_accelerators(self):
        """Detektera AI-acceleratorer (Coral, NPU, etc.)."""
        accelerators = []

        # Google Coral USB
        try:
            result = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                if '1a6e:089a' in result.stdout or '18d1:9302' in result.stdout:
                    accelerators.append({
                        'type': 'coral_usb',
                        'name': 'Google Coral USB Accelerator',
                        'available': True,
                    })
        except Exception:
            pass

        # Coral M.2 / PCIe
        try:
            if Path('/dev/apex_0').exists():
                accelerators.append({
                    'type': 'coral_pcie',
                    'name': 'Google Coral Edge TPU (PCIe/M.2)',
                    'available': True,
                })
        except Exception:
            pass

        # Rockchip NPU (Orange Pi 5, etc.)
        try:
            if Path('/dev/rknpu').exists() or Path('/sys/class/devfreq').exists():
                result = subprocess.run(['cat', '/sys/class/devfreq/fdab0000.npu/cur_freq'],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    accelerators.append({
                        'type': 'rockchip_npu',
                        'name': 'Rockchip NPU',
                        'available': True,
                    })
        except Exception:
            pass

        # NVIDIA Jetson (CUDA)
        try:
            import subprocess
            result = subprocess.run(['nvcc', '--version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                accelerators.append({
                    'type': 'nvidia_cuda',
                    'name': 'NVIDIA CUDA',
                    'available': True,
                })
        except Exception:
            pass

        # Kolla om tflite-runtime finns (för Coral)
        try:
            import tflite_runtime
            for acc in accelerators:
                if acc['type'].startswith('coral'):
                    acc['tflite_available'] = True
        except ImportError:
            pass

        return accelerators

    def _detect_storage(self):
        """Detektera lagring."""
        info = {'root_total_gb': 0, 'root_free_gb': 0, 'type': 'unknown'}
        try:
            stat = os.statvfs('/')
            info['root_total_gb'] = round((stat.f_blocks * stat.f_frsize) / (1024 ** 3), 1)
            info['root_free_gb'] = round((stat.f_bavail * stat.f_frsize) / (1024 ** 3), 1)
        except Exception:
            pass

        # Försök detektera lagringstyp
        try:
            result = subprocess.run(['lsblk', '-d', '-o', 'NAME,ROTA', '--noheadings'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 2:
                        if parts[1] == '0':
                            info['type'] = 'ssd'
                        else:
                            info['type'] = 'hdd'
                        break
        except Exception:
            pass

        # SD-kort detektering
        if Path('/sys/block/mmcblk0').exists():
            info['type'] = 'sdcard'

        return info

    def _detect_software(self):
        """Detektera installerad mjukvara."""
        software = {
            'opencv': False,
            'opencv_version': '',
            'numpy': False,
            'pillow': False,
            'tflite': False,
            'ultralytics': False,
            'ffmpeg': False,
        }

        try:
            import cv2
            software['opencv'] = True
            software['opencv_version'] = cv2.__version__
        except ImportError:
            pass

        try:
            import numpy
            software['numpy'] = True
        except ImportError:
            pass

        try:
            from PIL import Image
            software['pillow'] = True
        except ImportError:
            pass

        try:
            import tflite_runtime
            software['tflite'] = True
        except ImportError:
            pass

        try:
            import ultralytics
            software['ultralytics'] = True
        except ImportError:
            pass

        try:
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
            software['ffmpeg'] = result.returncode == 0
        except Exception:
            pass

        return software

    # ========================================================================
    # Benchmark
    # ========================================================================

    def _run_benchmark(self):
        """Kör CPU-benchmark för att mäta prestanda."""
        logger.info("Running hardware benchmark (this takes ~10 seconds)...")

        results = {
            'timestamp': time.time(),
            'cpu_score': 0,
            'frame_diff_fps': 0,
            'jpeg_decode_fps': 0,
            'mobilenet_fps': 0,
            'yolo_fps': 0,
        }

        # Test 1: CPU-score (matrisberäkning)
        try:
            import numpy as np
            start = time.time()
            iterations = 0
            while time.time() - start < 3:
                a = np.random.rand(200, 200)
                b = np.random.rand(200, 200)
                _ = np.dot(a, b)
                iterations += 1
            results['cpu_score'] = int(iterations * 100 / 3)
            logger.info(f"  CPU score: {results['cpu_score']}")
        except ImportError:
            # Fallback utan numpy
            start = time.time()
            iterations = 0
            while time.time() - start < 3:
                _ = sum(i * i for i in range(1000))
                iterations += 1
            results['cpu_score'] = int(iterations / 3)
            logger.info(f"  CPU score (no numpy): {results['cpu_score']}")

        # Test 2: Frame differencing speed
        try:
            import numpy as np
            frame1 = np.random.randint(0, 255, (240, 320), dtype=np.uint8)
            frame2 = np.random.randint(0, 255, (240, 320), dtype=np.uint8)
            start = time.time()
            iterations = 0
            while time.time() - start < 2:
                diff = np.abs(frame1.astype(np.int16) - frame2.astype(np.int16))
                _ = np.sum(diff > 25)
                iterations += 1
            results['frame_diff_fps'] = round(iterations / 2, 1)
            logger.info(f"  Frame diff: {results['frame_diff_fps']} FPS")
        except ImportError:
            pass

        # Test 3: JPEG decode speed
        try:
            from PIL import Image
            import io
            # Skapa en testbild
            img = Image.new('RGB', (640, 480), color=(128, 128, 128))
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=70)
            jpeg_data = buf.getvalue()

            start = time.time()
            iterations = 0
            while time.time() - start < 2:
                img = Image.open(io.BytesIO(jpeg_data))
                _ = img.convert('L')
                iterations += 1
            results['jpeg_decode_fps'] = round(iterations / 2, 1)
            logger.info(f"  JPEG decode: {results['jpeg_decode_fps']} FPS")
        except ImportError:
            pass

        # Test 4: MobileNet inference (om tillgängligt)
        try:
            import cv2
            import numpy as np

            model_dir = Path(__file__).parent / 'models'
            prototxt = model_dir / 'MobileNetSSD_deploy.prototxt'
            caffemodel = model_dir / 'MobileNetSSD_deploy.caffemodel'

            if prototxt.exists() and caffemodel.exists():
                net = cv2.dnn.readNetFromCaffe(str(prototxt), str(caffemodel))
                test_img = np.random.randint(0, 255, (300, 300, 3), dtype=np.uint8)
                blob = cv2.dnn.blobFromImage(test_img, 0.007843, (300, 300), 127.5)

                start = time.time()
                iterations = 0
                while time.time() - start < 3:
                    net.setInput(blob)
                    _ = net.forward()
                    iterations += 1
                results['mobilenet_fps'] = round(iterations / 3, 1)
                logger.info(f"  MobileNet: {results['mobilenet_fps']} FPS")
        except Exception:
            pass

        # Spara resultat
        try:
            with open(self.benchmark_file, 'w') as f:
                json.dump(results, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save benchmark: {e}")

        self.benchmark_results = results
        logger.info(f"Benchmark complete. CPU score: {results['cpu_score']}")

    # ========================================================================
    # Rekommendation
    # ========================================================================

    def _calculate_recommendation(self):
        """Beräkna rekommenderad detekteringsnivå baserat på hårdvara."""
        cpu_score = self.benchmark_results.get('cpu_score', 0)
        ram_mb = self.hardware_info.get('memory', {}).get('total_mb', 0)
        has_accelerator = len(self.hardware_info.get('accelerators', [])) > 0
        has_opencv = self.hardware_info.get('software', {}).get('opencv', False)
        has_ultralytics = self.hardware_info.get('software', {}).get('ultralytics', False)

        self.available_levels = []
        self.recommended_level = 0

        for level_id, level_info in sorted(DETECTION_LEVELS.items()):
            available = True
            reasons = []

            # Kolla CPU-krav
            if cpu_score < level_info['min_cpu_score']:
                available = False
                reasons.append(f"CPU-score {cpu_score} < {level_info['min_cpu_score']}")

            # Kolla RAM-krav
            if ram_mb < level_info['min_ram_mb']:
                available = False
                reasons.append(f"RAM {ram_mb}MB < {level_info['min_ram_mb']}MB")

            # Kolla accelerator-krav
            if level_info.get('requires_accelerator') and not has_accelerator:
                available = False
                reasons.append("Ingen AI-accelerator hittad")

            # Kolla mjukvarukrav
            if level_id >= 3 and not has_opencv:
                available = False
                reasons.append("OpenCV saknas")

            if level_id == 4 and not has_ultralytics:
                available = False
                reasons.append("ultralytics (YOLOv8) saknas")

            level_entry = {
                'level': level_id,
                **level_info,
                'available': available,
                'reasons': reasons,
            }
            self.available_levels.append(level_entry)

            if available:
                self.recommended_level = level_id

        # Om accelerator finns, rekommendera det
        if has_accelerator:
            for entry in self.available_levels:
                if entry['level'] == 5 and entry['available']:
                    self.recommended_level = 5
                    break

        logger.info(f"Recommended detection level: {self.recommended_level} "
                     f"({DETECTION_LEVELS[self.recommended_level]['label']})")

    def get_full_report(self):
        """Returnera fullständig rapport om hårdvara och rekommendation."""
        return {
            'hardware': self.hardware_info,
            'benchmark': self.benchmark_results,
            'detection_levels': self.available_levels,
            'recommended_level': self.recommended_level,
            'recommended_level_name': DETECTION_LEVELS[self.recommended_level]['name'],
            'recommended_level_label': DETECTION_LEVELS[self.recommended_level]['label'],
        }

    def get_summary(self):
        """Kort sammanfattning för visning."""
        hw = self.hardware_info
        platform_info = hw.get('platform', {})
        cpu = hw.get('cpu', {})
        mem = hw.get('memory', {})
        accel = hw.get('accelerators', [])

        device = platform_info.get('device_model', platform_info.get('device_type', 'Unknown'))
        cpu_model = cpu.get('model', 'Unknown')
        cores = cpu.get('cores', '?')
        ram = mem.get('total_mb', 0)
        cpu_score = self.benchmark_results.get('cpu_score', 0)

        accel_names = [a['name'] for a in accel] if accel else ['Ingen']

        gpio_info = hw.get('gpio', {})
        device_name = hw.get('device_name', 'unknown')

        return {
            'device': device,
            'device_name': device_name,
            'cpu': f"{cpu_model} ({cores} kärnor)",
            'ram_mb': ram,
            'cpu_score': cpu_score,
            'accelerators': accel_names,
            'gpio_pin_count': gpio_info.get('pin_count', 0),
            'gpio_library': gpio_info.get('recommended_library', 'gpiozero'),
            'recommended_level': self.recommended_level,
            'recommended_label': DETECTION_LEVELS[self.recommended_level]['label'],
            'available_levels': [
                {'level': l['level'], 'name': l['name'], 'label': l['label'], 'available': l['available']}
                for l in self.available_levels
            ],
        }


def auto_configure_detection(config_dir=None):
    """
    Convenience-funktion: kör detektering och returnera rekommenderad konfiguration.
    """
    detector = HardwareDetector(config_dir=config_dir)
    report = detector.detect_all()

    level = report['recommended_level']
    level_name = report['recommended_level_name']

    # Bygg konfiguration baserat på nivå
    detection_config = {
        'auto_detected': True,
        'detection_level': level,
        'detection_level_name': level_name,
        'hardware_summary': detector.get_summary(),
    }

    if level == 0:
        detection_config['motion'] = {'enabled': False}
        detection_config['object_detection'] = {'enabled': False}

    elif level == 1:
        detection_config['motion'] = {
            'enabled': True,
            'sensitivity': 50,
            'min_area_percent': 1.5,
            'min_frames': 3,
            'cooldown': 5,
        }
        detection_config['object_detection'] = {'enabled': False}

    elif level == 2:
        detection_config['motion'] = {
            'enabled': True,
            'sensitivity': 60,
            'min_area_percent': 1.0,
            'min_frames': 2,
            'cooldown': 3,
        }
        detection_config['object_detection'] = {'enabled': False}

    elif level == 3:
        detection_config['motion'] = {
            'enabled': True,
            'sensitivity': 50,
            'min_area_percent': 1.0,
            'min_frames': 2,
            'cooldown': 3,
        }
        detection_config['object_detection'] = {
            'enabled': True,
            'backend': 'mobilenet',
            'confidence': 0.5,
            'classes': ['person', 'car'],
            'interval': 3,  # Var 3:e sekund (spara CPU)
        }

    elif level == 4:
        detection_config['motion'] = {
            'enabled': True,
            'sensitivity': 50,
            'min_area_percent': 1.0,
            'min_frames': 2,
            'cooldown': 3,
        }
        detection_config['object_detection'] = {
            'enabled': True,
            'backend': 'yolov8',
            'confidence': 0.4,
            'classes': ['person', 'car', 'dog', 'cat'],
            'interval': 2,
        }

    elif level == 5:
        detection_config['motion'] = {
            'enabled': True,
            'sensitivity': 50,
            'min_area_percent': 1.0,
            'min_frames': 2,
            'cooldown': 3,
        }
        detection_config['object_detection'] = {
            'enabled': True,
            'backend': 'coral',
            'confidence': 0.4,
            'classes': ['person', 'car', 'dog', 'cat'],
            'interval': 1,  # Coral är snabb nog för varje sekund
        }

    return detection_config


if __name__ == '__main__':
    """Kör som standalone för att testa hårdvarudetektering."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    print("\n" + "=" * 60)
    print("  Hardware Detection & Benchmark")
    print("=" * 60 + "\n")

    config = auto_configure_detection()

    hw = config['hardware_summary']
    print(f"\nDevice name: {hw.get('device_name', 'unknown')}")
    print(f"Device: {hw['device']}")
    print(f"CPU: {hw['cpu']}")
    print(f"RAM: {hw['ram_mb']} MB")
    print(f"CPU Score: {hw['cpu_score']}")
    print(f"Accelerators: {', '.join(hw['accelerators'])}")
    print(f"GPIO pins: {hw.get('gpio_pin_count', '?')} available")
    print(f"GPIO library: {hw.get('gpio_library', '?')}")

    print(f"\nRecommended level: {config['detection_level']} - {config['detection_level_name']}")
    print(f"  ({hw['recommended_label']})")

    print("\nAvailable levels:")
    for l in hw['available_levels']:
        status = "✓" if l['available'] else "✗"
        print(f"  {status} Level {l['level']}: {l['label']}")

    # Visa GPIO pin-map
    gpio = get_available_gpio_pins()
    print(f"\nGPIO Pin-map ({gpio['pin_count']} pins):")
    print(f"  {'BCM':>4}  {'Phys':>4}  {'Name':<12}  Notes")
    print(f"  {'----':>4}  {'----':>4}  {'----':<12}  -----")
    for p in gpio['pins']:
        notes = p['notes'] or ''
        print(f"  {p['bcm']:>4}  {p['physical']:>4}  {p['name']:<12}  {notes}")

    print(f"\nMotion detection: {'Enabled' if config.get('motion', {}).get('enabled') else 'Disabled'}")
    print(f"Object detection: {'Enabled' if config.get('object_detection', {}).get('enabled') else 'Disabled'}")
    if config.get('object_detection', {}).get('enabled'):
        print(f"  Backend: {config['object_detection']['backend']}")
