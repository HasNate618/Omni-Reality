#!/usr/bin/env bash
# Start everything for the Quest -> Huawei -> SAM 2 demo, in the right order.
#
#   ./start-demo.sh            live Huawei planner (needs provider/.env)
#   ./start-demo.sh --stub     no model call, seeds the centre of the frame
#   ./start-demo.sh --layout   Layout mode: cart boxes in your corner, no SAM 2
#                              (works with a dead key: canned cart, local voice)
#   ./start-demo.sh --no-app   don't relaunch the Quest app
#   ./start-demo.sh --quiet    less logging (INFO instead of DEBUG)
#   ./start-demo.sh --install  install QuestDemo/Builds/QuestDemo.apk first
#   ./start-demo.sh --wifi     headset talks over Wi-Fi instead of the USB tunnel
#                              (default is USB: plug in, app reaches 127.0.0.1)
#   ./start-demo.sh --set-ip X point the app at address X (then rebuild in Unity)
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
USE_SAM2=1
INSTALL_APK=0
WIFI=0
SET_IP=""
APK=QuestDemo/Builds/QuestDemo.apk
SETTINGS=QuestDemo/Assets/Resources/QuestTrackingSettings.asset
PIDS=()

for arg in "$@"; do
  case "$arg" in
    --stub) PLANNER=stub ;;
    --layout) PLANNER=layout; USE_SAM2=0 ;;
    --no-app) LAUNCH_APP=0 ;;
    --quiet) LOG_LEVEL=INFO ;;
    --install) INSTALL_APK=1 ;;
    --wifi) WIFI=1 ;;
    --set-ip=*) SET_IP="${arg#*=}" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

app_address() { sed -n 's/ *laptopIpv4: *//p' "$SETTINGS" 2>/dev/null | tr -d '\r'; }

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()  { printf '    \033[32mok\033[0m  %s\n' "$*"; }
warn(){ printf '    \033[33m!!\033[0m  %s\n' "$*"; }

if [ -n "$SET_IP" ]; then
  # The address is baked into the build, so this needs a rebuild to take effect.
  sed -i '' "s/  laptopIpv4: .*/  laptopIpv4: $SET_IP/" "$SETTINGS"
  say "App address set to $SET_IP"
  echo "    Now rebuild in Unity, then: ./start-demo.sh --install$([ "$SET_IP" = 127.0.0.1 ] || echo ' --wifi')"
  exit 0
fi

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

# 0. Preflight: B-mode resamples the model's 24 kHz reply to 16 kHz for Quest.
if ! command -v ffmpeg >/dev/null 2>&1; then
  say "Preflight"
  warn "ffmpeg is NOT installed: A-mode is fine, B-mode conversation will be SILENT"
  warn "fix: brew install ffmpeg"
fi

# 1. SAM 2 server (slow to load, so reuse one that is already up)
say "SAM 2 server (port $SAM2_PORT)"
if [ "$USE_SAM2" = 0 ]; then
  ok "skipped: Layout mode draws its own boxes and never seeds a tracker"
elif [ -n "$(listening_pid $SAM2_PORT)" ]; then
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
if { [ "$PLANNER" = yibu ] || [ "$PLANNER" = layout ]; } && [ -f provider/.env ]; then
  set -a; . ./provider/.env; set +a
fi
if [ "$PLANNER" = yibu ] && [ -z "${YIBU_API_KEY:-}" ]; then
  warn "no YIBU_API_KEY (provider/.env); falling back to --stub"
  PLANNER=stub
fi
sam2_arg=""
[ "$USE_SAM2" = 1 ] && sam2_arg="--sam2-url ws://127.0.0.1:$SAM2_PORT"
# shellcheck disable=SC2086
( cd provider && . .venv/bin/activate && exec python -u -m coordinator.server \
    --planner "$PLANNER" $sam2_arg --log-level $LOG_LEVEL ) > logs/coordinator.log 2>&1 &
PIDS+=($!)
wait_for logs/coordinator.log "coordinator listening" 30 "Coordinator" || exit 1
ok "started (planner=$PLANNER, logs=$LOG_LEVEL)"
if [ "$PLANNER" = stub ]; then
  warn "stub planner: seeds the centre of the frame, Huawei is not called"
  warn "B-mode conversation needs the live model, so it will not work with --stub"
