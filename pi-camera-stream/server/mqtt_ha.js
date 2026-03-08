/**
 * MQTT Home Assistant Integration v5.0
 * 
 * Publicerar kamerastatus, rörelsehändelser och GPIO-tillstånd som
 * MQTT-sensorer med Home Assistant MQTT Discovery.
 * 
 * GPIO-integration:
 * - Ingångar → HA binary_sensor (dörrsensorer, PIR, knappar)
 * - Utgångar → HA switch (reläer, belysning, värme)
 * - Scener → HA button (fördefinierade kombinationer)
 * 
 * HA kan styra utgångar via MQTT command_topic och aktivera scener
 * via button press. Alla ändringar vidarebefordras till Pi-klienten
 * via WebSocket genom relay-servern.
 */

const mqtt = require('mqtt');

class MQTTHomeAssistant {
  constructor(options = {}) {
    this.enabled = options.enabled === true || options.enabled === 'true';
    this.host = options.host || 'localhost';
    this.port = parseInt(options.port) || 1883;
    this.username = options.username || '';
    this.password = options.password || '';
    this.topicPrefix = options.topicPrefix || 'pi_camera_stream';
    this.discoveryPrefix = options.discoveryPrefix || 'homeassistant';
    this.client = null;
    this.connected = false;
    this.registeredCameras = new Set();

    // Callback för att skicka kommandon till Pi-klienter via relay-servern
    this._gpioCommandCallback = null;
    this._sceneCommandCallback = null;

    // Håll koll på registrerade GPIO-pins och scener för MQTT-prenumerationer
    this._subscribedTopics = new Set();

    if (this.enabled) {
      this.connect();
    }
  }

  /**
   * Sätt callback som anropas när HA skickar ett GPIO-kommando via MQTT.
   * Callback: (cameraId, pinName, state) => void
   */
  onGPIOCommand(callback) {
    this._gpioCommandCallback = callback;
  }

  /**
   * Sätt callback som anropas när HA aktiverar en scen via MQTT.
   * Callback: (cameraId, sceneName) => void
   */
  onSceneCommand(callback) {
    this._sceneCommandCallback = callback;
  }

  connect() {
    const url = `mqtt://${this.host}:${this.port}`;
    console.log(`[MQTT] Connecting to ${url}...`);

    const opts = {
      clientId: `pi_camera_stream_${Date.now()}`,
      clean: true,
      reconnectPeriod: 5000,
      connectTimeout: 10000,
    };

    if (this.username) {
      opts.username = this.username;
      opts.password = this.password;
    }

    // Will message – markera som offline om anslutningen bryts
    opts.will = {
      topic: `${this.topicPrefix}/status`,
      payload: 'offline',
      qos: 1,
      retain: true,
    };

    this.client = mqtt.connect(url, opts);

    this.client.on('connect', () => {
      console.log('[MQTT] Connected to broker');
      this.connected = true;

      // Publicera online-status
      this.client.publish(
        `${this.topicPrefix}/status`,
        'online',
        { qos: 1, retain: true }
      );

      // Registrera om alla kameror (vid reconnect)
      this.registeredCameras.forEach(cameraId => {
        this._publishDiscovery(cameraId);
      });

      // Prenumerera om på alla command-topics
      this._subscribedTopics.forEach(topic => {
        this.client.subscribe(topic, { qos: 1 });
      });
    });

    this.client.on('error', (err) => {
      console.error(`[MQTT] Error: ${err.message}`);
    });

    this.client.on('offline', () => {
      console.log('[MQTT] Disconnected');
      this.connected = false;
    });

    this.client.on('reconnect', () => {
      console.log('[MQTT] Reconnecting...');
    });

    // ── Hantera inkommande MQTT-meddelanden (kommandon från HA) ──
    this.client.on('message', (topic, message) => {
      const payload = message.toString();
      this._handleIncomingMessage(topic, payload);
    });
  }

  // ── Hantera inkommande MQTT-meddelanden ───────────────────────────

