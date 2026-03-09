/**
 * Pi Camera Relay Server v5.1.0 – Home Assistant Add-on Edition
 * 
 * Anpassad för att köras som HA add-on med:
 * - Ingress-stöd (X-Ingress-Path header)
 * - HA Auth API (valfritt – autentisera via HA:s egna användare)
 * - MQTT HA Discovery (rörelse, status, FPS som HA-sensorer)
 * - Allt från v3.0: JWT-auth, inspelning, PTZ, dashboard
 */

const WebSocket = require('ws');
const express = require('express');
const http = require('http');
const https = require('https');
const { v4: uuidv4 } = require('uuid');
const path = require('path');
const fs = require('fs');
const AuthManager = require('./auth');
const RecordingsManager = require('./recordings');
const MQTTHomeAssistant = require('./mqtt_ha');

// ============================================================================
// Konfiguration (läser från miljövariabler satta av run.sh)
// ============================================================================

const IS_HA_ADDON = process.env.HA_ADDON === 'true';
const INGRESS_PATH = process.env.INGRESS_PATH || '';

const CONFIG = {
  port: parseInt(process.env.PORT) || 8099,
  cameraSecret: process.env.CAMERA_SECRET || 'change-me-to-a-strong-secret',
  jwtSecret: process.env.JWT_SECRET || 'change-me-jwt-secret',
  maxViewersPerCamera: parseInt(process.env.MAX_VIEWERS) || 20,
  heartbeatInterval: 30000,
  maxFrameSize: 5 * 1024 * 1024,
  recordingsDir: process.env.RECORDING_PATH || path.join(__dirname, '..', 'data', 'recordings'),
  dbPath: process.env.DB_PATH || path.join(__dirname, '..', 'data', 'db'),
  recordOnMotion: process.env.RECORDING_ENABLED !== 'false',
  motionRecordDuration: 30,
  cleanupMaxAgeDays: parseInt(process.env.CLEANUP_DAYS) || 30,
  maxRecordingSizeMB: parseInt(process.env.MAX_RECORDING_SIZE_MB) || 1000,
  logLevel: process.env.LOG_LEVEL || 'info',
  supervisorToken: process.env.SUPERVISOR_TOKEN || '',
  haAuthApi: process.env.HA_AUTH_API === 'true',
  directWsUrl: process.env.DIRECT_WS_URL || '',
};

console.log('===========================================');
console.log('  Pi Camera Relay Server v5.1.0');
console.log('  Home Assistant Add-on Edition');
console.log('===========================================');
if (IS_HA_ADDON) {
  console.log(`  Ingress path: ${INGRESS_PATH}`);
  if (CONFIG.directWsUrl) console.log(`  Direct WS URL: ${CONFIG.directWsUrl}`);
}

// ============================================================================
// Initiera moduler
// ============================================================================

const auth = new AuthManager({ jwtSecret: CONFIG.jwtSecret, dbPath: CONFIG.dbPath });
const recordings = new RecordingsManager({ recordingsDir: CONFIG.recordingsDir, dbPath: CONFIG.dbPath });
const mqttHA = new MQTTHomeAssistant({
  enabled: process.env.MQTT_ENABLED,
  host: process.env.MQTT_HOST,
  port: process.env.MQTT_PORT,
  username: process.env.MQTT_USER,
  password: process.env.MQTT_PASS,
  topicPrefix: process.env.MQTT_TOPIC_PREFIX || 'pi_camera_stream',
});

// ============================================================================
// State
// ============================================================================

const cameras = new Map();
const viewers = new Map();
const allViewers = new Map();
const motionTimers = new Map();
const serverStartTime = Date.now();

// ============================================================================
// Express + HTTP-server
// ============================================================================

const app = express();
const server = http.createServer(app);
app.use(express.json());

// ── Ingress-aware base path middleware ──────────────────────────────

app.use((req, res, next) => {
  // HA Ingress skickar X-Ingress-Path header
  const ingressPath = req.headers['x-ingress-path'] || INGRESS_PATH || '';
  req.ingressPath = ingressPath;
  res.locals.ingressPath = ingressPath;
  next();
});

// ── Servera viewer-appen ────────────────────────────────────────────

const viewerPath = path.join(__dirname, '..', 'viewer');
if (fs.existsSync(viewerPath)) {
  app.use('/viewer', express.static(viewerPath));
  // Redirect root till viewer
  app.get('/', (req, res) => {
    const base = req.ingressPath || '';
    res.redirect(`${base}/viewer/`);
  });
}

// Servera inspelningar
app.use('/recordings', express.static(CONFIG.recordingsDir));

// ── Auth middleware ──────────────────────────────────────────────────

function authMiddleware(requiredRole = null) {
  return async (req, res, next) => {
    // Metod 1: JWT token
    const token = req.headers.authorization?.replace('Bearer ', '') ||
                  req.query.token;

    if (token) {
      const payload = auth.verifyToken(token);
      if (payload) {
        if (requiredRole && payload.role !== requiredRole && payload.role !== 'admin') {
          return res.status(403).json({ error: 'Insufficient permissions' });
        }
        req.user = payload;
        return next();
      }
    }

    // Metod 2: HA Ingress auth (automatisk via HA proxy)
    if (IS_HA_ADDON && req.headers['x-ingress-path']) {
      // HA Ingress hanterar auth automatiskt – om vi når hit är användaren autentiserad
      req.user = {
        userId: 0,
        username: 'ha_user',
        role: 'admin',
        allowedCameras: '*',
      };
      return next();
    }

    return res.status(401).json({ error: 'No valid authentication' });
  };
}

// ── Auth API ────────────────────────────────────────────────────────

app.post('/api/auth/login', (req, res) => {
  const { username, password } = req.body;
  if (!username || !password) {
    return res.status(400).json({ error: 'Username and password required' });
  }

  const result = auth.login(username, password);
  if (!result) {
    return res.status(401).json({ error: 'Invalid credentials' });
  }

  auth.logAction(result.user.id, 'login', '', req.ip);
  res.json(result);
});

app.get('/api/auth/me', authMiddleware(), (req, res) => {
  if (req.user.userId === 0) {
    // HA ingress user
    return res.json({ user: { id: 0, username: 'ha_user', role: 'admin', displayName: 'Home Assistant' } });
  }
  const user = auth.getUser(req.user.userId);
  res.json({ user });
});

app.post('/api/auth/change-password', authMiddleware(), (req, res) => {
  const { currentPassword, newPassword } = req.body;
  if (!currentPassword || !newPassword) {
    return res.status(400).json({ error: 'Both passwords required' });
  }

  const user = auth.getUserByUsername(req.user.username);
  const bcrypt = require('bcryptjs');
  if (!bcrypt.compareSync(currentPassword, user.password_hash)) {
    return res.status(401).json({ error: 'Current password is incorrect' });
  }

  auth.updateUser(req.user.userId, { password: newPassword });
  auth.logAction(req.user.userId, 'change_password', '', req.ip);
  res.json({ message: 'Password changed' });
});

