import os

from hms import create_app

app = create_app({"SECRET_KEY": os.environ.get("HMS_SECRET_KEY")})


if __name__ == "__main__":
    app.run(
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1"
        and os.environ.get("FLASK_ENV") != "production"
    )
