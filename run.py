from backend.app import create_app

app = create_app()

if __name__ == "__main__":
    settings = app.config["SETTINGS"]["server"]
    tls = (settings["certfile"], settings["keyfile"]) if settings.get("tls") else None
    app.run(host=settings["host"], port=int(settings["port"]), ssl_context=tls, threaded=True, debug=False)
