#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ -f .run/pid ]; then
  kill "$(cat .run/pid)" 2>/dev/null || true
  rm -f .run/pid .run/port
fi
