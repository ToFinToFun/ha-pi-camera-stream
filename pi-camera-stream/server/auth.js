/**
 * Auth Module – JWT-baserad autentisering med användarkonton och behörigheter
 * 
 * Roller:
 *   admin  – Full tillgång: hantera användare, se alla kameror, ändra inställningar
 *   user   – Se tilldelade kameror, ta snapshots
 *   guest  – Se tilldelade kameror (read-only, ingen PTZ/inspelning)
 * 
 * Lagring: SQLite (better-sqlite3) – enkel, filbaserad, ingen extern databas behövs.
 */

const Database = require('better-sqlite3');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const path = require('path');
const fs = require('fs');

class AuthManager {
  constructor(options = {}) {
    const dbDir = options.dbPath || path.join(__dirname, '..', 'data', 'db');
    if (!fs.existsSync(dbDir)) {
      fs.mkdirSync(dbDir, { recursive: true });
    }

    this.db = new Database(path.join(dbDir, 'users.db'));
    this.jwtSecret = options.jwtSecret || process.env.JWT_SECRET || 'default-jwt-secret';
    this.tokenExpiry = options.tokenExpiry || '24h';

    this._initDb();
  }

  _initDb() {
    this.db.pragma('journal_mode = WAL');

    this.db.exec(`
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        display_name TEXT,
        allowed_cameras TEXT DEFAULT '*',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        last_login TEXT,
        active INTEGER DEFAULT 1
      );

      CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        details TEXT,
        ip_address TEXT,
        created_at TEXT DEFAULT (datetime('now'))
      );
    `);

    // Skapa default admin om inga användare finns
    const count = this.db.prepare('SELECT COUNT(*) as count FROM users').get();
    if (count.count === 0) {
      this.createUser({
        username: 'admin',
        password: 'admin',
        role: 'admin',
        displayName: 'Administrator',
        allowedCameras: '*',
      });
      console.log('[Auth] Default admin user created (username: admin, password: admin)');
      console.log('[Auth] IMPORTANT: Change the default password immediately!');
    }
  }

  // ── Användarhantering ──────────────────────────────────────────────

  createUser({ username, password, role = 'user', displayName = '', allowedCameras = '*' }) {
    const hash = bcrypt.hashSync(password, 10);
    const stmt = this.db.prepare(`
      INSERT INTO users (username, password_hash, role, display_name, allowed_cameras)
      VALUES (?, ?, ?, ?, ?)
    `);

    try {
      const result = stmt.run(username, hash, role, displayName || username, allowedCameras);
      return { id: result.lastInsertRowid, username, role, displayName: displayName || username };
    } catch (err) {
      if (err.message.includes('UNIQUE')) {
        throw new Error(`Username '${username}' already exists`);
      }
      throw err;
    }
  }

  updateUser(userId, updates) {
    const fields = [];
    const values = [];

    if (updates.displayName !== undefined) {
      fields.push('display_name = ?');
      values.push(updates.displayName);
    }
    if (updates.role !== undefined) {
      fields.push('role = ?');
      values.push(updates.role);
    }
    if (updates.allowedCameras !== undefined) {
      fields.push('allowed_cameras = ?');
      values.push(updates.allowedCameras);
    }
    if (updates.active !== undefined) {
      fields.push('active = ?');
      values.push(updates.active ? 1 : 0);
    }
    if (updates.password) {
      fields.push('password_hash = ?');
      values.push(bcrypt.hashSync(updates.password, 10));
    }

    fields.push("updated_at = datetime('now')");
    values.push(userId);

    const stmt = this.db.prepare(`UPDATE users SET ${fields.join(', ')} WHERE id = ?`);
    return stmt.run(...values);
  }

  deleteUser(userId) {
    return this.db.prepare('DELETE FROM users WHERE id = ?').run(userId);
  }

  getUser(userId) {
    const user = this.db.prepare('SELECT * FROM users WHERE id = ?').get(userId);
    if (user) delete user.password_hash;
    return user;
  }

  getUserByUsername(username) {
    return this.db.prepare('SELECT * FROM users WHERE username = ?').get(username);
  }

  listUsers() {
    return this.db.prepare(
      'SELECT id, username, role, display_name, allowed_cameras, created_at, last_login, active FROM users ORDER BY id'
    ).all();
  }

  // ── Autentisering ──────────────────────────────────────────────────

  login(username, password) {
    const user = this.getUserByUsername(username);
    if (!user || !user.active) {
      return null;
    }

    if (!bcrypt.compareSync(password, user.password_hash)) {
      return null;
    }

    // Uppdatera last_login
    this.db.prepare("UPDATE users SET last_login = datetime('now') WHERE id = ?").run(user.id);

    const token = jwt.sign(
      {
        userId: user.id,
        username: user.username,
        role: user.role,
        allowedCameras: user.allowed_cameras,
      },
      this.jwtSecret,
      { expiresIn: this.tokenExpiry }
    );

    return {
      token,
      user: {
        id: user.id,
        username: user.username,
        role: user.role,
        displayName: user.display_name,
        allowedCameras: user.allowed_cameras,
      },
    };
  }

  verifyToken(token) {
    try {
      return jwt.verify(token, this.jwtSecret);
    } catch (err) {
      return null;
    }
  }

  // ── Behörighetskontroll ────────────────────────────────────────────

  canAccessCamera(userPayload, cameraId) {
    if (!userPayload) return false;
    if (userPayload.role === 'admin') return true;
    if (userPayload.allowedCameras === '*') return true;

    const allowed = userPayload.allowedCameras.split(',').map(s => s.trim());
    return allowed.includes(cameraId);
  }

  canControlCamera(userPayload) {
    if (!userPayload) return false;
    return userPayload.role === 'admin' || userPayload.role === 'user';
  }

  canManageUsers(userPayload) {
    if (!userPayload) return false;
    return userPayload.role === 'admin';
  }

  // ── Audit log ──────────────────────────────────────────────────────

  logAction(userId, action, details = '', ipAddress = '') {
    this.db.prepare(
      'INSERT INTO audit_log (user_id, action, details, ip_address) VALUES (?, ?, ?, ?)'
    ).run(userId, action, details, ipAddress);
  }

  getAuditLog(limit = 100) {
    return this.db.prepare(`
      SELECT a.*, u.username
      FROM audit_log a
      LEFT JOIN users u ON a.user_id = u.id
      ORDER BY a.created_at DESC
      LIMIT ?
    `).all(limit);
  }

  close() {
    this.db.close();
  }
}

module.exports = AuthManager;
