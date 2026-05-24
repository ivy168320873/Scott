#!/usr/bin/env bash
set -e
pip install -r requirements.txt -q
echo "啟動股市動能分析伺服器 → http://localhost:5000"
python app.py