// ── User Management API (admin only) ────────────────────────────────

app.get('/api/users', authMiddleware('admin'), (req, res) => {
  res.json({ users: auth.listUsers() });
});

app.post('/api/users', authMiddleware('admin'), (req, res) => {
  try {
    const { username, password, role, displayName, allowedCameras } = req.body;
    const user = auth.createUser({ username, password, role, displayName, allowedCameras });
    auth.logAction(req.user.userId, 'create_user', username, req.ip);
    res.json({ user });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

app.put('/api/users/:id', authMiddleware('admin'), (req, res) => {
  const { displayName, role, allowedCameras, active, password } = req.body;
  auth.updateUser(parseInt(req.params.id), { displayName, role, allowedCameras, active, password });
  auth.logAction(req.user.userId, 'update_user', `id:${req.params.id}`, req.ip);
  res.json({ message: 'User updated' });
});

app.delete('/api/users/:id', authMiddleware('admin'), (req, res) => {
  if (parseInt(req.params.id) === req.user.userId) {
    return res.status(400).json({ error: 'Cannot delete yourself' });
  }
  auth.deleteUser(parseInt(req.params.id));
  auth.logAction(req.user.userId, 'delete_user', `id:${req.params.id}`, req.ip);
  res.json({ message: 'User deleted' });
});

// ── Camera API ──────────────────────────────────────────────────────

app.get('/api/cameras', authMiddleware(), (req, res) => {
  const cameraList = [];
  cameras.forEach((cam, id) => {
    if (auth.canAccessCamera(req.user, id)) {
      cameraList.push({
        id,
        name: cam.info?.name || 'Unnamed',
        resolution: cam.info?.resolution || 'unknown',
        connectedAt: cam.connectedAt,
        viewers: viewers.get(id)?.size || 0,
        health: cam.health || {},
        recording: recordings.isRecording(id),
        capabilities: cam.info?.capabilities || {},
        detectionLevel: cam.health?.detectionLevel ?? cam.info?.capabilities?.detection_level ?? 0,
        hardwareSummary: cam.info?.capabilities?.hardware_summary || {},
        localRecording: cam.info?.capabilities?.local_recording || false,
        storageStats: cam.health?.storage || null,
      });
    }
  });
  res.json({ cameras: cameraList });
});

// ── Dashboard API ───────────────────────────────────────────────────

app.get('/api/dashboard', authMiddleware(), (req, res) => {
  const cameraStats = [];
  cameras.forEach((cam, id) => {
    if (auth.canAccessCamera(req.user, id)) {
      cameraStats.push({
        id,
        name: cam.info?.name || 'Unknown',
        status: 'online',
        viewers: viewers.get(id)?.size || 0,
        health: cam.health || {},
        recording: recordings.isRecording(id),
      });
    }
  });

  const storageStats = recordings.getStorageStats();

  res.json({
    server: {
      uptime: Math.floor((Date.now() - serverStartTime) / 1000),
      totalCameras: cameras.size,
      totalViewers: allViewers.size,
      memoryUsage: process.memoryUsage(),
      isHaAddon: IS_HA_ADDON,
      mqttConnected: mqttHA.connected,
    },
    cameras: cameraStats,
    storage: storageStats,
  });
});

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    version: '5.1.0-ha',
    cameras: cameras.size,
    totalViewers: allViewers.size,
    uptime: Math.floor((Date.now() - serverStartTime) / 1000),
    mqtt: mqttHA.connected,
    haAddon: IS_HA_ADDON,
    ingressPath: INGRESS_PATH || null,
    wsClients: wss.clients.size,
    directWsUrl: CONFIG.directWsUrl || null,
  });
});

// ── Camera Secret API (för att visa hemligheten i HA) ───────────────

app.get('/api/camera-secret', authMiddleware('admin'), (req, res) => {
  res.json({ secret: CONFIG.cameraSecret });
});

// ── Recordings / Timeline API ───────────────────────────────────────

app.get('/api/recordings/:cameraId', authMiddleware(), (req, res) => {
  if (!auth.canAccessCamera(req.user, req.params.cameraId)) {
    return res.status(403).json({ error: 'Access denied' });
  }
  const recs = recordings.getRecordings(req.params.cameraId, {
    from: req.query.from,
    to: req.query.to,
    limit: parseInt(req.query.limit) || 50,
  });
  res.json({ recordings: recs });
});

app.get('/api/timeline/:cameraId/:date', authMiddleware(), (req, res) => {
  if (!auth.canAccessCamera(req.user, req.params.cameraId)) {
    return res.status(403).json({ error: 'Access denied' });
  }
  const timeline = recordings.getTimeline(req.params.cameraId, req.params.date);
  res.json(timeline);
});

app.get('/api/events/:cameraId', authMiddleware(), (req, res) => {
  if (!auth.canAccessCamera(req.user, req.params.cameraId)) {
    return res.status(403).json({ error: 'Access denied' });
  }
  const events = recordings.getEvents(req.params.cameraId, {
    from: req.query.from,
    to: req.query.to,
    type: req.query.type,
    limit: parseInt(req.query.limit) || 50,
  });
  res.json({ events });
});

app.get('/api/recordings/:cameraId/thumbnail/:recordingId', authMiddleware(), (req, res) => {
  if (!auth.canAccessCamera(req.user, req.params.cameraId)) {
    return res.status(403).json({ error: 'Access denied' });
  }
  const thumbPath = recordings.getThumbnail(parseInt(req.params.recordingId));
  if (thumbPath) {
    res.sendFile(thumbPath);
  } else {
    res.status(404).json({ error: 'Thumbnail not found' });
  }
});

// ── Audit Log API ───────────────────────────────────────────────────

app.get('/api/audit-log', authMiddleware('admin'), (req, res) => {
  const log = auth.getAuditLog(parseInt(req.query.limit) || 100);
  res.json({ log });
});

// ============================================================================
// HTTP Polling API (fallback when WebSocket fails through ingress/Cloudflare)
// ============================================================================

const pollingViewers = new Map(); // viewerId -> { user, subscribedCameras, messageQueue, lastFrame, lastPoll }
const POLL_VIEWER_TIMEOUT = 60000; // Remove viewer after 60s of no polling

// Cleanup stale polling viewers
setInterval(() => {
  const now = Date.now();
  pollingViewers.forEach((pv, id) => {
    if (now - pv.lastPoll > POLL_VIEWER_TIMEOUT) {
      console.log(`[Poll] Removing stale viewer: ${id}`);
      // Remove from camera viewer sets
      pv.subscribedCameras.forEach(camId => {
        const camViewers = viewers.get(camId);
        if (camViewers) camViewers.delete(pv.viewerProxy);
      });
      pollingViewers.delete(id);
    }
  });
}, 15000);

