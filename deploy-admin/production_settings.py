"""Railway / HTTPS production tweaks for Flask apps."""
import os

from werkzeug.middleware.proxy_fix import ProxyFix


def is_production() -> bool:
    return (
        os.getenv("RAILWAY_ENVIRONMENT") == "production"
        or os.getenv("ENVIRONMENT", "").lower() == "production"
        or os.getenv("FLASK_ENV", "").lower() == "production"
    )


def apply_production_config(app) -> None:
    """Trust reverse-proxy headers and tighten cookies when deployed."""
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    if not is_production():
        return
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
