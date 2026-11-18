"""ASGI entry point:  uvicorn arkstorage.asgi:app   (or simply: python -m arkstorage)"""
from .app import create_app

app = create_app()
