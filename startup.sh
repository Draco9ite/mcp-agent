#!/bin/bash
# Azure App Service startup script for Price Adjustment Agent
# Uses gunicorn with eventlet worker for WebSocket support

gunicorn --worker-class eventlet -w 1 --bind=0.0.0.0:${PORT:-8000} --timeout 600 --log-level info app:app