  _handleIncomingMessage(topic, payload) {
    // GPIO output command: pi_camera_stream/{cameraId}/gpio/{pinName}/set
    const gpioSetMatch = topic.match(
      new RegExp(`^${this._escapeRegex(this.topicPrefix)}/([^/]+)/gpio/([^/]+)/set$`)
    );
    if (gpioSetMatch) {
      const cameraId = gpioSetMatch[1];
      const pinName = gpioSetMatch[2];
      const state = payload === 'ON';
      console.log(`[MQTT] HA command: GPIO ${pinName} on ${cameraId} -> ${state ? 'ON' : 'OFF'}`);

      if (this._gpioCommandCallback) {
        this._gpioCommandCallback(cameraId, pinName, state);
      }
      return;
    }

    // Scene command: pi_camera_stream/{cameraId}/scene/{sceneName}/activate
    const sceneMatch = topic.match(
      new RegExp(`^${this._escapeRegex(this.topicPrefix)}/([^/]+)/scene/([^/]+)/activate$`)
    );
    if (sceneMatch) {
      const cameraId = sceneMatch[1];
      const sceneName = sceneMatch[2];
      console.log(`[MQTT] HA command: Activate scene '${sceneName}' on ${cameraId}`);

      if (this._sceneCommandCallback) {
        this._sceneCommandCallback(cameraId, sceneName);
      }
      return;
    }
  }

  _escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // ── MQTT Discovery – registrera kamera som HA-enhet ────────────────

  registerCamera(cameraId, cameraInfo = {}) {
    if (!this.enabled || !this.connected) return;

    this.registeredCameras.add(cameraId);
    this._publishDiscovery(cameraId, cameraInfo);

    // Publicera initial status
    this._publish(`${this.topicPrefix}/${cameraId}/status`, 'online');
    this._publish(`${this.topicPrefix}/${cameraId}/motion`, 'OFF');
  }

  _publishDiscovery(cameraId, cameraInfo = {}) {
    const deviceId = `pi_cam_${cameraId.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
    const deviceName = cameraInfo.name || cameraId;

    const device = {
      identifiers: [deviceId],
      name: `Pi Camera: ${deviceName}`,
      manufacturer: 'Pi Camera Stream',
      model: 'Network Camera',
      sw_version: '5.0.0',
    };

    // ── Motion sensor (binary_sensor) ──
    const motionConfig = {
      name: `${deviceName} Rörelse`,
      unique_id: `${deviceId}_motion`,
      device_class: 'motion',
      state_topic: `${this.topicPrefix}/${cameraId}/motion`,
      payload_on: 'ON',
      payload_off: 'OFF',
      availability_topic: `${this.topicPrefix}/${cameraId}/status`,
      payload_available: 'online',
      payload_not_available: 'offline',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/binary_sensor/${deviceId}/motion/config`,
      JSON.stringify(motionConfig),
      { retain: true }
    );

    // ── Connection status sensor ──
    const statusConfig = {
      name: `${deviceName} Status`,
      unique_id: `${deviceId}_status`,
      device_class: 'connectivity',
      state_topic: `${this.topicPrefix}/${cameraId}/status`,
      payload_on: 'online',
      payload_off: 'offline',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/binary_sensor/${deviceId}/status/config`,
      JSON.stringify(statusConfig),
      { retain: true }
    );

    // ── Viewers count sensor ──
    const viewersConfig = {
      name: `${deviceName} Tittare`,
      unique_id: `${deviceId}_viewers`,
      icon: 'mdi:eye',
      state_topic: `${this.topicPrefix}/${cameraId}/viewers`,
      unit_of_measurement: 'st',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/sensor/${deviceId}/viewers/config`,
      JSON.stringify(viewersConfig),
      { retain: true }
    );

    // ── FPS sensor ──
    const fpsConfig = {
      name: `${deviceName} FPS`,
      unique_id: `${deviceId}_fps`,
      icon: 'mdi:speedometer',
      state_topic: `${this.topicPrefix}/${cameraId}/fps`,
      unit_of_measurement: 'fps',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/sensor/${deviceId}/fps/config`,
      JSON.stringify(fpsConfig),
      { retain: true }
    );

    // ── Recording status sensor ──
    const recordingConfig = {
      name: `${deviceName} Inspelning`,
      unique_id: `${deviceId}_recording`,
      icon: 'mdi:record-rec',
      state_topic: `${this.topicPrefix}/${cameraId}/recording`,
      payload_on: 'ON',
      payload_off: 'OFF',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/binary_sensor/${deviceId}/recording/config`,
      JSON.stringify(recordingConfig),
      { retain: true }
    );

