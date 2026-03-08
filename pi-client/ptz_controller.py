"""
PTZ Controller Module
=====================

Styr Pan/Tilt/Zoom på nätverkskameror.
Stöder:
- Axis VAPIX PTZ API
- Generisk ONVIF PTZ (via python-onvif-zeep)

Kommandon:
- move: Flytta kameran i en riktning (pan/tilt)
- zoom: Zooma in/ut
- preset: Gå till en sparad position
- home: Gå till hemposition
- stop: Stoppa pågående rörelse
"""

import logging
import time

logger = logging.getLogger('ptz')


class PTZController:
    """Basklass för PTZ-styrning."""

    def __init__(self, config=None):
        self.config = config or {}
        self.enabled = False

    def move(self, pan=0, tilt=0, speed=50):
        raise NotImplementedError

    def zoom(self, direction='in', speed=50):
        raise NotImplementedError

    def go_to_preset(self, preset_name):
        raise NotImplementedError

    def go_home(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def get_presets(self):
        return []

    def get_position(self):
        return {'pan': 0, 'tilt': 0, 'zoom': 0}


class AxisPTZController(PTZController):
    """PTZ-styrning för Axis-kameror via VAPIX API."""

    def __init__(self, host, username='root', password='', port=80,
                 use_https=False, camera_number=1, **kwargs):
        super().__init__(kwargs)
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.camera_number = camera_number
        self.session = None

        protocol = 'https' if use_https else 'http'
        self.base_url = f"{protocol}://{host}:{port}"

    def start(self):
        import requests
        from requests.auth import HTTPDigestAuth

        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(self.username, self.password)
        self.session.verify = False

        # Testa PTZ-stöd
        try:
            resp = self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi?query=position&camera={self.camera_number}",
                timeout=10,
            )
            if resp.status_code == 200:
                self.enabled = True
                logger.info(f"Axis PTZ enabled for {self.host}")
            else:
                logger.warning(f"Axis PTZ not available (HTTP {resp.status_code})")
        except Exception as e:
            logger.warning(f"Axis PTZ check failed: {e}")

    def move(self, pan=0, tilt=0, speed=50):
        """Flytta kameran. pan/tilt: -100 till 100."""
        if not self.enabled:
            return

        # Axis VAPIX: rpan/rtilt är relativ rörelse
        params = {
            'camera': self.camera_number,
            'rpan': int(pan),
            'rtilt': int(tilt),
            'speed': int(speed),
        }

        try:
            self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params=params,
                timeout=5,
            )
        except Exception as e:
            logger.error(f"PTZ move error: {e}")

    def zoom(self, direction='in', speed=50):
        """Zooma in eller ut."""
        if not self.enabled:
            return

        rzoom = int(speed) if direction == 'in' else -int(speed)
        params = {
            'camera': self.camera_number,
            'rzoom': rzoom,
        }

        try:
            self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params=params,
                timeout=5,
            )
        except Exception as e:
            logger.error(f"PTZ zoom error: {e}")

    def go_to_preset(self, preset_name):
        """Gå till en sparad preset-position."""
        if not self.enabled:
            return

        params = {
            'camera': self.camera_number,
            'gotoserverpresetname': preset_name,
        }

        try:
            self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params=params,
                timeout=5,
            )
            logger.info(f"PTZ goto preset: {preset_name}")
        except Exception as e:
            logger.error(f"PTZ preset error: {e}")

    def go_home(self):
        """Gå till hemposition."""
        if not self.enabled:
            return

        params = {
            'camera': self.camera_number,
            'move': 'home',
        }

        try:
            self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params=params,
                timeout=5,
            )
        except Exception as e:
            logger.error(f"PTZ home error: {e}")

    def stop(self):
        """Stoppa pågående rörelse."""
        if not self.enabled:
            return

        params = {
            'camera': self.camera_number,
            'move': 'stop',
        }

        try:
            self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params=params,
                timeout=5,
            )
        except Exception as e:
            logger.error(f"PTZ stop error: {e}")

    def get_presets(self):
        """Hämta lista med sparade presets."""
        if not self.enabled:
            return []

        try:
            resp = self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params={'query': 'presetposall', 'camera': self.camera_number},
                timeout=5,
            )
            if resp.status_code == 200:
                presets = []
                for line in resp.text.strip().split('\n'):
                    if '=' in line:
                        name = line.split('=')[0].replace('presetposno', '').strip()
                        presets.append(name)
                return presets
        except Exception as e:
            logger.error(f"PTZ get presets error: {e}")
        return []

    def get_position(self):
        """Hämta nuvarande position."""
        if not self.enabled:
            return {'pan': 0, 'tilt': 0, 'zoom': 0}

        try:
            resp = self.session.get(
                f"{self.base_url}/axis-cgi/com/ptz.cgi",
                params={'query': 'position', 'camera': self.camera_number},
                timeout=5,
            )
            if resp.status_code == 200:
                pos = {}
                for line in resp.text.strip().split('\n'):
                    if '=' in line:
                        key, val = line.split('=', 1)
                        pos[key.strip().lower()] = float(val.strip())
                return pos
        except Exception as e:
            logger.error(f"PTZ get position error: {e}")
        return {'pan': 0, 'tilt': 0, 'zoom': 0}


class DummyPTZController(PTZController):
    """Dummy PTZ för kameror utan PTZ-stöd."""

    def __init__(self, **kwargs):
        super().__init__()
        self.enabled = False

    def move(self, pan=0, tilt=0, speed=50):
        pass

    def zoom(self, direction='in', speed=50):
        pass

    def go_to_preset(self, preset_name):
        pass

    def go_home(self):
        pass

    def stop(self):
        pass
