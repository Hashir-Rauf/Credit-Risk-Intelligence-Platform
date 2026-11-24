"""WSGI entry point: `gunicorn src.serving.wsgi:app` (see docker/Dockerfile.api)."""

from src.serving.app import create_app

app = create_app()
