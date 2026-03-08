#!/usr/bin/env python3
"""
GPIO Controller - Hanterar digitala in/utgångar på Raspberry Pi.

Stöder:
- Digitala ingångar: Dörrsensorer, PIR-rörelsesensorer, larmknappar, etc.
- Digitala utgångar: Reläer för belysning, sirener, värme, lås, etc.
- Debouncing av ingångar
- Event-callbacks vid tillståndsändringar
- Konfiguration via YAML

Kräver: RPi.GPIO eller gpiozero (installeras automatiskt på Raspberry Pi OS)
Fallback: Simuleringsläge om GPIO inte finns (t.ex. på x86-maskiner)
"""

import asyncio
import logging
import time
import threading
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)

# ── Försök importera GPIO-bibliotek ──────────────────────────────────

GPIO_AVAILABLE = False
GPIO_LIB = None

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    GPIO_LIB = 'RPi.GPIO'
    logger.info("GPIO: Använder RPi.GPIO")
except ImportError:
    try:
        from gpiozero import Device, LED, Button
        GPIO_AVAILABLE = True
        GPIO_LIB = 'gpiozero'
        logger.info("GPIO: Använder gpiozero")
    except ImportError:
        logger.warning("GPIO: Inget GPIO-bibliotek tillgängligt. Kör i simuleringsläge.")


# ── GPIO Pin-klass ───────────────────────────────────────────────────

class GPIOPin:
    """Representerar en enda GPIO-pin med konfiguration."""

    def __init__(self, pin_number: int, name: str, direction: str,
                 active_low: bool = False, pull_up: bool = True,
                 debounce_ms: int = 200, default_state: bool = False):
        self.pin_number = pin_number
        self.name = name
        self.direction = direction  # 'input' eller 'output'
        self.active_low = active_low
        self.pull_up = pull_up
        self.debounce_ms = debounce_ms
        self.default_state = default_state
        self.state = default_state
        self.last_change = 0
        self.change_count = 0
        self._callbacks: List[Callable] = []

    def add_callback(self, callback: Callable):
        """Lägg till en callback som anropas vid tillståndsändring."""
        self._callbacks.append(callback)

    def notify(self, new_state: bool):
        """Notifiera alla callbacks om tillståndsändring."""
        old_state = self.state
        self.state = new_state
        self.last_change = time.time()
        self.change_count += 1
        for cb in self._callbacks:
            try:
                cb(self, old_state, new_state)
            except Exception as e:
                logger.error(f"GPIO callback error on pin {self.name}: {e}")

    def to_dict(self) -> dict:
        """Returnera pin-status som dictionary."""
        return {
            'pin': self.pin_number,
            'name': self.name,
            'direction': self.direction,
            'state': self.state,
            'active_low': self.active_low,
            'last_change': self.last_change,
            'change_count': self.change_count,
        }


# ── GPIO Controller ─────────────────────────────────────────────────

