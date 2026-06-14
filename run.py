"""
run.py — Arcane application entry point.
Run with: python3 run.py
Or:       uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""
import os
import sys

# Ensure the arcane directory is in the path
sys.path.insert(0, os.path.dirname(__file__))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