// Register a polling viewer
app.post('/api/poll/register', authMiddleware(), (req, res) => {
  const viewerId = uuidv4();
  const userPayload = req.user;
  
  const allCamerasList = [];
  cameras.forEach((cam, id) => {
    if (auth.canAccessCamera(userPayload, id)) {
      allCamerasList.push({
        id,
        name: cam.info?.name || 'Unknown',
        resolution: cam.info?.resolution || 'unknown',
        viewers: viewers.get(id)?.size || 0,
        capabilities: cam.info?.capabilities || {},
        detectionLevel: cam.health?.detectionLevel ?? cam.info?.capabilities?.detection_level ?? 0,
        hardwareSummary: cam.info?.capabilities?.hardware_summary || {},
        localRecording: cam.info?.capabilities?.local_recording || false,
      });
    }
  });

  // Create a proxy "ws" object so broadcastToViewers works for polling viewers too
  const viewerProxy = {
    ws: {
      readyState: 1, // OPEN
      send: (data) => {
        try {
          const pv = pollingViewers.get(viewerId);
          if (pv) {
            if (Buffer.isBuffer(data) || data instanceof ArrayBuffer || data instanceof Uint8Array) {
              // Binary frame - store as latest frame per camera
              const buf = Buffer.from(data);
              const idLen = buf.readUInt32BE(0);
              const camId = buf.slice(4, 4 + idLen).toString('utf8');
              pv.lastFrame.set(camId, buf.slice(4 + idLen));
            } else {
              // JSON message - queue it
              pv.messageQueue.push(typeof data === 'string' ? data : data.toString());
              if (pv.messageQueue.length > 100) pv.messageQueue.shift();
            }
          }
        } catch(e) {}
      },
    },
    id: viewerId,
    user: userPayload,
    subscribedCameras: new Set(),
  };

  pollingViewers.set(viewerId, {
    user: userPayload,
    subscribedCameras: new Set(),
    messageQueue: [],
    lastFrame: new Map(),
    lastPoll: Date.now(),
    viewerProxy,
  });

  allViewers.set(viewerId, viewerProxy);

  console.log(`[Poll] Viewer registered: ${viewerId} (${userPayload.username})`);

  res.json({
    type: 'connected',
    viewer_id: viewerId,
    user: { username: userPayload.username, role: userPayload.role },
    subscribed_cameras: [],
    available_cameras: allCamerasList,
    transport: 'polling',
  });
});

// Poll for messages
app.get('/api/poll/messages/:viewerId', authMiddleware(), (req, res) => {
  const pv = pollingViewers.get(req.params.viewerId);
  if (!pv) return res.status(404).json({ error: 'Viewer not found, re-register' });
  pv.lastPoll = Date.now();
  const messages = pv.messageQueue.splice(0);
  res.json({ messages: messages.map(m => { try { return JSON.parse(m); } catch(e) { return null; } }).filter(Boolean) });
});

// Poll for latest frame (JPEG) for a camera
app.get('/api/poll/frame/:viewerId/:cameraId', authMiddleware(), (req, res) => {
  const pv = pollingViewers.get(req.params.viewerId);
  if (!pv) return res.status(404).json({ error: 'Viewer not found' });
  pv.lastPoll = Date.now();
  const frame = pv.lastFrame.get(req.params.cameraId);
  if (frame) {
    pv.lastFrame.delete(req.params.cameraId); // Clear after serving
    res.set('Content-Type', 'image/jpeg');
    res.send(frame);
  } else {
    res.status(204).end(); // No new frame
  }
});

// Subscribe/unsubscribe via polling
app.post('/api/poll/action/:viewerId', authMiddleware(), (req, res) => {
  const pv = pollingViewers.get(req.params.viewerId);
  if (!pv) return res.status(404).json({ error: 'Viewer not found' });
  pv.lastPoll = Date.now();
  
  const msg = req.body;
  if (!msg || !msg.type) return res.status(400).json({ error: 'Missing type' });

  const viewerObj = pv.viewerProxy;

  switch(msg.type) {
    case 'subscribe': {
      const camId = msg.camera_id;
      if (!camId || !cameras.has(camId)) {
        return res.json({ type: 'error', message: 'Camera not found' });
      }
      if (!auth.canAccessCamera(pv.user, camId)) {
        return res.json({ type: 'error', message: 'Access denied' });
      }
      pv.subscribedCameras.add(camId);
      viewerObj.subscribedCameras.add(camId);
      let camViewers = viewers.get(camId);
      if (!camViewers) { camViewers = new Set(); viewers.set(camId, camViewers); }
      camViewers.add(viewerObj);
      const cam = cameras.get(camId);
      return res.json({
        type: 'subscribed',
        camera_id: camId,
        camera_name: cam?.info?.name || 'Unknown',
        camera_resolution: cam?.info?.resolution || 'unknown',
        camera_capabilities: cam?.info?.capabilities || {},
      });
    }
    case 'unsubscribe': {
      const camId = msg.camera_id;
      pv.subscribedCameras.delete(camId);
      viewerObj.subscribedCameras.delete(camId);
      const camViewers = viewers.get(camId);
      if (camViewers) camViewers.delete(viewerObj);
      pv.lastFrame.delete(camId);
      return res.json({ type: 'unsubscribed', camera_id: camId });
    }
    case 'list_cameras': {
      const cameraList = [];
      cameras.forEach((cam, id) => {
        if (auth.canAccessCamera(pv.user, id)) {
          cameraList.push({
            id,
            name: cam.info?.name || 'Unknown',
            resolution: cam.info?.resolution || 'unknown',
            viewers: viewers.get(id)?.size || 0,
            capabilities: cam.info?.capabilities || {},
            detectionLevel: cam.health?.detectionLevel ?? cam.info?.capabilities?.detection_level ?? 0,
            hardwareSummary: cam.info?.capabilities?.hardware_summary || {},
            localRecording: cam.info?.capabilities?.local_recording || false,
          });
        }
      });
      return res.json({ type: 'camera_list', cameras: cameraList });
    }
    case 'command':
    case 'ptz':
    case 'set_detection_level':
    case 'start_recording':
    case 'stop_recording':
    case 'get_remote_recordings':
    case 'get_remote_clip':
    case 'get_gpio_states':
    case 'set_gpio_output':
    case 'activate_scene':
    case 'get_storage_stats':
    case 'get_notification_settings':
    case 'update_notification_settings': {
      // Forward command to camera via its WS connection
      const camId = msg.camera_id;
      if (camId && cameras.has(camId)) {
        const cam = cameras.get(camId);
        cam.ws.send(JSON.stringify({
          type: 'viewer_command',
          command: msg.type,
          params: msg,
          viewer_id: viewerObj.id,
        }));
      }
      return res.json({ type: 'ok', message: 'Command forwarded' });
    }
    default:
      return res.json({ type: 'error', message: 'Unknown action type' });
  }
});

