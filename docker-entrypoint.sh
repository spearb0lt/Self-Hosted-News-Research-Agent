#!/usr/bin/env bash
# Start the pieces the container was asked for.
#
#   all   both the API and the Next.js server (the default)
#   api   the Python API only, for when the frontend is hosted elsewhere
#   cron  a one shot pipeline run for every workspace, then exit
#
# Both processes run in the foreground under this script rather than under a
# supervisor, because a container that keeps running after half of it has died
# looks healthy to an orchestrator while serving errors.
set -euo pipefail

MODE="${1:-all}"
API_PORT="${PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"

run_api() {
  exec uvicorn server.main:app \
    --host 0.0.0.0 --port "${API_PORT}" \
    --workers "${UVICORN_WORKERS:-1}" \
    --timeout-keep-alive 65
}

case "${MODE}" in
  api)
    run_api
    ;;

  cron)
    exec python -m server.ops.cli run-all
    ;;

  all)
    uvicorn server.main:app --host 0.0.0.0 --port "${API_PORT}" \
      --workers "${UVICORN_WORKERS:-1}" --timeout-keep-alive 65 &
    API_PID=$!

    npm run start -- --port "${WEB_PORT}" --hostname 0.0.0.0 &
    WEB_PID=$!

    # Forward a stop signal to both, so docker stop is not a 10 second wait
    # followed by a kill.
    trap 'kill -TERM "${API_PID}" "${WEB_PID}" 2>/dev/null || true' TERM INT

    # Exit as soon as either child does, carrying its status out with it.
    wait -n "${API_PID}" "${WEB_PID}"
    STATUS=$?
    kill -TERM "${API_PID}" "${WEB_PID}" 2>/dev/null || true
    exit "${STATUS}"
    ;;

  *)
    exec "$@"
    ;;
esac
