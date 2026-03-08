/**
 * Recordings Module – Inspelning och tidslinje
 * 
 * Hanterar lagring av video-frames och rörelsedetekterings-events.
 * Sparar JPEG-frames till disk organiserade per kamera och dag.
 * SQLite-databas för metadata och snabb sökning.
 */

const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');

class RecordingsManager {
  constructor(options = {}) {
    this.recordingsDir = options.recordingsDir || path.join(__dirname, '..', 'data', 'recordings');
    const dbDir = options.dbPath || path.join(__dirname, '..', 'data', 'db');

    if (!fs.existsSync(this.recordingsDir)) {
      fs.mkdirSync(this.recordingsDir, { recursive: true });
    }
    if (!fs.existsSync(dbDir)) {
      fs.mkdirSync(dbDir, { recursive: true });
    }

    this.db = new Database(path.join(dbDir, 'recordings.db'));
    this._initDb();

    // Aktiva inspelningssessioner: Map<cameraId, { active, startedAt, frameCount }>
    this.activeSessions = new Map();
  }

  _initDb() {
    this.db.pragma('journal_mode = WAL');

    this.db.exec(`
      CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        camera_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp TEXT DEFAULT (datetime('now')),
        duration_seconds REAL DEFAULT 0,
        frame_count INTEGER DEFAULT 0,
        thumbnail_path TEXT,
        metadata TEXT,
        created_at TEXT DEFAULT (datetime('now'))
      );

      CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id);
      CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
      CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

      CREATE TABLE IF NOT EXISTS recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        camera_id TEXT NOT NULL,
        event_id INTEGER,
        start_time TEXT NOT NULL,
        end_time TEXT,
        frame_count INTEGER DEFAULT 0,
        file_path TEXT,
        file_size INTEGER DEFAULT 0,
        status TEXT DEFAULT 'recording',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (event_id) REFERENCES events(id)
      );

      CREATE INDEX IF NOT EXISTS idx_recordings_camera ON recordings(camera_id);
      CREATE INDEX IF NOT EXISTS idx_recordings_time ON recordings(start_time);
    `);
  }

  // ── Event-hantering ────────────────────────────────────────────────

  createEvent(cameraId, eventType, metadata = {}) {
    const stmt = this.db.prepare(`
      INSERT INTO events (camera_id, event_type, metadata)
      VALUES (?, ?, ?)
    `);
    const result = stmt.run(cameraId, eventType, JSON.stringify(metadata));
    return result.lastInsertRowid;
  }

  updateEvent(eventId, updates) {
    const fields = [];
    const values = [];

    if (updates.durationSeconds !== undefined) {
      fields.push('duration_seconds = ?');
      values.push(updates.durationSeconds);
    }
    if (updates.frameCount !== undefined) {
      fields.push('frame_count = ?');
      values.push(updates.frameCount);
    }
    if (updates.thumbnailPath !== undefined) {
      fields.push('thumbnail_path = ?');
      values.push(updates.thumbnailPath);
    }

    values.push(eventId);
    this.db.prepare(`UPDATE events SET ${fields.join(', ')} WHERE id = ?`).run(...values);
  }

  getEvents(cameraId, options = {}) {
    let query = 'SELECT * FROM events WHERE camera_id = ?';
    const params = [cameraId];

    if (options.from) {
      query += ' AND timestamp >= ?';
      params.push(options.from);
    }
    if (options.to) {
      query += ' AND timestamp <= ?';
      params.push(options.to);
    }
    if (options.type) {
      query += ' AND event_type = ?';
      params.push(options.type);
    }

    query += ' ORDER BY timestamp DESC';

    if (options.limit) {
      query += ' LIMIT ?';
      params.push(options.limit);
    }

    return this.db.prepare(query).all(...params);
  }

  // ── Inspelning ─────────────────────────────────────────────────────

  startRecording(cameraId, eventId = null) {
    const now = new Date().toISOString();
    const dateStr = now.slice(0, 10);
    const timeStr = now.slice(11, 19).replace(/:/g, '-');

    const dirPath = path.join(this.recordingsDir, cameraId, dateStr);
    if (!fs.existsSync(dirPath)) {
      fs.mkdirSync(dirPath, { recursive: true });
    }

    const filePath = path.join(dirPath, `rec_${timeStr}`);
    fs.mkdirSync(filePath, { recursive: true });

    const stmt = this.db.prepare(`
      INSERT INTO recordings (camera_id, event_id, start_time, file_path, status)
      VALUES (?, ?, ?, ?, 'recording')
    `);
    const result = stmt.run(cameraId, eventId, now, filePath);

    this.activeSessions.set(cameraId, {
      recordingId: result.lastInsertRowid,
      filePath,
      startedAt: Date.now(),
      frameCount: 0,
    });

    return result.lastInsertRowid;
  }

  saveFrame(cameraId, frameData) {
    const session = this.activeSessions.get(cameraId);
    if (!session) return false;

    session.frameCount++;
    const framePath = path.join(session.filePath, `frame_${String(session.frameCount).padStart(6, '0')}.jpg`);

    try {
      fs.writeFileSync(framePath, frameData);

      // Spara första framen som thumbnail
      if (session.frameCount === 1) {
        const thumbPath = path.join(session.filePath, 'thumbnail.jpg');
        fs.writeFileSync(thumbPath, frameData);
      }

      return true;
    } catch (err) {
      console.error(`[Recording] Error saving frame: ${err.message}`);
      return false;
    }
  }

