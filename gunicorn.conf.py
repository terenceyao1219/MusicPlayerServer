from backend.app import BASE
import os
import yaml

with open(os.environ.get("MPLAYER_CONFIG", BASE / "config.yaml"), encoding="utf-8") as file:
    settings = yaml.safe_load(file)["server"]
bind = f"{settings['host']}:{settings['port']}"
# File/database mutations share an in-process lock. Keep exactly one worker.
workers = 1
threads = 8
timeout = 120
accesslog = "-"
errorlog = "-"
if settings.get("tls"):
    certfile = settings["certfile"]
    keyfile = settings["keyfile"]
