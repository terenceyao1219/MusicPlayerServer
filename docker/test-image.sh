#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
IMAGE="${1:-music-player-server:$VERSION}"
WORK="$(mktemp -d /tmp/sonora-image-test.XXXXXX)"
CONTAINER=""
cleanup() {
  if [[ -n "$CONTAINER" ]]; then docker rm -fv "$CONTAINER" >/dev/null 2>&1 || true; fi
  rm -rf -- "$WORK"
}
trap cleanup EXIT
mkdir "$WORK/fixtures"
# Full host FFmpeg generates representative input codecs. The runtime encoder
# intentionally cannot create MP3/Vorbis/Opus test fixtures or decode TS output.
fixture() {
  ffmpeg -nostdin -hide_banner -loglevel error -f lavfi -i 'sine=frequency=440:sample_rate=48000' \
    -t 1 -ac 2 -c:a "$2" "$WORK/fixtures/$1"
}
fixture mp3.mp3 libmp3lame
fixture aac.m4a aac
fixture alac.m4a alac
fixture vorbis.ogg libvorbis
fixture opus.ogg libopus
fixture pcm16.wav pcm_s16le
fixture pcm24.wav pcm_s24le
fixture float32.wav pcm_f32le
fixture ima.wav adpcm_ima_wav
fixture ms.wav adpcm_ms
# Keep Windows docker credential helpers away from WSL network mount cwd.
cd /
CONTAINER="$(docker create --entrypoint sh "$IMAGE" -c 'python /tmp/smoke_image.py && /usr/local/bin/python -m pip --python /opt/venv/bin/python install --no-cache-dir --target /tmp/test-deps pytest==8.4.2 && PYTHONPATH=/tmp/test-deps:/app python -m pytest -q -o cache_dir=/tmp/pytest-cache /tmp/tests/test_api.py /tmp/tests/test_passkeys.py')"
docker cp "$WORK/fixtures" "$CONTAINER:/tmp/image-fixtures"
docker cp "$ROOT/tests/smoke_image.py" "$CONTAINER:/tmp/smoke_image.py"
docker cp "$ROOT/tests" "$CONTAINER:/tmp/tests"
docker start -a "$CONTAINER"
docker cp "$CONTAINER:/tmp/image-results" "$WORK/results"
for segment in "$WORK/results/"*.ts; do
  ffmpeg -nostdin -hide_banner -loglevel error -i "$segment" -f null -
done
echo 'PASS: all generated HLS samples decode successfully with the full host FFmpeg.'

# Finally exercise the image's real Gunicorn CMD, without exposing a host port.
docker rm -v "$CONTAINER" >/dev/null
CONTAINER="$(docker create --tmpfs /app/data:uid=10001,gid=10001 "$IMAGE")"
docker start "$CONTAINER" >/dev/null
docker exec -i "$CONTAINER" python - <<'PY'
import json
import time
import urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
            assert json.load(response) == {'status': 'ok'}
        print('PASS: production Gunicorn CMD and HTTP /health')
        break
    except OSError:
        time.sleep(.2)
else:
    raise SystemExit('Gunicorn did not become healthy')
PY
