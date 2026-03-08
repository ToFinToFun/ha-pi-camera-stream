"""
Notifications Module (v2)
=========================

Skickar push-notiser vid rörelsedetektering, GPIO-events eller andra händelser.
Stöder:
- Telegram Bot API
- Pushover
- Webhook (generisk HTTP POST)
- Home Assistant Companion App (via HA REST API)

Nyheter i v2:
- Per-kamera notis-konfiguration (vilka kameror ska larma?)
- GPIO-event notiser
- Scen-aktivering notiser
- Tidsschema (tysta notiser nattetid etc.)
- Fjärrstyrning av notis-inställningar från viewer-appen
"""

import logging
import time
import threading
import io
import json
from typing import Dict, List, Optional, Set

logger = logging.getLogger('notifications')


class NotificationManager:
    """Hanterar push-notiser med per-kamera konfiguration och rate limiting."""

    def __init__(self, config=None):
        config = config or {}
        self.enabled = config.get('enabled', False)
        self.providers: List = []
        self.cooldown = config.get('cooldown', 60)
        self.last_notification: Dict[str, float] = {}
        self.include_snapshot = config.get('include_snapshot', True)

        # Per-kamera inställningar
        # camera_id -> { 'motion': True, 'gpio': True, 'objects': ['person', 'car'] }
        self.camera_settings: Dict[str, dict] = {}

        # Globala inställningar
        self.notify_motion = config.get('notify_motion', True)
        self.notify_gpio = config.get('notify_gpio', True)
        self.notify_offline = config.get('notify_offline', True)
        self.notify_objects_only = config.get('notify_objects_only', [])  # Tom = alla

        # Tidsschema: tysta perioder
        self.quiet_hours = config.get('quiet_hours', None)
        # Format: { 'start': '23:00', 'end': '06:00', 'allow_priority': True }

        # Läs per-kamera config
        cam_configs = config.get('cameras', {})
        for cam_id, cam_conf in cam_configs.items():
            self.camera_settings[cam_id] = {
                'motion': cam_conf.get('motion', True),
                'gpio': cam_conf.get('gpio', True),
                'objects': cam_conf.get('objects', []),
                'cooldown': cam_conf.get('cooldown', self.cooldown),
                'enabled': cam_conf.get('enabled', True),
            }

        if not self.enabled:
            return

        # Konfigurera providers
        if config.get('telegram'):
            self.providers.append(TelegramProvider(config['telegram']))
            logger.info("Telegram notifications enabled")

        if config.get('pushover'):
            self.providers.append(PushoverProvider(config['pushover']))
            logger.info("Pushover notifications enabled")

        if config.get('webhook'):
            self.providers.append(WebhookProvider(config['webhook']))
            logger.info("Webhook notifications enabled")

        if config.get('ha_notify'):
            self.providers.append(HANotifyProvider(config['ha_notify']))
            logger.info("Home Assistant notifications enabled")

        if not self.providers:
            logger.warning("Notifications enabled but no providers configured")
            self.enabled = False

    def _is_quiet_hours(self) -> bool:
        """Kontrollera om vi är i tysta timmar."""
        if not self.quiet_hours:
            return False

        now = time.strftime('%H:%M')
        start = self.quiet_hours.get('start', '23:00')
        end = self.quiet_hours.get('end', '06:00')

        if start <= end:
            return start <= now <= end
        else:
            # Över midnatt (t.ex. 23:00 - 06:00)
            return now >= start or now <= end

    def _should_notify_camera(self, camera_id: str, event_type: str,
                               objects: list = None, priority: bool = False) -> bool:
        """Kontrollera om notis ska skickas för denna kamera och event."""
        if not self.enabled:
            return False

        # Tysta timmar (om inte priority)
        if self._is_quiet_hours() and not priority:
            if not self.quiet_hours.get('allow_priority', True):
                return False

        # Per-kamera inställningar
        cam_settings = self.camera_settings.get(camera_id, {})
        if not cam_settings.get('enabled', True):
            return False

        if event_type == 'motion':
            if not cam_settings.get('motion', self.notify_motion):
                return False

            # Kolla objektfilter
            required_objects = cam_settings.get('objects', self.notify_objects_only)
            if required_objects and objects:
                # Bara notifiera om rätt typ av objekt detekterades
                if not any(obj in required_objects for obj in objects):
                    return False
            elif required_objects and not objects:
                # Kräver specifika objekt men inga detekterades
                return False

        elif event_type == 'gpio':
            if not cam_settings.get('gpio', self.notify_gpio):
                return False

        elif event_type == 'offline':
            if not self.notify_offline:
                return False

        # Rate limiting
        cooldown = cam_settings.get('cooldown', self.cooldown)
        now = time.time()
        key = f"{camera_id}:{event_type}"
        last = self.last_notification.get(key, 0)
        if now - last < cooldown:
            return False

        self.last_notification[key] = now
        return True

    # ── Notis-metoder ────────────────────────────────────────────────

    def notify_motion_event(self, camera_id: str, camera_name: str,
                            objects: list = None, snapshot_data: bytes = None):
        """Skicka notis om rörelsedetektering."""
        if not self._should_notify_camera(camera_id, 'motion', objects):
            return

        title = "Rörelse detekterad"
        message = f"Kamera: {camera_name}"
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        if objects:
            obj_str = ', '.join(objects)
            message += f"\nObjekt: {obj_str}"
            title = f"{obj_str.title()} detekterad"

        message += f"\nTid: {timestamp}"

        self._send_async(title, message,
                         snapshot_data if self.include_snapshot else None,
                         priority='high' if objects else 'normal')

    def notify_gpio_event(self, client_name: str, pin_name: str,
                          state: bool, snapshot_data: bytes = None):
        """Skicka notis om GPIO-event."""
        if not self.enabled or not self.notify_gpio:
            return

        # GPIO-events har alltid hög prioritet (t.ex. dörrsensor)
        now = time.time()
        key = f"gpio:{pin_name}"
        last = self.last_notification.get(key, 0)
        if now - last < 30:  # 30s cooldown för GPIO
            return
        self.last_notification[key] = now

        state_str = "AKTIVERAD" if state else "AVAKTIVERAD"
        title = f"GPIO: {pin_name} {state_str}"
        message = f"Sensor '{pin_name}' på {client_name} ändrades till {state_str}"
        message += f"\nTid: {time.strftime('%Y-%m-%d %H:%M:%S')}"

        self._send_async(title, message, snapshot_data, priority='high')

    def notify_scene_activated(self, client_name: str, scene_name: str, scene_label: str):
        """Skicka notis om scen aktiverades."""
        title = f"Scen aktiverad: {scene_label}"
        message = f"Scenen '{scene_label}' aktiverades på {client_name}"
        message += f"\nTid: {time.strftime('%Y-%m-%d %H:%M:%S')}"

        self._send_async(title, message, priority='normal')

    def notify_camera_offline(self, camera_id: str, camera_name: str):
        """Skicka notis om kamera gick offline."""
        if not self._should_notify_camera(camera_id, 'offline'):
            return

        title = "Kamera offline"
        message = f"Kamera '{camera_name}' ({camera_id}) har tappat anslutningen"
        message += f"\nTid: {time.strftime('%Y-%m-%d %H:%M:%S')}"

        self._send_async(title, message, priority='high')

    # ── Fjärrstyrning ────────────────────────────────────────────────

    def update_camera_settings(self, camera_id: str, settings: dict):
        """Uppdatera notis-inställningar för en specifik kamera (från viewer-appen)."""
        if camera_id not in self.camera_settings:
            self.camera_settings[camera_id] = {}

        cam = self.camera_settings[camera_id]
        if 'motion' in settings:
            cam['motion'] = settings['motion']
        if 'gpio' in settings:
            cam['gpio'] = settings['gpio']
        if 'objects' in settings:
            cam['objects'] = settings['objects']
        if 'cooldown' in settings:
            cam['cooldown'] = settings['cooldown']
        if 'enabled' in settings:
            cam['enabled'] = settings['enabled']

        logger.info(f"Notification settings updated for {camera_id}: {cam}")

    def get_camera_settings(self, camera_id: str) -> dict:
        """Hämta notis-inställningar för en kamera."""
        defaults = {
            'motion': self.notify_motion,
            'gpio': self.notify_gpio,
            'objects': self.notify_objects_only,
            'cooldown': self.cooldown,
            'enabled': True,
        }
        cam = self.camera_settings.get(camera_id, {})
        return {**defaults, **cam}

    def get_all_settings(self) -> dict:
        """Hämta alla notis-inställningar."""
        return {
            'enabled': self.enabled,
            'providers': [p.name for p in self.providers],
            'global': {
                'cooldown': self.cooldown,
                'notify_motion': self.notify_motion,
                'notify_gpio': self.notify_gpio,
                'notify_offline': self.notify_offline,
                'notify_objects_only': self.notify_objects_only,
                'include_snapshot': self.include_snapshot,
                'quiet_hours': self.quiet_hours,
            },
            'cameras': {
                cam_id: settings
                for cam_id, settings in self.camera_settings.items()
            },
        }

    # ── Intern ───────────────────────────────────────────────────────

    def _send_async(self, title: str, message: str,
                    image_data: bytes = None, priority: str = 'normal'):
        """Skicka notis i bakgrundstråd."""
        thread = threading.Thread(
            target=self._send_all,
            args=(title, message, image_data, priority),
            daemon=True,
        )
        thread.start()

    def _send_all(self, title: str, message: str,
                  image_data: bytes = None, priority: str = 'normal'):
        """Skicka via alla konfigurerade providers."""
        for provider in self.providers:
            try:
                provider.send(title, message, image_data, priority)
            except Exception as e:
                logger.error(f"Notification error ({provider.name}): {e}")


