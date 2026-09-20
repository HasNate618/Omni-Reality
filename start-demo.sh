#!/usr/bin/env bash
# Start everything for the Quest -> Huawei -> SAM 2 demo, in the right order.
#
#   ./start-demo.sh            live Huawei planner (needs provider/.env)
#   ./start-demo.sh --stub     no model call, seeds the centre of the frame
#   ./start-demo.sh --no-app   don't relaunch the Quest app
#   ./start-demo.sh --quiet    less logging (INFO instead of DEBUG)
#   ./start-demo.sh --install  install QuestDemo/Builds/QuestDemo.apk first
#
# Ctrl+C stops everything this script started. Logs live in logs/.
set -u -o pipefail

cd "$(dirname "$0")"
mkdir -p logs
PLANNER=yibu
LAUNCH_APP=1
LOG_LEVEL=DEBUG
COORD_PORT=8765
SAM2_PORT=8766
APP_ID=com.omni.questdemo
STARTED_SAM2=0
INSTALL_APK=0
APK=QuestDemo/Builds/QuestDemo.apk
PIDS=()

for arg in "$@"; do
  case "$arg" in
    --stub) PLANNER=stub ;;
    --no-app) LAUNCH_APP=0 ;;
    --quiet) LOG_LEVEL=INFO ;;
    --install) INSTALL_APK=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()  { printf '    \033[32mok\033[0m  %s\n' "$*"; }
warn(){ printf '    \033[33m!!\033[0m  %s\n' "$*"; }

cleanup() {
  say "Stopping"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  [ "$STARTED_SAM2" = 1 ] && pkill -f "sam2_ws_server.py" 2>/dev/null
  echo "    logs kept in logs/"
  exit 0
}
trap cleanup INT TERM

listening_pid() { lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }

# Wait until a log file contains a marker, or give up.
wait_for() { # file, marker, seconds, name
  for _ in $(seq "$3"); do
    grep -q "$2" "$1" 2>/dev/null && return 0
    sleep 1
  done
  warn "$4 did not start; last lines of $1:"
  tail -5 "$1" | sed 's/^/        /'
  return 1
}

# 1. SAM 2 server (slow to load, so reuse one that is already up)
say "SAM 2 server (port $SAM2_PORT)"
if [ -n "$(listening_pid $SAM2_PORT)" ]; then
  ok "already running (pid $(listening_pid $SAM2_PORT)), reusing it"
else
  ( cd sam2 && . .venv/bin/activate && SAM2_WS_PORT=$SAM2_PORT exec python -u sam2_ws_server.py ) \
    > logs/sam2.log 2>&1 &
  STARTED_SAM2=1
  echo "    loading the model, this takes ~40 s on first run..."
  wait_for logs/sam2.log "Starting SAM 2" 180 "SAM 2 server" || exit 1
  ok "started ($(grep -m1 'Using device' logs/sam2.log | sed 's/Using //'))"
fi

# 2. Coordinator. Only ever one: a leftover holds the headset's socket and
# silently swallows everything it sends.
say "Coordinator (port $COORD_PORT)"
old=$(listening_pid $COORD_PORT)
if [ -n "$old" ]; then
  kill "$old" 2>/dev/null; sleep 1
  ok "stopped leftover coordinator (pid $old)"
fi
if [ "$PLANNER" = yibu ] && [ -f provider/.env ]; then
  set -a; . ./provider/.env; set +a
fi
if [ "$PLANNER" = yibu ] && [ -z "${YIBU_API_KEY:-}" ]; then
  warn "no YIBU_API_KEY (provider/.env); falling back to --stub"
  PLANNER=stub