  stopRecording(cameraId) {
    const session = this.activeSessions.get(cameraId);
    if (!session) return;

    const now = new Date().toISOString();
    const durationMs = Date.now() - session.startedAt;

    // Beräkna filstorlek
    let totalSize = 0;
    try {
      const files = fs.readdirSync(session.filePath);
      files.forEach(f => {
        const stat = fs.statSync(path.join(session.filePath, f));
        totalSize += stat.size;
      });
    } catch (err) { }

    this.db.prepare(`
      UPDATE recordings SET end_time = ?, frame_count = ?, file_size = ?, status = 'complete'
      WHERE id = ?
    `).run(now, session.frameCount, totalSize, session.recordingId);

    this.activeSessions.delete(cameraId);

    return {
      recordingId: session.recordingId,
      frameCount: session.frameCount,
      durationMs,
      totalSize,
    };
  }

  isRecording(cameraId) {
    return this.activeSessions.has(cameraId);
  }

  // ── Tidslinje / Hämta inspelningar ─────────────────────────────────

  getRecordings(cameraId, options = {}) {
    let query = 'SELECT * FROM recordings WHERE camera_id = ?';
    const params = [cameraId];

    if (options.from) {
      query += ' AND start_time >= ?';
      params.push(options.from);
    }
    if (options.to) {
      query += ' AND start_time <= ?';
      params.push(options.to);
    }
    if (options.status) {
      query += ' AND status = ?';
      params.push(options.status);
    }

    query += ' ORDER BY start_time DESC';

    if (options.limit) {
      query += ' LIMIT ?';
      params.push(options.limit);
    }

    return this.db.prepare(query).all(...params);
  }

  getTimeline(cameraId, date) {
    // Returnera alla events och inspelningar för en specifik dag
    const from = `${date} 00:00:00`;
    const to = `${date} 23:59:59`;

    const events = this.db.prepare(
      'SELECT * FROM events WHERE camera_id = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp'
    ).all(cameraId, from, to);

    const recordings = this.db.prepare(
      'SELECT * FROM recordings WHERE camera_id = ? AND start_time BETWEEN ? AND ? ORDER BY start_time'
    ).all(cameraId, from, to);

    return { events, recordings };
  }

  getThumbnail(recordingId) {
    const rec = this.db.prepare('SELECT file_path FROM recordings WHERE id = ?').get(recordingId);
    if (!rec) return null;

    const thumbPath = path.join(rec.file_path, 'thumbnail.jpg');
    if (fs.existsSync(thumbPath)) {
      return thumbPath;
    }
    return null;
  }

  getRecordingFrames(recordingId) {
    const rec = this.db.prepare('SELECT * FROM recordings WHERE id = ?').get(recordingId);
    if (!rec || !rec.file_path) return null;

    try {
      const files = fs.readdirSync(rec.file_path)
        .filter(f => f.startsWith('frame_') && f.endsWith('.jpg'))
        .sort();
      return {
        recording: rec,
        frames: files.map(f => path.join(rec.file_path, f)),
      };
    } catch (err) {
      return null;
    }
  }

  // ── Cleanup ────────────────────────────────────────────────────────

  cleanupOldRecordings(maxAgeDays = 30) {
    const cutoff = new Date(Date.now() - maxAgeDays * 24 * 60 * 60 * 1000).toISOString();

    const oldRecordings = this.db.prepare(
      'SELECT * FROM recordings WHERE start_time < ? AND status = ?'
    ).all(cutoff, 'complete');

    let deletedCount = 0;
    oldRecordings.forEach(rec => {
      try {
        if (rec.file_path && fs.existsSync(rec.file_path)) {
          fs.rmSync(rec.file_path, { recursive: true, force: true });
        }
        this.db.prepare('DELETE FROM recordings WHERE id = ?').run(rec.id);
        deletedCount++;
      } catch (err) {
        console.error(`[Recording] Cleanup error: ${err.message}`);
      }
    });

    // Rensa gamla events
    this.db.prepare('DELETE FROM events WHERE timestamp < ?').run(cutoff);

    return deletedCount;
  }

  getStorageStats() {
    const totalRecordings = this.db.prepare('SELECT COUNT(*) as count FROM recordings').get().count;
    const totalEvents = this.db.prepare('SELECT COUNT(*) as count FROM events').get().count;
    const totalSize = this.db.prepare('SELECT COALESCE(SUM(file_size), 0) as total FROM recordings').get().total;

    return {
      totalRecordings,
      totalEvents,
      totalSizeBytes: totalSize,
      totalSizeMB: Math.round(totalSize / 1024 / 1024 * 10) / 10,
    };
  }

  close() {
    // Stoppa alla aktiva inspelningar
    this.activeSessions.forEach((session, cameraId) => {
      this.stopRecording(cameraId);
    });
    this.db.close();
  }
}

module.exports = RecordingsManager;