fi
if [ "$PLANNER" = layout ]; then
  if [ -n "${YIBU_API_KEY:-}" ]; then
    ok "key present: the model picks the arrangement, cloud voice speaks it"
  else
    warn "no key: canned arrangement and macOS 'say' for the voice"
    warn "this is the path the demo falls back to, and it is meant to work"
  fi
fi

# 3. Headset: USB tunnel, so the app can use 127.0.0.1 and ignore Wi-Fi.
say "Headset"
lan_ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
app_ip=$(app_address)
adb_up=$([ -n "$(adb devices | sed -n '2p' | grep -w device)" ] && echo 1 || echo 0)

if [ "$WIFI" = 1 ]; then
  [ "$adb_up" = 1 ] && adb reverse --remove tcp:$COORD_PORT >/dev/null 2>&1
  if [ "$app_ip" = "$lan_ip" ]; then
    ok "Wi-Fi mode: the app connects to $app_ip:$COORD_PORT"
  else
    warn "the app is built to reach '$app_ip', but this Mac is $lan_ip"
    warn "run: ./start-demo.sh --set-ip=$lan_ip   then rebuild in Unity and --install"
  fi
  echo "    Both devices need the same network, and it must allow device-to-device"
  echo "    traffic (campus Wi-Fi usually blocks it; a phone hotspot works)."
  echo "    macOS may also ask to allow incoming connections for python: say yes."
elif [ "$adb_up" = 1 ]; then
  adb reverse tcp:$COORD_PORT tcp:$COORD_PORT >/dev/null && ok "USB tunnel ready (app connects to 127.0.0.1:$COORD_PORT)"
  if [ "$app_ip" != "127.0.0.1" ]; then
    warn "USB mode, but the app is built to reach '$app_ip' -- it will NEVER connect."
    warn "fix, in order:  ./start-demo.sh --set-ip=127.0.0.1"
    warn "                rebuild in Unity (menu: Omni > Build Quest APK)"
    warn "                ./start-demo.sh --install"
  fi
fi

if [ "$adb_up" = 0 ] && [ "$INSTALL_APK" = 1 ]; then
  warn "--install needs the USB cable (adb sees no device); nothing was installed"
  warn "plug in, run: adb install -r $APK   (and 'adb tcpip 5555' for wireless logs), then unplug"
fi

if [ "$adb_up" = 1 ]; then
  # A freshly built APK that was never installed is the classic "my change
  # did nothing": Unity writes the file, the headset keeps the old build.
  if [ -f "$APK" ]; then
    apk_epoch=$(stat -f %m "$APK")
    installed=$(adb shell dumpsys package $APP_ID 2>/dev/null | sed -n 's/.*lastUpdateTime=//p' | head -1 | tr -d '\r')
    installed_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$installed" +%s 2>/dev/null || echo 0)
    if [ "$INSTALL_APK" = 1 ]; then
      echo "    installing $APK ..."
      out=$(adb install -r "$APK" 2>&1)
      if echo "$out" | grep -q Success; then
        ok "installed (built $(date -r "$apk_epoch" '+%H:%M'))"
      elif echo "$out" | grep -q INSTALL_FAILED_UPDATE_INCOMPATIBLE; then
        # Different signing keystore than the build already on the headset.
        # The app keeps no data worth saving, so replace it outright.
        warn "installed app was signed with a different keystore; replacing it"
        adb uninstall $APP_ID >/dev/null 2>&1
        out=$(adb install "$APK" 2>&1)
        if echo "$out" | grep -q Success; then
          ok "reinstalled clean (built $(date -r "$apk_epoch" '+%H:%M'))"
        else
          warn "reinstall failed:"
          echo "$out" | sed 's/^/        /'
        fi
      else
        warn "adb install failed:"
        echo "$out" | sed 's/^/        /'
      fi
    elif [ "$apk_epoch" -gt "$installed_epoch" ]; then
      warn "the headset is running an OLDER build (installed $installed, apk built $(date -r "$apk_epoch" '+%Y-%m-%d %H:%M'))"
      warn "your Unity changes are NOT on the device. Re-run with --install"
    else
      # The APK can be "current" against the headset and still be older than
      # the C# it was built from -- which looks exactly like a fix not working.
      newest_src=$(find QuestDemo/Assets -name '*.cs' -newer "$APK" -print -quit 2>/dev/null)
      if [ -n "$newest_src" ]; then
        warn "the APK is older than your Unity sources (e.g. $newest_src)"
        warn "C# changes are NOT on the device: rebuild (Omni > Build Quest APK), then --install"
      else
        ok "installed build is current"
      fi
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
elif [ "$WIFI" != 1 ]; then
  warn "no headset over USB. Plug it in, or use Wi-Fi: ./start-demo.sh --set-ip=$lan_ip (rebuild) then --wifi"
