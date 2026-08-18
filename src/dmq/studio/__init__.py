"""Студия — локальное веб-приложение для сборки игры."""

from .app import create_app, serve

__all__ = ["create_app", "serve"]