    // ── Last motion timestamp sensor ──
    const lastMotionConfig = {
      name: `${deviceName} Senaste rörelse`,
      unique_id: `${deviceId}_last_motion`,
      icon: 'mdi:clock-outline',
      state_topic: `${this.topicPrefix}/${cameraId}/last_motion`,
      device_class: 'timestamp',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/sensor/${deviceId}/last_motion/config`,
      JSON.stringify(lastMotionConfig),
      { retain: true }
    );

    // ── Detected objects sensor ──
    const objectsConfig = {
      name: `${deviceName} Detekterade objekt`,
      unique_id: `${deviceId}_objects`,
      icon: 'mdi:shape-outline',
      state_topic: `${this.topicPrefix}/${cameraId}/objects`,
      json_attributes_topic: `${this.topicPrefix}/${cameraId}/objects_attr`,
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/sensor/${deviceId}/objects/config`,
      JSON.stringify(objectsConfig),
      { retain: true }
    );

    // ── CPU Temperature sensor ──
    const cpuTempConfig = {
      name: `${deviceName} CPU Temperatur`,
      unique_id: `${deviceId}_cpu_temp`,
      device_class: 'temperature',
      unit_of_measurement: '°C',
      state_topic: `${this.topicPrefix}/${cameraId}/cpu_temp`,
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/sensor/${deviceId}/cpu_temp/config`,
      JSON.stringify(cpuTempConfig),
      { retain: true }
    );

    // ── CPU Usage sensor ──
    const cpuUsageConfig = {
      name: `${deviceName} CPU Användning`,
      unique_id: `${deviceId}_cpu_usage`,
      icon: 'mdi:cpu-64-bit',
      unit_of_measurement: '%',
      state_topic: `${this.topicPrefix}/${cameraId}/cpu_usage`,
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/sensor/${deviceId}/cpu_usage/config`,
      JSON.stringify(cpuUsageConfig),
      { retain: true }
    );

