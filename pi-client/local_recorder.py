"""
Local Recorder Module
=====================

Sparar inspelningar lokalt på Pi-klienten vid rörelse.
Hanterar diskutrymme, rotation av gamla filer, och
serverar inspelningar on-demand till relay-servern.

Inspelningar sparas som MJPEG-filer med metadata i JSON.
"""

import asyncio
import json
import logging
import os
import shutil
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

logger = logging.getLogger('recorder')


class LocalRecorder:
    """
    Spelar in video lokalt vid rörelse.
    Hanterar lagring, rotation och on-demand-hämtning.
    """

    def __init__(self, config=None):
        config = config or {}

        # Lagringskonfiguration
        self.storage_path = Path(config.get('storage_path', '/var/lib/pi-camera/recordings'))
        self.max_storage_mb = config.get('max_storage_mb', 5000)  # 5 GB default
        self.max_age_days = config.get('max_age_days', 30)
        self.pre_record_seconds = config.get('pre_record_seconds', 5)
        self.post_record_seconds = config.get('post_record_seconds', 10)
        self.max_clip_seconds = config.get('max_clip_seconds', 300)  # 5 min max

        # State per kamera
        self._camera_states = {}
        self._lock = threading.Lock()

        # Skapa lagringsmapp
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Starta cleanup-timer
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        logger.info(
            f"Local recorder initialized: path={self.storage_path}, "
            f"max_storage={self.max_storage_mb}MB, max_age={self.max_age_days}d"
        )

    def get_camera_state(self, camera_id):
        """Hämta eller skapa state för en kamera."""
        if camera_id not in self._camera_states:
            cam_path = self.storage_path / camera_id
            cam_path.mkdir(parents=True, exist_ok=True)
            self._camera_states[camera_id] = {
                'recording': False,
                'current_clip': None,
                'current_file': None,
                'frame_count': 0,
                'start_time': None,
                'pre_buffer': deque(maxlen=self.pre_record_seconds * 15),  # ~15 FPS buffer
                'post_timer': None,
                'path': cam_path,
            }
        return self._camera_states[camera_id]

    def buffer_frame(self, camera_id, frame_data):
        """
        Buffra en frame i pre-record-buffern.
        Kallas för varje frame oavsett om det är rörelse eller inte.
        """
        state = self.get_camera_state(camera_id)
        state['pre_buffer'].append({
            'data': frame_data,
            'timestamp': time.time(),
        })

        # Om vi spelar in, skriv framen till filen
        if state['recording'] and state['current_file']:
            self._write_frame(state, frame_data)

    def start_recording(self, camera_id, trigger='motion', metadata=None):
        """
        Starta inspelning för en kamera.
        Skriver först ut pre-record-buffern, sedan nya frames.
        """
        state = self.get_camera_state(camera_id)

        if state['recording']:
            # Redan inspelning – förläng genom att nollställa post-timer
            if state['post_timer']:
                state['post_timer'] = None
            return state['current_clip']

        # Skapa nytt klipp
        now = datetime.now()
        date_dir = state['path'] / now.strftime('%Y-%m-%d')
        date_dir.mkdir(parents=True, exist_ok=True)

        clip_id = now.strftime('%H%M%S') + f'_{trigger}'
        clip_path = date_dir / clip_id
        clip_path.mkdir(parents=True, exist_ok=True)

        # Metadata
        clip_meta = {
            'clip_id': clip_id,
            'camera_id': camera_id,
            'trigger': trigger,
            'start_time': now.isoformat(),
            'end_time': None,
            'frame_count': 0,
            'size_bytes': 0,
            'metadata': metadata or {},
        }

        # Öppna fil för frames
        frames_file = clip_path / 'frames.mjpeg'
        index_file = clip_path / 'index.json'

        state['recording'] = True
        state['current_clip'] = clip_meta
        state['current_file'] = open(frames_file, 'wb')
        state['frame_count'] = 0
        state['start_time'] = time.time()
        state['frame_index'] = []
        state['clip_path'] = clip_path
        state['index_file'] = index_file

        # Skriv pre-record buffer
        pre_frames = list(state['pre_buffer'])
        for pf in pre_frames:
            self._write_frame(state, pf['data'], pf['timestamp'])

        logger.info(f"[{camera_id}] Recording started: {clip_id} (pre-buffer: {len(pre_frames)} frames)")
        return clip_meta

    def stop_recording(self, camera_id):
        """Stoppa inspelning för en kamera."""
        state = self.get_camera_state(camera_id)

        if not state['recording']:
            return None

        clip_meta = state['current_clip']

        # Stäng filen
        if state['current_file']:
            state['current_file'].close()
            state['current_file'] = None

        # Uppdatera metadata
        clip_meta['end_time'] = datetime.now().isoformat()
        clip_meta['frame_count'] = state['frame_count']

        # Beräkna storlek
        frames_file = state['clip_path'] / 'frames.mjpeg'
        if frames_file.exists():
            clip_meta['size_bytes'] = frames_file.stat().st_size

        # Spara index och metadata
        index_data = {
            'meta': clip_meta,
            'frames': state.get('frame_index', []),
        }
        with open(state['index_file'], 'w') as f:
            json.dump(index_data, f, indent=2)

        # Skapa thumbnail från första framen
        self._create_thumbnail(state['clip_path'], state.get('first_frame'))

        # Nollställ state
        state['recording'] = False
        state['current_clip'] = None
        state['frame_count'] = 0
        state['start_time'] = None
        state['frame_index'] = []
        state['post_timer'] = None

        logger.info(
            f"[{camera_id}] Recording stopped: {clip_meta['clip_id']} "
            f"({clip_meta['frame_count']} frames, "
            f"{clip_meta['size_bytes'] / 1024:.0f} KB)"
        )
        return clip_meta

    def motion_event(self, camera_id, event_type, metadata=None):
        """
        Hantera rörelse-event.
        event_type: 'start' eller 'end'
        """
        state = self.get_camera_state(camera_id)

        if event_type == 'start':
            self.start_recording(camera_id, trigger='motion', metadata=metadata)

        elif event_type == 'end':
            if state['recording']:
                # Vänta post_record_seconds innan vi stoppar
                state['post_timer'] = time.time() + self.post_record_seconds

    def check_post_timers(self):
        """Kolla om några post-record timers har gått ut. Kallas periodiskt."""
        now = time.time()
        for camera_id, state in self._camera_states.items():
            if state['recording'] and state['post_timer'] and now >= state['post_timer']:
                self.stop_recording(camera_id)

            # Max-längd check
            if state['recording'] and state['start_time']:
                duration = now - state['start_time']
                if duration >= self.max_clip_seconds:
                    logger.info(f"[{camera_id}] Max clip duration reached, stopping")
                    self.stop_recording(camera_id)

    def _write_frame(self, state, frame_data, timestamp=None):
        """Skriv en frame till inspelningsfilen."""
        if not state['current_file']:
            return

        ts = timestamp or time.time()
        offset = state['current_file'].tell()

        # Skriv JPEG-data med längdprefix
        length = len(frame_data)
        state['current_file'].write(length.to_bytes(4, 'big'))
        state['current_file'].write(frame_data)

        # Spara frame-index
        state['frame_index'].append({
            'offset': offset,
            'size': length,
            'timestamp': ts,
        })

        state['frame_count'] += 1

        # Spara första framen som thumbnail-källa
        if state['frame_count'] == 1:
            state['first_frame'] = frame_data

    def _create_thumbnail(self, clip_path, frame_data):
        """Skapa en thumbnail från en frame."""
        if not frame_data:
            return

        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(frame_data))
            img.thumbnail((320, 240))
            thumb_path = clip_path / 'thumbnail.jpg'
            img.save(thumb_path, 'JPEG', quality=60)
        except Exception as e:
            logger.debug(f"Could not create thumbnail: {e}")

    # ========================================================================
    # On-demand hämtning
    # ========================================================================

    def list_recordings(self, camera_id=None, date=None, limit=50):
        """
        Lista inspelningar, valfritt filtrerade per kamera och datum.
        Returnerar metadata-lista sorterad efter tid (nyast först).
        """
        recordings = []

        search_paths = []
        if camera_id:
            cam_path = self.storage_path / camera_id
            if cam_path.exists():
                search_paths.append(cam_path)
        else:
            for cam_path in self.storage_path.iterdir():
                if cam_path.is_dir():
                    search_paths.append(cam_path)

        for cam_path in search_paths:
            date_dirs = sorted(cam_path.iterdir(), reverse=True)
            if date:
                date_dirs = [d for d in date_dirs if d.name == date]

            for date_dir in date_dirs:
                if not date_dir.is_dir():
                    continue
                for clip_dir in sorted(date_dir.iterdir(), reverse=True):
                    if not clip_dir.is_dir():
                        continue
                    index_file = clip_dir / 'index.json'
                    if index_file.exists():
                        try:
                            with open(index_file) as f:
                                data = json.load(f)
                            meta = data.get('meta', {})
                            meta['has_thumbnail'] = (clip_dir / 'thumbnail.jpg').exists()
                            meta['clip_path'] = str(clip_dir)
                            recordings.append(meta)
                        except Exception:
                            pass

                    if len(recordings) >= limit:
                        break
                if len(recordings) >= limit:
                    break

        return recordings[:limit]

    def get_recording_frames(self, clip_path, start_frame=0, max_frames=None):
        """
        Hämta frames från en inspelning.
        Returnerar en generator av (timestamp, jpeg_data) tuples.
        """
        clip_path = Path(clip_path)
        frames_file = clip_path / 'frames.mjpeg'
        index_file = clip_path / 'index.json'

        if not frames_file.exists() or not index_file.exists():
            return

        with open(index_file) as f:
            data = json.load(f)

        frame_index = data.get('frames', [])
        end_frame = len(frame_index) if max_frames is None else min(start_frame + max_frames, len(frame_index))

        with open(frames_file, 'rb') as f:
            for i in range(start_frame, end_frame):
                entry = frame_index[i]
                f.seek(entry['offset'])
                length_bytes = f.read(4)
                if len(length_bytes) < 4:
                    break
                length = int.from_bytes(length_bytes, 'big')
                frame_data = f.read(length)
                if len(frame_data) < length:
                    break
                yield (entry['timestamp'], frame_data)

    def get_thumbnail(self, clip_path):
        """Hämta thumbnail för ett klipp."""
        thumb_path = Path(clip_path) / 'thumbnail.jpg'
        if thumb_path.exists():
            return thumb_path.read_bytes()
        return None

    def get_storage_stats(self):
        """Hämta lagringstatistik."""
        total_size = 0
        total_clips = 0
        camera_stats = {}

        for cam_dir in self.storage_path.iterdir():
            if not cam_dir.is_dir():
                continue
            cam_size = 0
            cam_clips = 0
            for root, dirs, files in os.walk(cam_dir):
                for f in files:
                    fpath = os.path.join(root, f)
                    cam_size += os.path.getsize(fpath)
                    if f == 'index.json':
                        cam_clips += 1
            camera_stats[cam_dir.name] = {
                'size_mb': round(cam_size / (1024 * 1024), 1),
                'clips': cam_clips,
            }
            total_size += cam_size
            total_clips += cam_clips

        disk_stat = shutil.disk_usage(self.storage_path)

        return {
            'total_size_mb': round(total_size / (1024 * 1024), 1),
            'total_clips': total_clips,
            'max_storage_mb': self.max_storage_mb,
            'disk_total_gb': round(disk_stat.total / (1024 ** 3), 1),
            'disk_free_gb': round(disk_stat.free / (1024 ** 3), 1),
            'cameras': camera_stats,
        }

    # ========================================================================
    # Cleanup / rotation
    # ========================================================================

    def _cleanup_loop(self):
        """Bakgrundstråd som rensar gamla inspelningar."""
        while True:
            try:
                self._cleanup()
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
            time.sleep(3600)  # Kör varje timme

    def _cleanup(self):
        """Rensa gamla inspelningar baserat på ålder och storlek."""
        # Rensa baserat på ålder
        cutoff = datetime.now() - timedelta(days=self.max_age_days)
        removed_age = 0

        for cam_dir in self.storage_path.iterdir():
            if not cam_dir.is_dir():
                continue
            for date_dir in cam_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                try:
                    dir_date = datetime.strptime(date_dir.name, '%Y-%m-%d')
                    if dir_date < cutoff:
                        shutil.rmtree(date_dir)
                        removed_age += 1
                except ValueError:
                    pass

        if removed_age > 0:
            logger.info(f"Cleanup: removed {removed_age} old date directories")

        # Rensa baserat på storlek
        stats = self.get_storage_stats()
        if stats['total_size_mb'] > self.max_storage_mb:
            self._cleanup_by_size(stats['total_size_mb'] - self.max_storage_mb * 0.8)

    def _cleanup_by_size(self, mb_to_free):
        """Rensa äldsta inspelningar tills tillräckligt med utrymme frigjorts."""
        all_clips = []
        for cam_dir in self.storage_path.iterdir():
            if not cam_dir.is_dir():
                continue
            for date_dir in sorted(cam_dir.iterdir()):
                if not date_dir.is_dir():
                    continue
                for clip_dir in sorted(date_dir.iterdir()):
                    if not clip_dir.is_dir():
                        continue
                    size = sum(
                        os.path.getsize(os.path.join(root, f))
                        for root, dirs, files in os.walk(clip_dir)
                        for f in files
                    )
                    all_clips.append((clip_dir, size))

        # Sortera äldst först (baserat på mappnamn)
        all_clips.sort(key=lambda x: str(x[0]))

        freed = 0
        for clip_dir, size in all_clips:
            if freed >= mb_to_free * 1024 * 1024:
                break
            shutil.rmtree(clip_dir)
            freed += size
            logger.info(f"Cleanup: removed {clip_dir.name} ({size / 1024:.0f} KB)")

        # Rensa tomma mappar
        for cam_dir in self.storage_path.iterdir():
            if not cam_dir.is_dir():
                continue
            for date_dir in cam_dir.iterdir():
                if date_dir.is_dir() and not any(date_dir.iterdir()):
                    date_dir.rmdir()

    def stop_all(self):
        """Stoppa alla pågående inspelningar."""
        for camera_id in list(self._camera_states.keys()):
            if self._camera_states[camera_id]['recording']:
                self.stop_recording(camera_id)
