#!/usr/bin/env bash
set -euo pipefail

# Sanity check: /app/.env must have been written by the initContainer.
if [ ! -f /app/.env ]; then
  echo "FATAL: /app/.env not found. Did the env-builder initContainer run?" >&2
  exit 1
fi

# We DON'T source /app/.env here. Python's load_dotenv() will read it.
# Read APP_MODE just enough to dispatch.
APP_MODE_VALUE=$(grep -E '^APP_MODE=' /app/.env | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")

case "${APP_MODE_VALUE}" in
  pre_earnings)    exec python -m pre_earnings.main ;;
  ects)            exec python -m ects.main ;;
  calendar_sync)   exec python -m event_calendar.calendar_sync_main ;;
  task_dispatcher) exec python -m event_calendar.task_dispatcher_main ;;
  *) echo "Unknown APP_MODE='${APP_MODE_VALUE}' in /app/.env"; exit 1 ;;
esac