class GPIOController:
    """Huvudklass för GPIO-hantering."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.pins: Dict[str, GPIOPin] = {}
        self.running = False
        self._poll_thread = None
        self._event_callback = None  # Extern callback för events
        self._simulated_states: Dict[int, bool] = {}  # För simuleringsläge

        # Scener
        self.scenes: Dict[str, dict] = {}

        if config:
            self._setup_from_config(config)

    def _setup_from_config(self, config: dict):
        """Konfigurera GPIO från YAML-konfiguration."""

        # Konfigurera ingångar
        inputs = config.get('inputs', [])
        for inp in inputs:
            pin = GPIOPin(
                pin_number=inp['pin'],
                name=inp['name'],
                direction='input',
                active_low=inp.get('active_low', False),
                pull_up=inp.get('pull_up', True),
                debounce_ms=inp.get('debounce_ms', 200),
            )
            self.pins[inp['name']] = pin
            logger.info(f"GPIO Input: {inp['name']} on pin {inp['pin']}")

        # Konfigurera utgångar
        outputs = config.get('outputs', [])
        for out in outputs:
            pin = GPIOPin(
                pin_number=out['pin'],
                name=out['name'],
                direction='output',
                active_low=out.get('active_low', False),
                default_state=out.get('default_state', False),
            )
            self.pins[out['name']] = pin
            logger.info(f"GPIO Output: {out['name']} on pin {out['pin']}")

        # Konfigurera scener
        scenes_config = config.get('scenes', [])
        for scene in scenes_config:
            self.scenes[scene['name']] = {
                'name': scene['name'],
                'label': scene.get('label', scene['name']),
                'icon': scene.get('icon', 'toggle'),
                'actions': scene.get('actions', []),
                'description': scene.get('description', ''),
            }
            logger.info(f"GPIO Scene: {scene['name']} ({len(scene.get('actions', []))} actions)")

    def set_event_callback(self, callback: Callable):
        """Sätt extern callback för GPIO-events (skickas till servern)."""
        self._event_callback = callback

    def start(self):
        """Starta GPIO-kontrollern."""
        if self.running:
            return

        self.running = True

        if GPIO_AVAILABLE and GPIO_LIB == 'RPi.GPIO':
            self._setup_rpi_gpio()
        elif GPIO_AVAILABLE and GPIO_LIB == 'gpiozero':
            self._setup_gpiozero()
        else:
            logger.info("GPIO: Simuleringsläge aktivt")
            self._setup_simulated()

        # Starta polling-tråd för att läsa ingångar
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        logger.info(f"GPIO Controller startad: {len(self.pins)} pins konfigurerade")

    def stop(self):
        """Stoppa GPIO-kontrollern."""
        self.running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2)

        if GPIO_AVAILABLE and GPIO_LIB == 'RPi.GPIO':
            try:
                import RPi.GPIO as GPIO
                GPIO.cleanup()
            except Exception:
                pass

        logger.info("GPIO Controller stoppad")

    # ── RPi.GPIO setup ───────────────────────────────────────────────

    def _setup_rpi_gpio(self):
        """Konfigurera med RPi.GPIO-biblioteket."""
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        for name, pin in self.pins.items():
            if pin.direction == 'input':
                pull = GPIO.PUD_UP if pin.pull_up else GPIO.PUD_DOWN
                GPIO.setup(pin.pin_number, GPIO.IN, pull_up_down=pull)

                # Läs initialt tillstånd
                raw = GPIO.input(pin.pin_number)
                pin.state = (not raw) if pin.active_low else bool(raw)

                # Sätt upp interrupt-baserad detektering
                edge = GPIO.BOTH
                GPIO.add_event_detect(
                    pin.pin_number, edge,
                    callback=lambda ch, p=pin: self._rpi_gpio_callback(p, ch),
                    bouncetime=pin.debounce_ms
                )

            elif pin.direction == 'output':
                GPIO.setup(pin.pin_number, GPIO.OUT)
                # Sätt default-tillstånd
                out_val = (not pin.default_state) if pin.active_low else pin.default_state
                GPIO.output(pin.pin_number, out_val)
                pin.state = pin.default_state

    def _rpi_gpio_callback(self, pin: GPIOPin, channel):
        """Callback från RPi.GPIO interrupt."""
        import RPi.GPIO as GPIO
        raw = GPIO.input(pin.pin_number)
        new_state = (not raw) if pin.active_low else bool(raw)

        if new_state != pin.state:
            pin.notify(new_state)
            self._fire_event(pin, new_state)

    # ── gpiozero setup ───────────────────────────────────────────────

    def _setup_gpiozero(self):
        """Konfigurera med gpiozero-biblioteket."""
        from gpiozero import LED, Button

        for name, pin in self.pins.items():
            if pin.direction == 'input':
                btn = Button(
                    pin.pin_number,
                    pull_up=pin.pull_up,
                    bounce_time=pin.debounce_ms / 1000.0,
                    active_state=not pin.active_low if not pin.pull_up else None,
                )
                pin._gpiozero_obj = btn
                pin.state = btn.is_pressed

                btn.when_pressed = lambda p=pin: self._gpiozero_callback(p, True)
                btn.when_released = lambda p=pin: self._gpiozero_callback(p, False)

            elif pin.direction == 'output':
                led = LED(pin.pin_number, active_high=not pin.active_low)
                pin._gpiozero_obj = led
                if pin.default_state:
                    led.on()
                else:
                    led.off()
                pin.state = pin.default_state

    def _gpiozero_callback(self, pin: GPIOPin, pressed: bool):
        """Callback från gpiozero."""
        new_state = pressed
        if new_state != pin.state:
            pin.notify(new_state)
            self._fire_event(pin, new_state)

    # ── Simuleringsläge ──────────────────────────────────────────────

    def _setup_simulated(self):
        """Konfigurera simuleringsläge (för testning utan GPIO-hårdvara)."""
        for name, pin in self.pins.items():
            self._simulated_states[pin.pin_number] = pin.default_state
            pin.state = pin.default_state

    # ── Polling-loop ─────────────────────────────────────────────────

    def _poll_loop(self):
        """Polling-loop för att läsa ingångar (backup om interrupts inte fungerar)."""
        while self.running:
            time.sleep(0.5)  # Polla varannan halvsekund

            if not GPIO_AVAILABLE:
                continue

            # RPi.GPIO använder interrupts, behöver inte pollas
            # gpiozero använder callbacks, behöver inte pollas
            # Denna loop är bara för hälsokontroll

    # ── Event-hantering ──────────────────────────────────────────────

    def _fire_event(self, pin: GPIOPin, new_state: bool):
        """Skicka GPIO-event till extern callback."""
        event = {
            'type': 'gpio_event',
            'pin_name': pin.name,
            'pin_number': pin.pin_number,
            'direction': pin.direction,
            'state': new_state,
            'previous_state': not new_state,
            'timestamp': time.time(),
        }

        logger.info(f"GPIO Event: {pin.name} (pin {pin.pin_number}) -> {'ON' if new_state else 'OFF'}")

        if self._event_callback:
            try:
                self._event_callback(event)
            except Exception as e:
                logger.error(f"GPIO event callback error: {e}")

    # ── Utgångs-styrning ─────────────────────────────────────────────

    def set_output(self, name: str, state: bool) -> bool:
        """Sätt en utgång till ett specifikt tillstånd."""
        pin = self.pins.get(name)
        if not pin or pin.direction != 'output':
            logger.warning(f"GPIO: Kan inte sätta '{name}' - inte en utgång")
            return False

        old_state = pin.state
        pin.state = state

        if GPIO_AVAILABLE and GPIO_LIB == 'RPi.GPIO':
            import RPi.GPIO as GPIO_mod
            out_val = (not state) if pin.active_low else state
            GPIO_mod.output(pin.pin_number, out_val)

        elif GPIO_AVAILABLE and GPIO_LIB == 'gpiozero':
            if hasattr(pin, '_gpiozero_obj'):
                if state:
                    pin._gpiozero_obj.on()
                else:
                    pin._gpiozero_obj.off()

        else:
            # Simuleringsläge
            self._simulated_states[pin.pin_number] = state

        pin.last_change = time.time()
        logger.info(f"GPIO Output: {name} (pin {pin.pin_number}) -> {'ON' if state else 'OFF'}")

        # Skicka event
        self._fire_event(pin, state)

        return True

    def toggle_output(self, name: str) -> bool:
        """Toggla en utgång."""
        pin = self.pins.get(name)
        if not pin or pin.direction != 'output':
            return False
        return self.set_output(name, not pin.state)

    def get_input(self, name: str) -> Optional[bool]:
        """Läs en ingång."""
        pin = self.pins.get(name)
        if not pin or pin.direction != 'input':
            return None
        return pin.state

    # ── Scener ───────────────────────────────────────────────────────

    def activate_scene(self, scene_name: str) -> bool:
        """Aktivera en scen (kör alla dess actions)."""
        scene = self.scenes.get(scene_name)
        if not scene:
            logger.warning(f"GPIO: Scen '{scene_name}' finns inte")
            return False

        logger.info(f"GPIO: Aktiverar scen '{scene_name}'")

        for action in scene.get('actions', []):
            output_name = action.get('output')
            state = action.get('state', True)
            delay = action.get('delay', 0)

            if delay > 0:
                time.sleep(delay)

            if output_name:
                self.set_output(output_name, state)

        # Skicka scen-event
        if self._event_callback:
            self._event_callback({
                'type': 'scene_activated',
                'scene_name': scene_name,
                'scene_label': scene.get('label', scene_name),
                'timestamp': time.time(),
            })

        return True

    # ── Status ───────────────────────────────────────────────────────

    def get_all_states(self) -> dict:
        """Hämta alla pin-tillstånd."""
        inputs = {}
        outputs = {}

        for name, pin in self.pins.items():
            if pin.direction == 'input':
                inputs[name] = pin.to_dict()
            elif pin.direction == 'output':
                outputs[name] = pin.to_dict()

        return {
            'inputs': inputs,
            'outputs': outputs,
            'scenes': {
                name: {
                    'name': s['name'],
                    'label': s['label'],
                    'icon': s['icon'],
                    'description': s['description'],
                    'action_count': len(s['actions']),
                }
                for name, s in self.scenes.items()
            },
            'gpio_available': GPIO_AVAILABLE,
            'gpio_library': GPIO_LIB or 'simulated',
        }

    def get_capabilities(self) -> dict:
        """Returnera GPIO-kapabiliteter för registrering hos servern."""
        gpio_inputs = [
            {
                'name': p.name,
                'label': p.name.replace('_', ' ').title(),
                'pin': p.pin_number,
                'active_low': p.active_low,
            }
            for p in self.pins.values() if p.direction == 'input'
        ]
        gpio_outputs = [
            {
                'name': p.name,
                'label': p.name.replace('_', ' ').title(),
                'pin': p.pin_number,
                'active_low': p.active_low,
            }
            for p in self.pins.values() if p.direction == 'output'
        ]

        return {
            'gpio': True,
            'gpio_inputs': gpio_inputs,
            'gpio_outputs': gpio_outputs,
            'gpio_scenes': [
                {
                    'name': s['name'],
                    'label': s['label'],
                    'icon': s['icon'],
                    'description': s['description'],
                }
                for s in self.scenes.values()
            ],
        }

    # ── Simulering (för testning) ────────────────────────────────────

    def simulate_input(self, name: str, state: bool):
        """Simulera en ingångsändring (för testning)."""
        pin = self.pins.get(name)
        if not pin or pin.direction != 'input':
            return

        if state != pin.state:
            pin.notify(state)
            self._fire_event(pin, state)