// ============================================================================
// WebSocket-server
// ============================================================================

const wss = new WebSocket.Server({ server });

function heartbeat() {
  this.isAlive = true;
}

const heartbeatTimer = setInterval(() => {
  wss.clients.forEach((ws) => {
    if (ws.isAlive === false) return ws.terminate();
    ws.isAlive = false;
    ws.ping();
  });
}, CONFIG.heartbeatInterval);

wss.on('close', () => clearInterval(heartbeatTimer));

wss.on('connection', (ws, req) => {
  ws.isAlive = true;
  ws.on('pong', heartbeat);
  const clientIp = req.headers['x-forwarded-for'] || req.socket.remoteAddress;
  const upgradeUrl = req.url;
  const ingressHeader = req.headers['x-ingress-path'] || '';
  
  console.log(`[WS] New connection from ${clientIp}`);
  console.log(`[WS]   URL: ${upgradeUrl}`);
  console.log(`[WS]   X-Ingress-Path: ${ingressHeader}`);
  console.log(`[WS]   Headers: ${JSON.stringify({host: req.headers.host, origin: req.headers.origin, 'user-agent': req.headers['user-agent']?.substring(0,60)})}`);

  ws.once('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      console.log(`[WS] First message from ${clientIp}: ${JSON.stringify(msg).substring(0,200)}`);
      if (msg.type === 'register_camera') {
        handleCameraRegistration(ws, msg, clientIp);
      } else if (msg.type === 'register_viewer') {
        handleViewerRegistration(ws, msg, clientIp);
      } else {
        console.log(`[WS] Unknown type: ${msg.type}`);
        ws.send(JSON.stringify({ type: 'error', message: 'Unknown type' }));
        ws.close();
      }
    } catch (err) {
      console.log(`[WS] Parse error from ${clientIp}: ${err.message}, data: ${data.toString().substring(0,200)}`);
      ws.send(JSON.stringify({ type: 'error', message: 'Invalid JSON' }));
      ws.close();
    }
  });

  ws.on('close', (code, reason) => {
    console.log(`[WS] Connection closed from ${clientIp}: code=${code} reason=${reason}`);
  });

  setTimeout(() => {
    if (!ws.role) {
      console.log(`[WS] Registration timeout for ${clientIp} (no message received in 10s)`);
      ws.send(JSON.stringify({ type: 'error', message: 'Registration timeout' }));
      ws.close();
    }
  }, 10000);
});

// ============================================================================
// Kamera-hantering
// ============================================================================

function handleCameraRegistration(ws, msg, clientIp) {
  const { secret, camera_id, name, resolution, capabilities } = msg;

  if (secret !== CONFIG.cameraSecret) {
    ws.send(JSON.stringify({ type: 'error', message: 'Auth failed' }));
    ws.close();
    return;
  }

  const cameraId = camera_id || uuidv4();

  if (cameras.has(cameraId)) {
    cameras.get(cameraId).ws.close();
  }

  ws.role = 'camera';
  ws.cameraId = cameraId;

  cameras.set(cameraId, {
    ws,
    info: {
      name: name || 'Camera',
      resolution: resolution || '640x480',
      capabilities: capabilities || {},
    },
    connectedAt: new Date().toISOString(),
    health: {},
  });

  if (!viewers.has(cameraId)) {
    viewers.set(cameraId, new Set());
  }

  console.log(`[Camera] Registered: ${cameraId} (${name}) from ${clientIp}`);

  ws.send(JSON.stringify({
    type: 'registered',
    camera_id: cameraId,
    message: 'Camera registered',
  }));

  // MQTT: Registrera kamera i HA
  mqttHA.registerCamera(cameraId, { name: name || cameraId });

  // MQTT: Registrera GPIO-pins och scener om kameran har sådana
  if (capabilities) {
    const gpioInputs = capabilities.gpio_inputs || [];
    const gpioOutputs = capabilities.gpio_outputs || [];
    const scenes = capabilities.gpio_scenes || [];
    const camInfo = { name: name || cameraId };

    gpioInputs.forEach(pin => {
      mqttHA.registerGPIOInput(cameraId, pin.name, pin, camInfo);
      console.log(`[MQTT] GPIO input registered: ${pin.name} on ${cameraId}`);
    });

    gpioOutputs.forEach(pin => {
      mqttHA.registerGPIOOutput(cameraId, pin.name, pin, camInfo);
      console.log(`[MQTT] GPIO output registered: ${pin.name} on ${cameraId}`);
    });

    scenes.forEach(scene => {
      mqttHA.registerScene(cameraId, scene.name, scene, camInfo);
      console.log(`[MQTT] Scene registered: ${scene.name} on ${cameraId}`);
    });
  }

  broadcastCameraListUpdate();

  ws.on('message', (data) => {
    if (Buffer.isBuffer(data) || data instanceof ArrayBuffer) {
      relayFrameToViewers(cameraId, data);

      if (recordings.isRecording(cameraId)) {
        recordings.saveFrame(cameraId, Buffer.from(data));
      }
    } else {
      try {
        const msg = JSON.parse(data.toString());
        handleCameraMessage(cameraId, msg);
      } catch (err) { }
    }
  });

  ws.on('close', () => {
    console.log(`[Camera] Disconnected: ${cameraId}`);

    if (recordings.isRecording(cameraId)) {
      recordings.stopRecording(cameraId);
      mqttHA.publishRecordingStatus(cameraId, false);
    }

    cameras.delete(cameraId);

    // MQTT: Markera kamera som offline
    mqttHA.unregisterCamera(cameraId);

    const cameraViewers = viewers.get(cameraId);
    if (cameraViewers) {
      cameraViewers.forEach((v) => {
        try {
          v.ws.send(JSON.stringify({ type: 'camera_disconnected', camera_id: cameraId }));
        } catch (err) { }
      });
    }

    broadcastCameraListUpdate();
  });
}

