#!/bin/sh
set -e

mkdir -p "$PYTHONUSERBASE"
cd /app

if [ -n "$REQUIREMENTS_FILE" ] && [ -f "$REQUIREMENTS_FILE" ]; then
    echo "[serves] installing dependencies from $REQUIREMENTS_FILE ..."
    pip install --user --no-cache-dir -r "$REQUIREMENTS_FILE"
fi

echo "[serves] starting: $RUN_COMMAND"
exec sh -c "$RUN_COMMAND"
