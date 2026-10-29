import os
from pathlib import Path

from flask import Flask

from hms import create_app


app = create_app(
    {
        "SECRET_KEY": os.environ.get("HMS_SECRET_KEY", "local-demo-key-change-me"),
        "DATABASE": str(Path(__file__).parent / "instance" / "hospital.db"),
    }
)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
