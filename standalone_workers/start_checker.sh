#!/usr/bin/env bash
echo "========================================================"
echo "      KAMAL EXPRESS - 1-CLICK GVC SLOT SCOUT"
echo "========================================================"
echo ""
echo "Starting slot availability checker using config.json..."
echo ""

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"
python3 checker.py
