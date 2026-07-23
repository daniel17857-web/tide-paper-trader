#!/usr/bin/env bash
# verify.sh — lint(語法編譯)+ 全測試。DoD 第 3 條:全過才算完成。
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== lint(py_compile)=="
python3 -m compileall -q config.py adapters signals core report scripts tests
echo "OK"
echo "== pytest =="
python3 -m pytest tests -q
echo "== verify.sh 全過 =="
