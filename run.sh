#!/bin/bash
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

.venv/bin/python main.py
