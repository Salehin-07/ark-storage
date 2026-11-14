"""WSGI entry point:  gunicorn -c gunicorn.conf.py wsgi:app"""
from arkstorage.app import create_app

app = create_app()

if __name__ == "__main__":
    import os
    app.run(host=os.environ.get("ARK_HOST", "0.0.0.0"), port=int(os.environ.get("ARK_PORT") or os.environ.get("PORT") or 8000), threaded=True)