function handleCameraMessage(cameraId, msg) {
  const cam = cameras.get(cameraId);
  if (!cam) return;

  switch (msg.type) {
    case 'health':
      cam.health = {
        cpuTemp: msg.cpu_temp,
        cpuUsage: msg.cpu_usage,
        memoryUsage: msg.memory_usage,
        diskFree: msg.disk_free,
        uptime: msg.uptime,
        fps: msg.fps,
        bandwidth: msg.bandwidth,
        detectionLevel: msg.detection_level,
        storage: msg.storage || null,
        updatedAt: new Date().toISOString(),
      };
      // MQTT: Publicera FPS
      if (msg.fps) {
        mqttHA.publishFPS(cameraId, msg.fps);
      }
      break;

    case 'motion_start':
      console.log(`[Motion] Detected on ${cameraId}`);
      const eventId = recordings.createEvent(cameraId, 'motion', {
        zones: msg.zones,
        intensity: msg.intensity,
        objects: msg.objects,
      });

      // MQTT: Publicera rörelse till HA
      mqttHA.publishMotionStart(cameraId, {
        objects: msg.objects,
        intensity: msg.intensity,
      });

      if (CONFIG.recordOnMotion && !recordings.isRecording(cameraId)) {
        recordings.startRecording(cameraId, eventId);
        mqttHA.publishRecordingStatus(cameraId, true);
        console.log(`[Recording] Started for ${cameraId}`);
      }

      if (motionTimers.has(cameraId)) {
        clearTimeout(motionTimers.get(cameraId));
        motionTimers.delete(cameraId);
      }

      broadcastToViewers(cameraId, {
        type: 'motion_event',
        camera_id: cameraId,
        event: 'start',
        objects: msg.objects,
        timestamp: new Date().toISOString(),
      });
      break;

    case 'motion_end':
      console.log(`[Motion] Ended on ${cameraId}`);

      // MQTT: Publicera rörelse slut
      mqttHA.publishMotionEnd(cameraId);

      if (recordings.isRecording(cameraId)) {
        const timer = setTimeout(() => {
          if (recordings.isRecording(cameraId)) {
            const result = recordings.stopRecording(cameraId);
            mqttHA.publishRecordingStatus(cameraId, false);
            console.log(`[Recording] Stopped for ${cameraId}: ${result?.frameCount} frames`);
          }
          motionTimers.delete(cameraId);
        }, CONFIG.motionRecordDuration * 1000);
        motionTimers.set(cameraId, timer);
      }

      broadcastToViewers(cameraId, {
        type: 'motion_event',
        camera_id: cameraId,
        event: 'end',
        timestamp: new Date().toISOString(),
      });
      break;

    case 'audio_event':
      recordings.createEvent(cameraId, 'audio', { level: msg.level });
      broadcastToViewers(cameraId, {
        type: 'audio_event',
        camera_id: cameraId,
        level: msg.level,
        timestamp: new Date().toISOString(),
      });
      break;

    case 'gpio_event':
      console.log(`[GPIO] ${msg.pin_name} -> ${msg.state ? 'ON' : 'OFF'} on ${cameraId}`);
      recordings.createEvent(cameraId, 'gpio', {
        pin_name: msg.pin_name,
        pin_number: msg.pin_number,
        state: msg.state,
      });
      // MQTT: Publicera GPIO-event till HA
      mqttHA.publishGPIOEvent(cameraId, msg.pin_name, msg.state);
      // Vidarebefordra till alla viewers
      broadcastToAllViewers({
        type: 'gpio_event',
        camera_id: cameraId,
        pin_name: msg.pin_name,
        pin_number: msg.pin_number,
        direction: msg.direction,
        state: msg.state,
        timestamp: new Date().toISOString(),
      });
      break;

    case 'scene_activated':
      console.log(`[Scene] ${msg.scene_name} activated on ${cameraId}`);
      recordings.createEvent(cameraId, 'scene', {
        scene_name: msg.scene_name,
        scene_label: msg.scene_label,
      });
      broadcastToAllViewers({
        type: 'scene_activated',
        camera_id: cameraId,
        scene_name: msg.scene_name,
        scene_label: msg.scene_label,
        timestamp: new Date().toISOString(),
      });
      break;

    case 'gpio_states':
      // Pi-klienten skickar alla GPIO-tillstånd
      cam.gpioStates = msg.states;
      break;

    case 'notification_settings_response':
      // Vidarebefordra notis-inställningar till viewer som frågade
      if (msg.viewer_id && allViewers.has(msg.viewer_id)) {
        allViewers.get(msg.viewer_id).ws.send(JSON.stringify({
          type: 'notification_settings',
          camera_id: cameraId,
          settings: msg.settings,
        }));
      }
      break;
  }
}

function relayFrameToViewers(cameraId, frameData) {
  const cameraViewers = viewers.get(cameraId);
  if (!cameraViewers || cameraViewers.size === 0) return;

  const cameraIdBuf = Buffer.from(cameraId, 'utf8');
  const lengthBuf = Buffer.alloc(4);
  lengthBuf.writeUInt32BE(cameraIdBuf.length, 0);
  const taggedFrame = Buffer.concat([lengthBuf, cameraIdBuf, Buffer.from(frameData)]);

  cameraViewers.forEach((viewer) => {
    try {
      if (viewer.ws.readyState === WebSocket.OPEN) {
        viewer.ws.send(taggedFrame, { binary: true });
      }
    } catch (err) { }
  });

  // MQTT: Uppdatera viewer count
  mqttHA.publishViewerCount(cameraId, cameraViewers.size);
}

function broadcastToViewers(cameraId, message) {
  const cameraViewers = viewers.get(cameraId);
  if (!cameraViewers) return;
  const json = JSON.stringify(message);
  cameraViewers.forEach((v) => {
    try { if (v.ws.readyState === WebSocket.OPEN) v.ws.send(json); } catch (e) { }
  });
}

// ============================================================================
// Viewer-hantering
// ============================================================================

