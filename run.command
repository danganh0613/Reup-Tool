#!/bin/bash
# macOS: nhấp đúp để chạy bản mã nguồn (cần Python 3.12 từ python.org + pip install -r requirements.txt av)
cd "$(dirname "$0")" || exit 1
exec python3 main.py "$@"