# ── Providers ────────────────────────────────────────────────────────

class TelegramProvider:
    """Skickar notiser via Telegram Bot API."""

    name = 'telegram'

    def __init__(self, config):
        self.bot_token = config['bot_token']
        self.chat_id = config['chat_id']
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send(self, title, message, image_data=None, priority='normal'):
        import requests

        text = f"*{title}*\n{message}"

        if image_data and len(image_data) > 0:
            url = f"{self.api_url}/sendPhoto"
            files = {'photo': ('snapshot.jpg', io.BytesIO(image_data), 'image/jpeg')}
            data = {'chat_id': self.chat_id, 'caption': text, 'parse_mode': 'Markdown'}
            resp = requests.post(url, data=data, files=files, timeout=30)
        else:
            url = f"{self.api_url}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': text, 'parse_mode': 'Markdown'}
            resp = requests.post(url, json=data, timeout=30)

        if resp.status_code != 200:
            raise RuntimeError(f"Telegram API error: {resp.status_code} {resp.text}")

        logger.info(f"Telegram notification sent to {self.chat_id}")


class PushoverProvider:
    """Skickar notiser via Pushover."""

    name = 'pushover'

    def __init__(self, config):
        self.user_key = config['user_key']
        self.app_token = config['app_token']

    def send(self, title, message, image_data=None, priority='normal'):
        import requests

        prio_map = {'low': -1, 'normal': 0, 'high': 1}

        url = "https://api.pushover.net/1/messages.json"
        data = {
            'token': self.app_token,
            'user': self.user_key,
            'title': title,
            'message': message,
            'priority': prio_map.get(priority, 0),
        }

        if priority == 'high':
            data['sound'] = 'siren'

        files = {}
        if image_data:
            files['attachment'] = ('snapshot.jpg', io.BytesIO(image_data), 'image/jpeg')

        resp = requests.post(url, data=data, files=files if files else None, timeout=30)

        if resp.status_code != 200:
            raise RuntimeError(f"Pushover API error: {resp.status_code}")

        logger.info("Pushover notification sent")