function handleViewerRegistration(ws, msg, clientIp) {
  let userPayload = null;

  if (msg.token) {
    userPayload = auth.verifyToken(msg.token);
    if (!userPayload) {
      ws.send(JSON.stringify({ type: 'error', message: 'Invalid token' }));
      ws.close();
      return;
    }
  } else if (msg.ha_ingress === true) {
    // HA Ingress-autentiserad viewer (HA hanterar auth)
    userPayload = { userId: 0, username: 'ha_user', role: 'admin', allowedCameras: '*' };
  } else if (msg.secret) {
    const viewerSecret = process.env.VIEWER_SECRET || CONFIG.cameraSecret;
    if (msg.secret !== viewerSecret) {
      ws.send(JSON.stringify({ type: 'error', message: 'Auth failed' }));
      ws.close();
      return;
    }
    userPayload = { userId: 0, username: 'legacy', role: 'user', allowedCameras: '*' };
  } else {
    ws.send(JSON.stringify({ type: 'error', message: 'No credentials' }));
    ws.close();
    return;
  }

  const viewerId = uuidv4();
  ws.role = 'viewer';
  ws.viewerId = viewerId;

  const subscribedCameras = new Set();

  const viewerObj = {
    ws,
    id: viewerId,
    user: userPayload,
    connectedAt: new Date().toISOString(),
    subscribedCameras,
  };

  allViewers.set(viewerId, viewerObj);

  const allCamerasList = [];
  cameras.forEach((cam, id) => {
    if (auth.canAccessCamera(userPayload, id)) {
      allCamerasList.push({
        id,
        name: cam.info?.name || 'Unknown',
        resolution: cam.info?.resolution || 'unknown',
        viewers: viewers.get(id)?.size || 0,
        capabilities: cam.info?.capabilities || {},
      });
    }
  });

  ws.send(JSON.stringify({
    type: 'connected',
    viewer_id: viewerId,
    user: { username: userPayload.username, role: userPayload.role },
    subscribed_cameras: [],
    available_cameras: allCamerasList,
  }));

  console.log(`[Viewer] ${userPayload.username} (${viewerId}) connected from ${clientIp}`);

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());

      switch (msg.type) {
        case 'subscribe':
          handleViewerSubscribe(viewerObj, msg);
          break;
        case 'unsubscribe':
          handleViewerUnsubscribe(viewerObj, msg);
          break;
        case 'list_cameras':
          handleListCameras(viewerObj);
          break;
        case 'command':
          handleViewerCommand(viewerObj, msg);
          break;
        case 'ptz':
          handlePTZCommand(viewerObj, msg);
          break;
        case 'start_recording':
          handleManualRecording(viewerObj, msg, true);
          break;
        case 'stop_recording':
          handleManualRecording(viewerObj, msg, false);
          break;
        case 'set_detection_level':
          handleSetDetectionLevel(viewerObj, msg);
          break;
        case 'get_remote_recordings':
          handleGetRemoteRecordings(viewerObj, msg);
          break;
        case 'get_remote_clip':
          handleGetRemoteClip(viewerObj, msg);
          break;
        case 'get_remote_thumbnail':
          handleGetRemoteThumbnail(viewerObj, msg);
          break;
        case 'get_storage_stats':
          handleGetStorageStats(viewerObj, msg);
          break;
        case 'set_gpio_output':
          handleSetGPIOOutput(viewerObj, msg);
          break;
        case 'activate_scene':
          handleActivateScene(viewerObj, msg);
          break;
        case 'get_gpio_states':
          handleGetGPIOStates(viewerObj, msg);
          break;
        case 'update_notification_settings':
          handleUpdateNotificationSettings(viewerObj, msg);
          break;
        case 'get_notification_settings':
          handleGetNotificationSettings(viewerObj, msg);
          break;
      }
    } catch (err) { }
  });

  ws.on('close', () => {
    subscribedCameras.forEach(camId => {
      const camViewers = viewers.get(camId);
      if (camViewers) camViewers.delete(viewerObj);
    });
    allViewers.delete(viewerId);
  });
}

function handleViewerSubscribe(viewerObj, msg) {
  const { camera_id } = msg;
  if (!camera_id || !cameras.has(camera_id)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: `Camera '${camera_id}' not available` }));
    return;
  }

  if (!auth.canAccessCamera(viewerObj.user, camera_id)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'Access denied' }));
    return;
  }

  if (viewerObj.subscribedCameras.has(camera_id)) return;

  const camViewers = viewers.get(camera_id) || new Set();
  if (camViewers.size >= CONFIG.maxViewersPerCamera) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'Max viewers reached' }));
    return;
  }

  viewerObj.subscribedCameras.add(camera_id);
  camViewers.add(viewerObj);
  viewers.set(camera_id, camViewers);

  const cam = cameras.get(camera_id);
  viewerObj.ws.send(JSON.stringify({
    type: 'subscribed',
    camera_id,
    camera_name: cam?.info?.name || 'Unknown',
    camera_resolution: cam?.info?.resolution || 'unknown',
    camera_capabilities: cam?.info?.capabilities || {},
  }));
}

function handleViewerUnsubscribe(viewerObj, msg) {
  const { camera_id } = msg;
  if (!camera_id) return;

  viewerObj.subscribedCameras.delete(camera_id);
  const camViewers = viewers.get(camera_id);
  if (camViewers) camViewers.delete(viewerObj);

  viewerObj.ws.send(JSON.stringify({ type: 'unsubscribed', camera_id }));
}

function handleListCameras(viewerObj) {
  const cameraList = [];
  cameras.forEach((cam, id) => {
    if (auth.canAccessCamera(viewerObj.user, id)) {
      cameraList.push({
        id,
        name: cam.info?.name || 'Unknown',
        resolution: cam.info?.resolution || 'unknown',
        viewers: viewers.get(id)?.size || 0,
        capabilities: cam.info?.capabilities || {},
        detectionLevel: cam.health?.detectionLevel ?? cam.info?.capabilities?.detection_level ?? 0,
        hardwareSummary: cam.info?.capabilities?.hardware_summary || {},
        localRecording: cam.info?.capabilities?.local_recording || false,
      });
    }
  });
  viewerObj.ws.send(JSON.stringify({ type: 'camera_list', cameras: cameraList }));
}

function handleViewerCommand(viewerObj, msg) {
  const { camera_id, command, params } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) return;

  cameras.get(camera_id).ws.send(JSON.stringify({
    type: 'viewer_command',
    viewer_id: viewerObj.id,
    command,
    params,
  }));
}

// ── Detekteringsnivå-styrning ──────────────────────────────────────

function handleSetDetectionLevel(viewerObj, msg) {
  const { camera_id, level, config } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'Access denied' }));
    return;
  }

  const cam = cameras.get(camera_id);
  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'set_detection_level',
    params: { level, config },
  }));

  console.log(`[Detection] Level set to ${level} for ${camera_id} by ${viewerObj.user.username}`);
  viewerObj.ws.send(JSON.stringify({
    type: 'detection_level_update',
    camera_id,
    level,
  }));
}

// ── On-demand inspelningshämtning (från Pi-klient) ─────────────────

function handleGetRemoteRecordings(viewerObj, msg) {
  const { camera_id, date, limit } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canAccessCamera(viewerObj.user, camera_id)) return;

  const requestId = msg.request_id || Date.now().toString();
  const cam = cameras.get(camera_id);

  // Skicka förfrågan till Pi-klienten
  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'get_recordings',
    params: { camera_id, date, limit: limit || 50 },
    request_id: requestId,
  }));

  // Lyssna efter svar från Pi-klienten och vidarebefordra till viewer
  const handler = (data) => {
    try {
      const response = JSON.parse(data.toString());
      if (response.type === 'recordings_list' && response.request_id === requestId) {
        viewerObj.ws.send(JSON.stringify({
          type: 'remote_recordings',
          camera_id,
          recordings: response.recordings,
          request_id: requestId,
        }));
        cam.ws.removeListener('message', handler);
      }
    } catch (e) {}
  };
  cam.ws.on('message', handler);
  // Timeout
  setTimeout(() => cam.ws.removeListener('message', handler), 15000);
}