fi
( cd provider && . .venv/bin/activate && exec python -u -m coordinator.server \
    --planner "$PLANNER" --sam2-url ws://127.0.0.1:$SAM2_PORT --log-level $LOG_LEVEL ) > logs/coordinator.log 2>&1 &
PIDS+=($!)
wait_for logs/coordinator.log "coordinator listening" 30 "Coordinator" || exit 1
ok "started (planner=$PLANNER, logs=$LOG_LEVEL)"
[ "$PLANNER" = stub ] && warn "stub planner: seeds the centre of the frame, Huawei is not called"

# 3. Headset: USB tunnel, so the app can use 127.0.0.1 and ignore Wi-Fi.
say "Headset"
if [ -n "$(adb devices | sed -n '2p' | grep -w device)" ]; then
  adb reverse tcp:$COORD_PORT tcp:$COORD_PORT >/dev/null && ok "USB tunnel ready (app connects to 127.0.0.1:$COORD_PORT)"
  # A freshly built APK that was never installed is the classic "my change
  # did nothing": Unity writes the file, the headset keeps the old build.
  if [ -f "$APK" ]; then
    apk_epoch=$(stat -f %m "$APK")
    installed=$(adb shell dumpsys package $APP_ID 2>/dev/null | sed -n 's/.*lastUpdateTime=//p' | head -1 | tr -d '\r')
    installed_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$installed" +%s 2>/dev/null || echo 0)
    if [ "$INSTALL_APK" = 1 ]; then
      echo "    installing $APK ..."
      if adb install -r "$APK" >/dev/null 2>&1; then ok "installed (built $(date -r "$apk_epoch" '+%H:%M'))"
      else warn "adb install failed; install it from Unity instead"; fi
    elif [ "$apk_epoch" -gt "$installed_epoch" ]; then
      warn "the headset is running an OLDER build (installed $installed, apk built $(date -r "$apk_epoch" '+%Y-%m-%d %H:%M'))"
      warn "your Unity changes are NOT on the device. Re-run with --install"
    else
      ok "installed build is current"
    fi
  fi
  if [ "$LAUNCH_APP" = 1 ]; then
    adb shell monkey -p $APP_ID -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
    sleep 4
    if [ -n "$(adb shell pidof $APP_ID)" ]; then ok "app launched"; else warn "app did not start; launch it from the headset"; fi
  fi
  focus=$(adb shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus)
  case "$focus" in
    *questdemo*) ok "app is in the foreground" ;;
    *) warn "app is NOT in the foreground: $(echo "$focus" | tr -d '\r')"
       warn "PUT THE HEADSET ON. Off your face it suspends the app: no frames, no A button, no mic." ;;
  esac
else
  warn "no headset over USB. Plug it in and rerun, or set the app's laptop_ipv4 to $(ipconfig getifaddr en0 2>/dev/null || echo '<this mac ip>')"
fi

say "Ready"
cat <<'STEPS'
    What to do, in order:
      1. Put the headset ON (off your face = app suspended = nothing happens).
      2. Look at the object you want to track.
      3. Shake the right controller to wake it, then HOLD A.
      4. While holding A, say e.g. "track the laptop". Speak > 0.5 s.
      5. Release A.

    What you should see below, in this order (each line is a checkpoint):
      headset: QUEST_STREAM listening (hold A)        <- A press registered, mic recording
      headset: QUEST_STREAM utterance=... pcm_bytes=  <- speech captured and sent
      coord:   utterance_end ...: audio=N bytes       <- laptop received the speech
      coord:   turn N: snapshot found ...             <- the frame Huawei will look at
      coord:   calling qwen3.8-omni-flash ...         <- request sent to Huawei
      coord:   turn N: model replied ... target=...   <- Huawei's answer (u,v of the object)
      coord:   SAM2 seed frame_id=... x=... y=...     <- point handed to SAM 2 as the click
      coord:   SAM2 connected; seeding frame ...
      headset: QUEST_TRACKING tracking: SAM 2 is running.
      headset: QUEST_TRACKING first mask frame=...    <- masks arriving, overlay should show

    If it stops, the LAST line above tells you where. Common causes:
      no "listening"          -> controller asleep, or headset off your face
      "too short"             -> held A under half a second
      no "calling qwen..."    -> snapshot missing: speak again while looking at the object
      "call failed"           -> Huawei/network/key problem (the line names it)
      "target=None"           -> Huawei could not pick one object; say it differently
      "SAM2 ... failed"       -> SAM 2 server problem (see logs/sam2.log)
      masks but nothing shown -> check headset: QUEST_OVERLAY lines (they say why)
      white/blank square       -> old build still installed: rebuild in Unity, then --install

    Ctrl+C stops everything. Full logs: logs/coordinator.log, logs/sam2.log
STEPS
echo
tail -f logs/coordinator.log | grep --line-buffered -v "websockets.server" | sed -u 's/^/coord:   /' &
PIDS+=($!)
tail -f logs/sam2.log | grep --line-buffered -E "stats|Error|error|Traceback" | sed -u 's/^/sam2:    /' &
PIDS+=($!)
if [ -n "$(adb devices | sed -n '2p' | grep -w device)" ]; then
  adb logcat -s Unity | grep --line-buffered -E "QUEST_TRACKING|QUEST_OVERLAY|QUEST_STREAM|CoordinatorClient" \
    | sed -u 's/^.*I Unity   : /headset: /' &
  PIDS+=($!)
fi
wait
