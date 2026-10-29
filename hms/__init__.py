from datetime import date
from pathlib import Path

from flask import Flask

from .db import init_app as init_db
from .routes import bp


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY="local-demo-key-change-me",
        DATABASE=f"{app.instance_path}/hospital.db",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
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
