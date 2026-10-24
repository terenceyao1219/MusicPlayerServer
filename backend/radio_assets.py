"""Bounded background conversion cache and generated station artwork."""
import hashlib
import math
import shutil
import struct
import subprocess
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor


class RadioAudio:
    def __init__(self, root, ffmpeg, limit):
        self.root, self.ffmpeg, self.limit = root, shutil.which(ffmpeg), limit
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='radio-audio')
        self.jobs, self.errors = {}, {}
        self.lock = threading.RLock()

    @staticmethod
    def key(source):
        info = source.stat()
        return hashlib.sha256(f'radio-v1:{source}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()

    def get(self, source):
        if not self.ffmpeg:
            raise ValueError('播放服務找不到 FFmpeg，請聯絡管理員。')
        key = self.key(source)
        with self.lock:
            target = self.root / key
            if (target / 'index.m3u8').is_file():
                segments, duration = [], 0
                for line in (target / 'index.m3u8').read_text().splitlines():
                    if line.startswith('#EXTINF:'):
                        duration = float(line.split(':')[1].rstrip(','))
                    elif line and not line.startswith('#'):
                        if '/' in line or '\\' in line or not line.endswith('.ts'):
                            raise ValueError('音訊快取格式不正確。')
                        if not (target / line).is_file():
                            raise ValueError('音訊快取不完整。')
                        segments.append((line, duration))
                if not segments:
                    raise ValueError('歌曲沒有可播放的音訊。')
                return key, segments
            if key in self.errors:
                raise ValueError('歌曲轉碼失敗，請檢查來源檔案後重新播放。')
            if key not in self.jobs:
                if len(self.jobs) >= 32:
                    raise ValueError('歌曲準備工作較多，請稍後再試。')
                self.jobs[key] = self.pool.submit(self.convert, key, source)
            return None

    def convert(self, key, source):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / key
            target.mkdir(exist_ok=True)
            subprocess.run([self.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:a:0', '-vn',
                            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-threads', '1',
                            '-f', 'hls', '-hls_time', '6', '-hls_playlist_type', 'vod',
                            '-hls_segment_filename', str(target / 'seg%06d.ts'), str(target / 'building.m3u8')],
                           check=True, capture_output=True, timeout=300)
            (target / 'building.m3u8').replace(target / 'index.m3u8')
        except Exception:
            with self.lock:
                self.errors[key] = time.time()
                # Bound error history as well as the work queue.
                if len(self.errors) > 128:
                    self.errors.pop(next(iter(self.errors)))
        finally:
            with self.lock:
                self.jobs.pop(key, None)

    def cleanup(self, pinned):
        with self.lock:
            if not self.root.exists():
                return
            directories = [p for p in self.root.iterdir() if p.is_dir() and len(p.name) == 64
                           and all(c in '0123456789abcdef' for c in p.name)]
            sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file()) for p in directories}
            total = sum(sizes.values())
            for p in sorted(directories, key=lambda p: p.stat().st_mtime):
                if total <= self.limit:
                    break
                if p.name not in pinned and p.name not in self.jobs:
                    total -= sizes[p]
                    shutil.rmtree(p)


def artwork(background, foreground):
    """Original record/radio design, 256px RGB PNG; no external image parser."""
    bg = bytes.fromhex(background[1:])
    fg = bytes.fromhex(foreground[1:])
    rows = bytearray()
    for y in range(256):
        rows.append(0)
        for x in range(256):
            radius = math.hypot(x - 128, y - 128)
            ink = 77 <= radius <= 84 or 48 <= radius <= 52 or radius <= 13
            # Radio waves at each side of the record.
            ink = ink or (x < 36 or x > 220) and 102 <= radius <= 107 and abs(y - 128) < 50
            rows.extend(fg if ink else bg)
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 256, 256, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(rows))) + chunk(b'IEND', b''))
