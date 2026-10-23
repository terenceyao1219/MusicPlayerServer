# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-alpine3.23
FROM ${PYTHON_IMAGE} AS base

FROM base AS ffmpeg-builder
ARG FFMPEG_BUILD_JOBS=2
RUN apk add --no-cache build-base nasm curl xz
WORKDIR /build
COPY docker/build-ffmpeg.sh ./
RUN curl -fSL --retry 3 https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz -o ffmpeg.tar.xz \
    && echo "7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3  ffmpeg.tar.xz" | sha256sum -c - \
    && tar -xf ffmpeg.tar.xz \
    && cd ffmpeg-8.1.3 \
    && sh /build/build-ffmpeg.sh

FROM base AS python-builder
COPY requirements.txt /build/requirements.txt
# watchdog has no musllinux wheel; build only this package from source on Alpine.
RUN python -m venv --without-pip /opt/venv \
    && python -m pip --python /opt/venv/bin/python install --no-cache-dir --only-binary=:all: --no-binary=watchdog -r /build/requirements.txt

FROM base AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
WORKDIR /app
ARG APP_VERSION
LABEL org.opencontainers.image.version=$APP_VERSION
COPY VERSION ./
RUN test -n "$APP_VERSION" && test "$APP_VERSION" = "$(tr -d '\r\n' < VERSION)"
RUN apk add --no-cache libgcc \
    && addgroup -g 10001 music \
    && adduser -D -u 10001 -G music music
COPY --from=ffmpeg-builder /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /opt/ffmpeg/lib/ /usr/local/lib/
COPY --from=ffmpeg-builder /opt/ffmpeg/share/licenses/ffmpeg/ /usr/local/share/licenses/ffmpeg/
COPY --from=python-builder /opt/venv /opt/venv
COPY backend ./backend
COPY frontend ./frontend
COPY run.py gunicorn.conf.py config.yaml ./
RUN ffmpeg -version \
    && mkdir -p /app/data/music && chown -R music:music /app/data
USER music
VOLUME ["/app/data"]
EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "run:app"]
