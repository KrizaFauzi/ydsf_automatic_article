#!/usr/bin/env python3
# /home/username/myproject/run.py

import sys
import os
import argparse

# ============================================
# KONFIGURASI - SESUAIKAN DENGAN SERVER ANDA
# ============================================

# Ganti dengan username dan path project Anda
PROJECT_PATH = '/home/info-falah/public_html/aiarticle'
VENV_PATH = os.path.join(PROJECT_PATH, 'venv')

# Cek versi Python yang digunakan venv
python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
VENV_PACKAGES = os.path.join(VENV_PATH, 'lib', python_version, 'site-packages')

# ============================================
# SETUP PATH
# ============================================

# Pastikan venv packages ditemukan
if not os.path.exists(VENV_PACKAGES):
    # Fallback: cari folder python apa saja di venv/lib
    lib_path = os.path.join(VENV_PATH, 'lib')
    if os.path.exists(lib_path):
        python_folders = [f for f in os.listdir(lib_path) if f.startswith('python')]
        if python_folders:
            VENV_PACKAGES = os.path.join(lib_path, python_folders[0], 'site-packages')
            print(f"Using fallback path: {VENV_PACKAGES}")

# Tambahkan ke sys.path (prioritas tertinggi)
sys.path.insert(0, VENV_PACKAGES)
sys.path.insert(0, PROJECT_PATH)

# Environment variables
os.environ['PYTHONPATH'] = VENV_PACKAGES
os.environ['VIRTUAL_ENV'] = VENV_PATH

# ============================================
# IMPORT APLIKASI
# ============================================

from a2wsgi import ASGIMiddleware
from main import app

# Buat WSGI application
application = ASGIMiddleware(app)

# ============================================
# JALANKAN SERVER (untuk mode Self Managed)
# ============================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run Python App for Webuzo Self Managed')
    parser.add_argument('--port', type=int, default=30000, help='Port to run on')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind')
    args = parser.parse_args()
    
    print(f"Starting server on {args.host}:{args.port}")
    print(f"Project path: {PROJECT_PATH}")
    print(f"Using Python: {sys.executable}")
    print(f"Python version: {sys.version}")
    
    from wsgiref.simple_server import make_server
    
    try:
        httpd = make_server(args.host, args.port, application)
        print(f"Server is running! Access http://{args.host}:{args.port}")
        httpd.serve_forever()
    except OSError as e:
        print(f"ERROR: Could not start server: {e}")
        sys.exit(1)