class WebhookProvider:
    """Skickar notiser via generisk HTTP webhook."""

    name = 'webhook'

    def __init__(self, config):
        self.url = config['url']
        self.headers = config.get('headers', {})
        self.method = config.get('method', 'POST').upper()

    def send(self, title, message, image_data=None, priority='normal'):
        import requests
        import base64

        payload = {
            'title': title,
            'message': message,
            'priority': priority,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }

        if image_data:
            payload['snapshot_base64'] = base64.b64encode(image_data).decode('utf-8')

        resp = requests.request(
            self.method,
            self.url,
            json=payload,
            headers=self.headers,
            timeout=30,
        )

        logger.info(f"Webhook notification sent ({resp.status_code})")


class HANotifyProvider:
    """Skickar notiser via Home Assistant REST API (Companion App)."""

    name = 'ha_notify'

    def __init__(self, config):
        self.ha_url = config['url'].rstrip('/')  # t.ex. http://homeassistant.local:8123
        self.token = config['token']  # Long-lived access token
        self.service = config.get('service', 'notify.mobile_app')  # t.ex. notify.mobile_app_telefon

    def send(self, title, message, image_data=None, priority='normal'):
        import requests
        import base64

        url = f"{self.ha_url}/api/services/{self.service.replace('.', '/')}"
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

        payload = {
            'title': title,
            'message': message,
        }

        # HA Companion App stöder bilder via URL eller base64
        if image_data:
            payload['data'] = {
                'image': f"data:image/jpeg;base64,{base64.b64encode(image_data).decode('utf-8')}",
                'importance': 'high' if priority == 'high' else 'default',
            }

        resp = requests.post(url, json=payload, headers=headers, timeout=30)

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"HA Notify error: {resp.status_code} {resp.text}")

        logger.info(f"HA notification sent via {self.service}")