fi

say "Ready"
cat <<'STEPS'
    Put the headset ON first. Off your face the app suspends: no frames,
    no buttons, no mic. Shake the right controller to wake it.

    ---- A: push to talk (tracking) ----------------------------------
      1. Look at the object, or at up to 3 of them.
      2. HOLD A, say "track the laptop and the mug" (speak > 0.5 s), RELEASE A.

      Checkpoints, in order:
        headset: QUEST_STREAM listening (hold A)       <- A registered, mic recording
        headset: QUEST_STREAM utterance=... pcm_bytes= <- speech captured and sent
        coord:   utterance_end ...: mode=ptt audio=N   <- laptop got it, A-mode route
        coord:   turn N: snapshot found ...            <- the frame Huawei will look at
        coord:   calling qwen3.8-omni-flash ...        <- request sent to Huawei
        coord:   turn N: model replied ... targets=... <- Huawei's answer, one point each
        coord:   SAM2 seed ... objects=2 [1:laptop@..  <- points handed to SAM 2
        headset: QUEST_TRACKING first mask frame=...   <- masks arriving, overlay shows
        headset: QUEST_OVERLAY drawn obj=1 ...         <- one line per mask placed
        headset: QUEST_SPEAK <the reply>               <- Android TTS speaks it

    ---- B: continuous conversation ---------------------------------
      1. Press B once. Caption: "Conversation on. Just speak."
      2. Talk with NO button held. Silence ends the turn.
      3. Mid-conversation say "track the mug" to drop an overlay on it.
      4. Press B again to hand the mic back to A.
      (Left-hand X stops tracking. B no longer does.)

      Checkpoints, in order:
        headset: QUEST_MODE live conversation ON       <- B registered, mic handed over
        headset: VoiceBootstrap ... event=vad_opened   <- your speech opened a turn
        headset: VoiceBootstrap ... event=vad_ended    <- silence closed it
        coord:   utterance_end ...: mode=live audio=N  <- B-mode route taken
        coord:   VoiceBootstrap ... mode=live_session  <- live turn started
        headset: VoiceBootstrap ... playback_started   <- streaming reply playing
        coord:   live tracking seed: turn N seeded ... <- only after a "track the ..."
        headset: QUEST_TRACKING first mask frame=...   <- overlay, conversation continues

    ---- C: layout mode (./start-demo.sh --layout) -------------------
      1. Stand facing an empty corner, a couple of metres back.
      2. HOLD the right SIDE trigger, say "fill my corner", RELEASE.
         Three true-size boxes land: sofa, armchair, side table.
      3. Point at a piece on the floor and hold the INDEX trigger to slide it.
         Let go; the clearance line updates.
      4. Press A for the furniture menu: point at a card, pull the trigger.
      5. Press B for resize: hold the trigger on a piece, move your hand
         in and out.
      6. Double-tap the trigger on a piece to rotate it.
      7. Hold the side trigger again to re-summon and start over.

      Checkpoints, in order:
        headset: QUEST_STREAM utterance=... pcm_bytes= <- speech captured
        coord:   utterance_end ...: mode=ptt audio=N   <- laptop got it
        coord:   VoiceBootstrap ... mode=layout        <- layout planner ran
        headset: QUEST_LAYOUT corner from walls at ... <- corner found
                 ("fallback (no walls found)" still works, just less precise)
        headset: QUEST_LAYOUT placed 2-seat sofa ...   <- one line per box
        headset: QUEST_LAYOUT grabbed / dropped ...    <- dragging works
        headset: QUEST_LAYOUT selector open           <- A opened the menu
        headset: QUEST_LAYOUT added armchair ...      <- picked from the menu
        headset: QUEST_LAYOUT resize mode ON          <- B toggled resize
        headset: QUEST_LAYOUT resized sofa to 140% .. <- scaled, listing kept
        headset: QUEST_LAYOUT rotate menu open for .. <- double tap registered
        headset: QUEST_LAYOUT rotated sofa by 90 deg  <- angle applied
        headset: QUEST_LAYOUT fit Roughly -- about ... <- clearance, hedged

      Layout mode needs no SAM 2 and no key. With a key the model picks the
      arrangement and the cloud voice speaks; without one you get the canned
      arrangement and macOS "say". Both are meant to work.

    If it stops, the LAST line you saw tells you where. Common causes:
      C: no boxes at all       -> check "mode=layout": running without --layout
      C: boxes but no voice    -> macOS 'say' is the fallback; check laptop volume
      C: corner says fallback  -> walls too far or too dark; boxes still land ahead
      C: cannot grab a box     -> point at the FLOOR where it stands, not at the air
      C: A/B do nothing        -> summon once first; they are Layout's only after that
      C: resize does nothing   -> press B first; the pointer turns amber in resize mode
      A: no "listening"        -> controller asleep, or headset off your face
      A: "too short"           -> held A under half a second
      A: no "calling qwen..."  -> snapshot missing: look at the object, speak again
      A: "targets=[]"          -> Huawei could not pick an object; say it differently
      A: "did not initialize"  -> a point missed its object; ask again, name them plainly
      A: "call failed"         -> Huawei/network/key problem (the line names it)
      B: no "QUEST_MODE"       -> B press not seen; wake the controller and press again
      B: "onset_dropped"       -> it was still speaking, or the socket is down
      B: reply never audible   -> ffmpeg missing (brew install ffmpeg); the 24 kHz
                                  live reply cannot be resampled for Quest
      B: nothing at all        -> running with --stub; B needs the live model
      B: seed never fires      -> ask for an object plainly ("track the mug");
                                  the planner answers track:null otherwise.
                                  OMNI_LIVE_TRACK=0 disables B-mode seeding
      masks but nothing shown  -> check headset: QUEST_OVERLAY lines (they say why)
      one mask for 2 objects   -> old build installed: the previous renderer merged
                                  every mask into one cyan shape. Rebuild, --install
      white/blank square       -> old build installed: rebuild in Unity, then --install
      neither mode connects    -> USB: app must be built for 127.0.0.1 (see above)

    Ctrl+C stops everything. Full logs: logs/coordinator.log, logs/sam2.log
STEPS
if [ "$WIFI" = 1 ] && [ -z "$(adb devices | sed -n '2p' | grep -w device)" ]; then
  echo "    No adb connection, so no headset logs below. To get them over Wi-Fi:"
  echo "      1. plug in USB once and run: adb tcpip 5555"
  echo "      2. unplug, then: adb connect <headset-ip>:5555   (headset Settings > Wi-Fi > your network)"
  echo
fi
echo
tail -f logs/coordinator.log | grep --line-buffered -v "websockets.server" | sed -u 's/^/coord:   /' &
PIDS+=($!)
tail -f logs/sam2.log | grep --line-buffered -E "stats|Error|error|Traceback" | sed -u 's/^/sam2:    /' &
PIDS+=($!)
if [ -n "$(adb devices | sed -n '2p' | grep -w device)" ]; then
  adb logcat -s Unity | grep --line-buffered -E "QUEST_TRACKING|QUEST_OVERLAY|QUEST_STREAM|QUEST_MODE|QUEST_SPEAK|VoiceBootstrap|CoordinatorClient" \
    | sed -u 's/^.*I Unity   : /headset: /' &
  PIDS+=($!)
fi
wait