function handleGetRemoteClip(viewerObj, msg) {
  const { camera_id, clip_path, start_frame, max_frames } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canAccessCamera(viewerObj.user, camera_id)) return;

  const requestId = msg.request_id || Date.now().toString();
  const cam = cameras.get(camera_id);

  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'get_recording_clip',
    params: { clip_path, start_frame: start_frame || 0, max_frames: max_frames || 100 },
    request_id: requestId,
  }));

  const handler = (data) => {
    try {
      const response = JSON.parse(data.toString());
      if (response.type === 'recording_clip' && response.request_id === requestId) {
        viewerObj.ws.send(JSON.stringify({
          type: 'remote_clip',
          camera_id,
          clip_path: response.clip_path,
          frames: response.frames,
          request_id: requestId,
        }));
        cam.ws.removeListener('message', handler);
      }
    } catch (e) {}
  };
  cam.ws.on('message', handler);
  setTimeout(() => cam.ws.removeListener('message', handler), 30000);
}

function handleGetRemoteThumbnail(viewerObj, msg) {
  const { camera_id, clip_path } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canAccessCamera(viewerObj.user, camera_id)) return;

  const requestId = msg.request_id || Date.now().toString();
  const cam = cameras.get(camera_id);

  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'get_thumbnail',
    params: { clip_path },
    request_id: requestId,
  }));

  const handler = (data) => {
    try {
      const response = JSON.parse(data.toString());
      if (response.type === 'recording_thumbnail' && response.request_id === requestId) {
        viewerObj.ws.send(JSON.stringify({
          type: 'remote_thumbnail',
          camera_id,
          clip_path: response.clip_path,
          data: response.data,
          request_id: requestId,
        }));
        cam.ws.removeListener('message', handler);
      }
    } catch (e) {}
  };
  cam.ws.on('message', handler);
  setTimeout(() => cam.ws.removeListener('message', handler), 15000);
}

function handleGetStorageStats(viewerObj, msg) {
  const { camera_id } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canAccessCamera(viewerObj.user, camera_id)) return;

  const requestId = msg.request_id || Date.now().toString();
  const cam = cameras.get(camera_id);

  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'get_storage_stats',
    params: {},
    request_id: requestId,
  }));

  const handler = (data) => {
    try {
      const response = JSON.parse(data.toString());
      if (response.type === 'storage_stats' && response.request_id === requestId) {
        viewerObj.ws.send(JSON.stringify({
          type: 'remote_storage_stats',
          camera_id,
          stats: response.stats,
          request_id: requestId,
        }));
        cam.ws.removeListener('message', handler);
      }
    } catch (e) {}
  };
  cam.ws.on('message', handler);
  setTimeout(() => cam.ws.removeListener('message', handler), 15000);
}

function handlePTZCommand(viewerObj, msg) {
  const { camera_id, action, params } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'PTZ access denied' }));
    return;
  }

  const cam = cameras.get(camera_id);
  cam.ws.send(JSON.stringify({
    type: 'ptz_command',
    action,
    params: params || {},
  }));
}

function handleManualRecording(viewerObj, msg, start) {
  const { camera_id } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) return;

  if (start && !recordings.isRecording(camera_id)) {
    const eventId = recordings.createEvent(camera_id, 'manual_recording', {
      user: viewerObj.user.username,
    });
    recordings.startRecording(camera_id, eventId);
    mqttHA.publishRecordingStatus(camera_id, true);
    viewerObj.ws.send(JSON.stringify({ type: 'recording_started', camera_id }));
  } else if (!start && recordings.isRecording(camera_id)) {
    recordings.stopRecording(camera_id);
    mqttHA.publishRecordingStatus(camera_id, false);
    viewerObj.ws.send(JSON.stringify({ type: 'recording_stopped', camera_id }));
  }
}

function broadcastCameraListUpdate() {
  allViewers.forEach((viewer) => {
    handleListCameras(viewer);
  });
}

function broadcastToAllViewers(message) {
  const json = JSON.stringify(message);
  allViewers.forEach((v) => {
    try { if (v.ws.readyState === WebSocket.OPEN) v.ws.send(json); } catch (e) { }
  });
}

// ── GPIO-hantering ──────────────────────────────────────────────────

function handleSetGPIOOutput(viewerObj, msg) {
  const { camera_id, output_name, state } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'GPIO access denied' }));
    return;
  }

  const cam = cameras.get(camera_id);
  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'set_gpio_output',
    params: { output_name, state },
  }));

  console.log(`[GPIO] Output '${output_name}' set to ${state} on ${camera_id} by ${viewerObj.user.username}`);
  viewerObj.ws.send(JSON.stringify({
    type: 'gpio_output_set',
    camera_id,
    output_name,
    state,
  }));
}

function handleActivateScene(viewerObj, msg) {
  const { camera_id, scene_name } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'Scene access denied' }));
    return;
  }

  const cam = cameras.get(camera_id);
  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'activate_scene',
    params: { scene_name },
  }));

  console.log(`[Scene] '${scene_name}' activated on ${camera_id} by ${viewerObj.user.username}`);
}

function handleGetGPIOStates(viewerObj, msg) {
  const { camera_id } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;

  const cam = cameras.get(camera_id);
  const requestId = msg.request_id || Date.now().toString();

  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'get_gpio_states',
    params: {},
    request_id: requestId,
  }));

  const handler = (data) => {
    try {
      const response = JSON.parse(data.toString());
      if (response.type === 'gpio_states' && response.request_id === requestId) {
        viewerObj.ws.send(JSON.stringify({
          type: 'gpio_states_response',
          camera_id,
          states: response.states,
          request_id: requestId,
        }));
        cam.ws.removeListener('message', handler);
      }
    } catch (e) {}
  };
  cam.ws.on('message', handler);
  setTimeout(() => cam.ws.removeListener('message', handler), 10000);
}

// ── Notis-inställningar ─────────────────────────────────────────────

function handleUpdateNotificationSettings(viewerObj, msg) {
  const { camera_id, settings } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;
  if (!auth.canControlCamera(viewerObj.user)) {
    viewerObj.ws.send(JSON.stringify({ type: 'error', message: 'Access denied' }));
    return;
  }

  const cam = cameras.get(camera_id);
  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'update_notification_settings',
    params: { camera_id, settings },
    viewer_id: viewerObj.id,
  }));

  console.log(`[Notifications] Settings updated for ${camera_id} by ${viewerObj.user.username}`);
}

function handleGetNotificationSettings(viewerObj, msg) {
  const { camera_id } = msg;
  if (!camera_id || !cameras.has(camera_id)) return;

  const cam = cameras.get(camera_id);
  cam.ws.send(JSON.stringify({
    type: 'viewer_command',
    command: 'get_notification_settings',
    params: { camera_id },
    viewer_id: viewerObj.id,
  }));
}

// ============================================================================
// Cleanup scheduler
// ============================================================================

setInterval(() => {
  const deleted = recordings.cleanupOldRecordings(CONFIG.cleanupMaxAgeDays);
  if (deleted > 0) {
    console.log(`[Cleanup] Deleted ${deleted} old recordings`);
  }

  // Kontrollera maxstorlek
  const stats = recordings.getStorageStats();
  if (stats.totalSizeMB > CONFIG.maxRecordingSizeMB) {
    console.log(`[Cleanup] Storage ${stats.totalSizeMB}MB exceeds max ${CONFIG.maxRecordingSizeMB}MB, cleaning...`);
    recordings.cleanupOldRecordings(1); // Rensa allt äldre än 1 dag
  }
}, 60 * 60 * 1000); // Varje timme

