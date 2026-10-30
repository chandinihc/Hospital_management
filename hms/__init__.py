import os
from datetime import date
from pathlib import Path

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from .db import get_production_passwords, init_app as init_db
from .routes import bp

csrf = CSRFProtect()


def create_app(test_config=None):
    static_folder = Path(__file__).parent.parent / "public" / "static"
    app = Flask(
        __name__,
        instance_relative_config=True,
        static_folder=str(static_folder),
        static_url_path="/static",
    )

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("HMS_SECRET_KEY"),
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        DATABASE=f"{app.instance_path}/hospital.db",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    if test_config:
        app.config.update(test_config)

    is_vercel = os.environ.get("VERCEL") == "1"
    is_production = is_vercel or os.environ.get("FLASK_ENV") == "production"
    app.config["IS_PRODUCTION"] = is_production

    if not app.config.get("SECRET_KEY"):
        if is_production:
            raise RuntimeError("HMS_SECRET_KEY must be set in production.")
        app.config["SECRET_KEY"] = "local-demo-key-change-me"

    if is_production and not app.config.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be set in production.")

    if is_production:
        try:
            get_production_passwords()
        except ValueError as error:
            raise RuntimeError(str(error)) from error

    if is_production:
        app.config["SESSION_COOKIE_SECURE"] = True

    if not app.config.get("DATABASE_URL"):
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        Path(app.config["DATABASE"]).expanduser().parent.mkdir(
            parents=True, exist_ok=True
        )

    csrf.init_app(app)
    init_db(app)
    app.register_blueprint(bp)

    @app.context_processor
    def inject_dates():
        current_date = date.today()
        return {
            "today_iso": current_date.isoformat(),
            "today_label": current_date.strftime("%A, %B %d, %Y"),
        }

    return app
