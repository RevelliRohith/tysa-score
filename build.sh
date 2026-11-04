#!/usr/bin/env bash
# Render Build Script for TYSA Score
set -o errexit

echo "==> Upgrading pip..."
pip install --upgrade pip

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Collecting static assets via WhiteNoise..."
python manage.py collectstatic --noinput

echo "==> Running Neon PostgreSQL migrations..."
python manage.py migrate --noinput

echo "==> Importing Asana catalogue (idempotent)..."
python manage.py import_asanas