// ============================================================================
// Starta servern
// ============================================================================

// ============================================================================
// MQTT -> WebSocket bridge: HA kan styra GPIO-utgångar och scener via MQTT
// ============================================================================

mqttHA.onGPIOCommand((cameraId, pinName, state) => {
  const cam = cameras.get(cameraId);
  if (!cam) {
    console.log(`[MQTT->WS] Camera ${cameraId} not connected, ignoring GPIO command`);
    return;
  }

  console.log(`[MQTT->WS] HA sets GPIO '${pinName}' to ${state ? 'ON' : 'OFF'} on ${cameraId}`);
  cam.ws.send(JSON.stringify({
    type: 'gpio_command',
    command: 'set_output',
    params: { name: pinName, state: state },
  }));
});

mqttHA.onSceneCommand((cameraId, sceneName) => {
  const cam = cameras.get(cameraId);
  if (!cam) {
    console.log(`[MQTT->WS] Camera ${cameraId} not connected, ignoring scene command`);
    return;
  }

  console.log(`[MQTT->WS] HA activates scene '${sceneName}' on ${cameraId}`);
  cam.ws.send(JSON.stringify({
    type: 'gpio_command',
    command: 'activate_scene',
    params: { scene: sceneName },
  }));
});

server.listen(CONFIG.port, '0.0.0.0', () => {
  console.log('');
  console.log(`Server running on port ${CONFIG.port}`);
  if (IS_HA_ADDON) {
    console.log(`  Mode:      Home Assistant Add-on`);
    console.log(`  Ingress:   ${INGRESS_PATH}`);
    console.log(`  MQTT:      ${mqttHA.enabled ? 'enabled' : 'disabled'}`);
  }
  console.log(`  Viewer:    http://localhost:${CONFIG.port}/viewer/`);
  console.log(`  API:       http://localhost:${CONFIG.port}/api/health`);
  console.log('');
});

// ── Direct WebSocket port (8765) for external/Cloudflare access ──────
const DIRECT_PORT = 8765;
const SSL_DIR = '/ssl';

// Auto-detect SSL certificates
function findSSLCerts() {
  const certNames = [
    { cert: 'fullchain.pem', key: 'privkey.pem' },
    { cert: 'origin.pem', key: 'privkey.pem' },
    { cert: 'certificate.pem', key: 'private_key.pem' },
    { cert: 'cert.pem', key: 'key.pem' },
    { cert: 'server.crt', key: 'server.key' },
    { cert: 'tls.crt', key: 'tls.key' },
    { cert: 'ssl.crt', key: 'ssl.key' },
  ];
  
  for (const pair of certNames) {
    const certPath = path.join(SSL_DIR, pair.cert);
    const keyPath = path.join(SSL_DIR, pair.key);
    if (fs.existsSync(certPath) && fs.existsSync(keyPath)) {
      console.log(`[SSL] Found certificates: ${pair.cert} + ${pair.key}`);
      return { cert: certPath, key: keyPath };
    }
  }
  
  // Also check for any .pem files as fallback
  try {
    const files = fs.readdirSync(SSL_DIR);
    console.log(`[SSL] Files in ${SSL_DIR}: ${files.join(', ')}`);
  } catch(e) {
    console.log(`[SSL] Cannot read ${SSL_DIR}: ${e.message}`);
  }
  
  return null;
}

const sslCerts = findSSLCerts();
let directServer;

if (sslCerts) {
  const sslOptions = {
    cert: fs.readFileSync(sslCerts.cert),
    key: fs.readFileSync(sslCerts.key),
  };
  directServer = https.createServer(sslOptions, app);
  console.log(`[SSL] Direct port ${DIRECT_PORT} will use HTTPS/WSS`);
} else {
  directServer = http.createServer(app);
  console.log(`[SSL] No certificates found in ${SSL_DIR}, direct port ${DIRECT_PORT} will use HTTP/WS`);
}

const directWss = new WebSocket.Server({ server: directServer });

// Share the same connection handler as the main WSS
directWss.on('connection', (ws, req) => {
  ws.isAlive = true;
  ws.on('pong', heartbeat);
  const clientIp = req.headers['x-forwarded-for'] || req.socket.remoteAddress;
  
  console.log(`[WS:${DIRECT_PORT}] New direct connection from ${clientIp}`);

  ws.once('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      console.log(`[WS:${DIRECT_PORT}] First message from ${clientIp}: ${JSON.stringify(msg).substring(0,200)}`);
      if (msg.type === 'register_camera') {
        handleCameraRegistration(ws, msg, clientIp);
      } else if (msg.type === 'register_viewer') {
        handleViewerRegistration(ws, msg, clientIp);
      } else {
        ws.send(JSON.stringify({ type: 'error', message: 'Unknown type' }));
        ws.close();
      }
    } catch (err) {
      ws.send(JSON.stringify({ type: 'error', message: 'Invalid JSON' }));
      ws.close();
    }
  });

  ws.on('close', (code, reason) => {
    console.log(`[WS:${DIRECT_PORT}] Connection closed from ${clientIp}: code=${code}`);
  });

  setTimeout(() => {
    if (!ws.role) {
      ws.send(JSON.stringify({ type: 'error', message: 'Registration timeout' }));
      ws.close();
    }
  }, 10000);
});

// Heartbeat for direct WSS
const directHeartbeat = setInterval(() => {
  directWss.clients.forEach((ws) => {
    if (!ws.isAlive) return ws.terminate();
    ws.isAlive = false;
    ws.ping();
  });
}, CONFIG.heartbeatInterval);
directWss.on('close', () => clearInterval(directHeartbeat));

directServer.listen(DIRECT_PORT, '0.0.0.0', () => {
  console.log(`Direct ${sslCerts ? 'HTTPS/WSS' : 'HTTP/WS'} server on port ${DIRECT_PORT}`);
  if (CONFIG.directWsUrl) {
    console.log(`  External URL: ${CONFIG.directWsUrl}`);
  }
});

process.on('SIGTERM', () => {
  console.log('[Server] Shutting down...');
  mqttHA.close();
  recordings.close();
  auth.close();
  wss.clients.forEach((ws) => ws.close());
  directWss.clients.forEach((ws) => ws.close());
  directServer.close();
  server.close(() => process.exit(0));
});

process.on('SIGINT', () => {
  console.log('[Server] Shutting down...');
  mqttHA.close();
  recordings.close();
  auth.close();
  wss.clients.forEach((ws) => ws.close());
  directWss.clients.forEach((ws) => ws.close());
  directServer.close();
  server.close(() => process.exit(0));
});
