FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
ARG APP_VERSION
LABEL org.opencontainers.image.version=$APP_VERSION
COPY VERSION ./
RUN test -n "$APP_VERSION" && test "$APP_VERSION" = "$(tr -d '\r\n' < VERSION)"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home music
COPY backend ./backend
COPY frontend ./frontend
COPY run.py gunicorn.conf.py config.yaml ./
RUN mkdir -p /app/data/music && chown -R music:music /app/data
USER music
VOLUME ["/app/data"]
EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "run:app"]