    console.log(`[MQTT] Registered camera ${cameraId} with HA discovery`);
  }

  // ── GPIO Discovery ────────────────────────────────────────────────

  registerGPIOInput(cameraId, pinName, pinConfig = {}, cameraInfo = {}) {
    if (!this.enabled || !this.connected) return;

    const deviceId = `pi_cam_${cameraId.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
    const pinId = pinName.replace(/[^a-zA-Z0-9_-]/g, '_');
    const deviceName = cameraInfo.name || cameraId;
    const pinLabel = pinConfig.label || pinName;

    // Bestäm device_class baserat på pin-namn
    let deviceClass = 'opening';  // default
    if (pinName.includes('pir') || pinName.includes('motion') || pinName.includes('rorelse')) {
      deviceClass = 'motion';
    } else if (pinName.includes('dorr') || pinName.includes('door')) {
      deviceClass = 'door';
    } else if (pinName.includes('fonster') || pinName.includes('window')) {
      deviceClass = 'window';
    } else if (pinName.includes('larm') || pinName.includes('alarm') || pinName.includes('knapp') || pinName.includes('button')) {
      deviceClass = 'safety';
    } else if (pinName.includes('smoke') || pinName.includes('rok')) {
      deviceClass = 'smoke';
    } else if (pinName.includes('vatten') || pinName.includes('water') || pinName.includes('leak')) {
      deviceClass = 'moisture';
    }

    const device = {
      identifiers: [deviceId],
      name: `Pi Camera: ${deviceName}`,
      manufacturer: 'Pi Camera Stream',
      model: 'Network Camera + GPIO',
      sw_version: '5.0.0',
    };

    const gpioConfig = {
      name: `${deviceName} ${pinLabel}`,
      unique_id: `${deviceId}_gpio_in_${pinId}`,
      device_class: deviceClass,
      state_topic: `${this.topicPrefix}/${cameraId}/gpio/${pinName}`,
      payload_on: 'ON',
      payload_off: 'OFF',
      availability_topic: `${this.topicPrefix}/${cameraId}/status`,
      payload_available: 'online',
      payload_not_available: 'offline',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/binary_sensor/${deviceId}/gpio_${pinId}/config`,
      JSON.stringify(gpioConfig),
      { retain: true }
    );

    console.log(`[MQTT] Registered GPIO input: ${pinName} on ${cameraId} as binary_sensor (${deviceClass})`);
  }

  registerGPIOOutput(cameraId, pinName, pinConfig = {}, cameraInfo = {}) {
    if (!this.enabled || !this.connected) return;

    const deviceId = `pi_cam_${cameraId.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
    const pinId = pinName.replace(/[^a-zA-Z0-9_-]/g, '_');
    const deviceName = cameraInfo.name || cameraId;
    const pinLabel = pinConfig.label || pinName;

    // Bestäm ikon baserat på pin-namn
    let icon = 'mdi:toggle-switch';
    if (pinName.includes('belysning') || pinName.includes('light') || pinName.includes('lamp')) {
      icon = 'mdi:lightbulb';
    } else if (pinName.includes('varme') || pinName.includes('heat')) {
      icon = 'mdi:radiator';
    } else if (pinName.includes('siren') || pinName.includes('larm') || pinName.includes('alarm')) {
      icon = 'mdi:alarm-light';
    } else if (pinName.includes('las') || pinName.includes('lock')) {
      icon = 'mdi:lock';
    } else if (pinName.includes('fan') || pinName.includes('flakt')) {
      icon = 'mdi:fan';
    }

    const device = {
      identifiers: [deviceId],
      name: `Pi Camera: ${deviceName}`,
      manufacturer: 'Pi Camera Stream',
      model: 'Network Camera + GPIO',
      sw_version: '5.0.0',
    };

    // Registrera som HA switch med command_topic
    const commandTopic = `${this.topicPrefix}/${cameraId}/gpio/${pinName}/set`;
    const stateTopic = `${this.topicPrefix}/${cameraId}/gpio/${pinName}`;

    const gpioConfig = {
      name: `${deviceName} ${pinLabel}`,
      unique_id: `${deviceId}_gpio_out_${pinId}`,
      icon: icon,
      state_topic: stateTopic,
      command_topic: commandTopic,
      payload_on: 'ON',
      payload_off: 'OFF',
      state_on: 'ON',
      state_off: 'OFF',
      availability_topic: `${this.topicPrefix}/${cameraId}/status`,
      payload_available: 'online',
      payload_not_available: 'offline',
      optimistic: false,
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/switch/${deviceId}/gpio_${pinId}/config`,
      JSON.stringify(gpioConfig),
      { retain: true }
    );

    // Prenumerera på command_topic för att ta emot kommandon från HA
    this._subscribeToTopic(commandTopic);

    console.log(`[MQTT] Registered GPIO output: ${pinName} on ${cameraId} as switch (${icon})`);
  }

  registerScene(cameraId, sceneName, sceneConfig = {}, cameraInfo = {}) {
    if (!this.enabled || !this.connected) return;

    const deviceId = `pi_cam_${cameraId.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
    const sceneId = sceneName.replace(/[^a-zA-Z0-9_-]/g, '_');
    const deviceName = cameraInfo.name || cameraId;
    const sceneLabel = sceneConfig.label || sceneName;

    // Bestäm ikon baserat på scen-namn
    let icon = 'mdi:play-circle';
    if (sceneName.includes('vinter') || sceneName.includes('winter')) {
      icon = 'mdi:snowflake';
    } else if (sceneName.includes('hemkomst') || sceneName.includes('home')) {
      icon = 'mdi:home';
    } else if (sceneName.includes('lamna') || sceneName.includes('leave') || sceneName.includes('away')) {
      icon = 'mdi:lock';
    } else if (sceneName.includes('larm') || sceneName.includes('alarm')) {
      icon = 'mdi:shield';
    } else if (sceneName.includes('natt') || sceneName.includes('night')) {
      icon = 'mdi:weather-night';
    }
    // Använd scen-konfigurerad ikon om den finns
    if (sceneConfig.icon) {
      icon = `mdi:${sceneConfig.icon}`;
    }

    const device = {
      identifiers: [deviceId],
      name: `Pi Camera: ${deviceName}`,
      manufacturer: 'Pi Camera Stream',
      model: 'Network Camera + GPIO',
      sw_version: '5.0.0',
    };

    // Registrera som HA button entity
    const commandTopic = `${this.topicPrefix}/${cameraId}/scene/${sceneName}/activate`;

    const buttonConfig = {
      name: `${deviceName} ${sceneLabel}`,
      unique_id: `${deviceId}_scene_${sceneId}`,
      icon: icon,
      command_topic: commandTopic,
      payload_press: 'PRESS',
      availability_topic: `${this.topicPrefix}/${cameraId}/status`,
      payload_available: 'online',
      payload_not_available: 'offline',
      device,
    };
    this._publish(
      `${this.discoveryPrefix}/button/${deviceId}/scene_${sceneId}/config`,
      JSON.stringify(buttonConfig),
      { retain: true }
    );

    // Prenumerera på command_topic
    this._subscribeToTopic(commandTopic);

    console.log(`[MQTT] Registered scene: ${sceneName} on ${cameraId} as button (${icon})`);
  }

  // ── Prenumerera på MQTT-topics ────────────────────────────────────

  _subscribeToTopic(topic) {
    this._subscribedTopics.add(topic);
    if (this.client && this.connected) {
      this.client.subscribe(topic, { qos: 1 }, (err) => {
        if (err) {
          console.error(`[MQTT] Subscribe error for ${topic}: ${err.message}`);
        } else {
          console.log(`[MQTT] Subscribed to: ${topic}`);
        }
      });
    }
  }

  // ── Publicera events ──────────────────────────────────────────────

  publishMotionStart(cameraId, data = {}) {
    if (!this.enabled || !this.connected) return;

    this._publish(`${this.topicPrefix}/${cameraId}/motion`, 'ON');
    this._publish(`${this.topicPrefix}/${cameraId}/last_motion`, new Date().toISOString());

    if (data.objects && data.objects.length > 0) {
      const objectNames = data.objects.map(o => o.label || o.class || o).join(', ');
      this._publish(`${this.topicPrefix}/${cameraId}/objects`, objectNames);
      this._publish(`${this.topicPrefix}/${cameraId}/objects_attr`, JSON.stringify({
        objects: data.objects,
        timestamp: new Date().toISOString(),
      }));
    }
  }

  publishMotionEnd(cameraId) {
    if (!this.enabled || !this.connected) return;
    this._publish(`${this.topicPrefix}/${cameraId}/motion`, 'OFF');
  }

  publishCameraStatus(cameraId, status) {
    if (!this.enabled || !this.connected) return;
    this._publish(`${this.topicPrefix}/${cameraId}/status`, status ? 'online' : 'offline', { retain: true });
  }

  publishViewerCount(cameraId, count) {
    if (!this.enabled || !this.connected) return;
    this._publish(`${this.topicPrefix}/${cameraId}/viewers`, String(count));
  }

  publishFPS(cameraId, fps) {
    if (!this.enabled || !this.connected) return;
    this._publish(`${this.topicPrefix}/${cameraId}/fps`, String(Math.round(fps)));
  }

  publishRecordingStatus(cameraId, recording) {
    if (!this.enabled || !this.connected) return;
    this._publish(`${this.topicPrefix}/${cameraId}/recording`, recording ? 'ON' : 'OFF');
  }

  publishGPIOEvent(cameraId, pinName, state) {
    if (!this.enabled || !this.connected) return;
    this._publish(`${this.topicPrefix}/${cameraId}/gpio/${pinName}`, state ? 'ON' : 'OFF');
  }

  publishSceneActivated(cameraId, sceneName) {
    if (!this.enabled || !this.connected) return;
    // Publicera event som HA kan använda i automationer
    this._publish(`${this.topicPrefix}/${cameraId}/scene/${sceneName}/state`, JSON.stringify({
      activated: true,
      timestamp: new Date().toISOString(),
    }));
  }

  publishHealth(cameraId, healthData = {}) {
    if (!this.enabled || !this.connected) return;
    if (healthData.cpu_temp !== undefined) {
      this._publish(`${this.topicPrefix}/${cameraId}/cpu_temp`, String(healthData.cpu_temp));
    }
    if (healthData.cpu_usage !== undefined) {
      this._publish(`${this.topicPrefix}/${cameraId}/cpu_usage`, String(healthData.cpu_usage));
    }
    if (healthData.fps !== undefined) {
      this._publish(`${this.topicPrefix}/${cameraId}/fps`, String(Math.round(healthData.fps)));
    }
  }

  // ── Avregistrera kamera ───────────────────────────────────────────

  unregisterCamera(cameraId) {
    if (!this.enabled || !this.connected) return;

    this._publish(`${this.topicPrefix}/${cameraId}/status`, 'offline', { retain: true });
    this._publish(`${this.topicPrefix}/${cameraId}/motion`, 'OFF');
    this.registeredCameras.delete(cameraId);
  }

  // ── Hjälpmetoder ──────────────────────────────────────────────────

  _publish(topic, message, opts = {}) {
    if (!this.client || !this.connected) return;
    try {
      this.client.publish(topic, message, { qos: 0, ...opts });
    } catch (err) {
      console.error(`[MQTT] Publish error: ${err.message}`);
    }
  }

  close() {
    if (this.client) {
      // Markera alla kameror som offline
      this.registeredCameras.forEach(cameraId => {
        this._publish(`${this.topicPrefix}/${cameraId}/status`, 'offline', { retain: true });
      });
      this._publish(`${this.topicPrefix}/status`, 'offline', { retain: true });
      this.client.end();
    }
  }
}

module.exports = MQTTHomeAssistant;
