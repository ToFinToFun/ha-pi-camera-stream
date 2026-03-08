"""
Motion Detector Module
======================

Jämför konsekutiva frames för att detektera rörelse.
Stöder konfigurerbara tröskelvärden och zoner.
Skickar motion_start/motion_end events till relay-servern.
"""

import logging
import time
import numpy as np
from collections import deque

logger = logging.getLogger('motion')


class MotionDetector:
    """
    Detekterar rörelse genom att jämföra JPEG-frames.
    Använder enkel frame-differencing med konfigurerbar känslighet.
    """

    def __init__(self, config=None):
        config = config or {}

        # Känslighet: 0-100 (högre = mer känslig)
        self.sensitivity = config.get('sensitivity', 50)

        # Minsta area (% av bilden) som måste ändras för att räknas som rörelse
        self.min_area_percent = config.get('min_area_percent', 1.0)

        # Antal frames som måste ha rörelse i rad för att trigga
        self.min_frames = config.get('min_frames', 2)

        # Cooldown efter motion_end innan ny motion_start kan triggas (sekunder)
        self.cooldown = config.get('cooldown', 5)

        # Zoner att övervaka (None = hela bilden)
        # Format: [{"x": 0, "y": 0, "w": 100, "h": 100}] (procent av bilden)
        self.zones = config.get('zones', None)

        # State
        self.prev_gray = None
        self.motion_active = False
        self.motion_frame_count = 0
        self.last_motion_end = 0
        self.motion_start_time = 0
        self.history = deque(maxlen=60)  # Senaste 60 sekunderna av rörelsedata

        # Threshold baserat på känslighet
        self._threshold = max(5, int(50 - self.sensitivity * 0.45))
        self._min_area_ratio = self.min_area_percent / 100.0

        logger.info(
            f"Motion detector initialized: sensitivity={self.sensitivity}, "
            f"threshold={self._threshold}, min_area={self.min_area_percent}%"
        )

    def analyze_frame(self, jpeg_data):
        """
        Analysera en JPEG-frame för rörelse.

        Returnerar:
            dict med:
                motion: bool - Om rörelse detekterades
                intensity: float - Rörelseintensitet (0-100)
                changed_area: float - Andel av bilden som ändrats (0-1)
                event: str|None - 'start', 'end', eller None
        """
        try:
            gray = self._jpeg_to_gray(jpeg_data)
        except Exception as e:
            logger.warning(f"Failed to decode frame: {e}")
            return {'motion': False, 'intensity': 0, 'changed_area': 0, 'event': None}

        if self.prev_gray is None:
            self.prev_gray = gray
            return {'motion': False, 'intensity': 0, 'changed_area': 0, 'event': None}

        # Beräkna skillnad
        diff = np.abs(gray.astype(np.int16) - self.prev_gray.astype(np.int16)).astype(np.uint8)
        self.prev_gray = gray

        # Applicera zoner om konfigurerade
        if self.zones:
            mask = np.zeros_like(diff)
            h, w = diff.shape
            for zone in self.zones:
                x1 = int(zone['x'] / 100 * w)
                y1 = int(zone['y'] / 100 * h)
                x2 = int((zone['x'] + zone['w']) / 100 * w)
                y2 = int((zone['y'] + zone['h']) / 100 * h)
                mask[y1:y2, x1:x2] = 1
            diff = diff * mask

        # Räkna pixlar som ändrats mer än threshold
        motion_pixels = np.sum(diff > self._threshold)
        total_pixels = diff.size
        changed_area = motion_pixels / total_pixels
        intensity = min(100, changed_area * 1000)

        motion_detected = changed_area > self._min_area_ratio

        # Spara i historik
        self.history.append({
            'time': time.time(),
            'intensity': intensity,
            'motion': motion_detected,
        })

        # Bestäm event
        event = None
        now = time.time()

        if motion_detected:
            self.motion_frame_count += 1

            if not self.motion_active and self.motion_frame_count >= self.min_frames:
                if now - self.last_motion_end > self.cooldown:
                    self.motion_active = True
                    self.motion_start_time = now
                    event = 'start'
                    logger.info(f"Motion started (intensity: {intensity:.1f}%)")
        else:
            if self.motion_active:
                self.motion_active = False
                self.last_motion_end = now
                duration = now - self.motion_start_time
                event = 'end'
                logger.info(f"Motion ended (duration: {duration:.1f}s)")

            self.motion_frame_count = 0

        return {
            'motion': motion_detected,
            'intensity': round(intensity, 2),
            'changed_area': round(changed_area, 4),
            'event': event,
        }

    def _jpeg_to_gray(self, jpeg_data):
        """Konvertera JPEG-data till gråskalebild (numpy array)."""
        try:
            # Försök med OpenCV (snabbast)
            import cv2
            nparr = np.frombuffer(jpeg_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                # Skala ner för snabbare beräkning
                h, w = img.shape
                if w > 320:
                    scale = 320 / w
                    img = cv2.resize(img, (320, int(h * scale)))
                return img
        except ImportError:
            pass

        # Fallback: Pillow
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(jpeg_data)).convert('L')
        # Skala ner
        if img.width > 320:
            scale = 320 / img.width
            img = img.resize((320, int(img.height * scale)))
        return np.array(img)

    def get_stats(self):
        """Returnera statistik om rörelsedetektering."""
        if not self.history:
            return {'motion_active': self.motion_active, 'avg_intensity': 0}

        recent = [h for h in self.history if time.time() - h['time'] < 60]
        avg_intensity = sum(h['intensity'] for h in recent) / len(recent) if recent else 0
        motion_percent = sum(1 for h in recent if h['motion']) / len(recent) * 100 if recent else 0

        return {
            'motion_active': self.motion_active,
            'avg_intensity': round(avg_intensity, 2),
            'motion_percent': round(motion_percent, 1),
        }
