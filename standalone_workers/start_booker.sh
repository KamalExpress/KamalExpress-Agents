#!/usr/bin/env bash
echo "========================================================"
echo "      KAMAL EXPRESS - 1-CLICK GVC DROP BOOKER"
echo "========================================================"
echo ""
echo "Arming slot-drop booker using config.json..."
echo ""

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"
python3 booker.py
