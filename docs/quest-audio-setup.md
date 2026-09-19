# Quest camera + push-to-talk setup

## What it is

Set up the Quest camera and microphone so a spoken request such as **“Track the
laptop”** initializes the existing SAM 2 tracker without clicking an object.

```text
Quest RGB stream + recorded speech → laptop coordinator
Selected snapshot + speech → Huawei → image point
Exact selected snapshot + point → SAM 2 → subsequent Quest frames
Tracking masks → Unity result handoff
```

Quest supplies the video; Huawei selects the object once per request; SAM 2
segments and tracks it. This setup supports one active object. The detailed
frame, buffering, and result contracts live in
[SAM 2 streaming](omni-sam2-streaming.md#quest--voice-automatic-initialization).

## 1. Start the Python processes

Use two terminals and the existing **separate** virtual environments. If SAM 2
is not already installed, follow its
[platform setup instructions](omni-sam2-streaming.md#setup).

For the coordinator, from `provider/`:

```bash
python3 -m venv .venv                 # first setup only
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Terminal A: SAM 2

Working directory: `sam2/`. On Mac/Linux:

```bash
source .venv/bin/activate
SAM2_WS_PORT=8766 python sam2_ws_server.py
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
$env:SAM2_WS_PORT = "8766"
python sam2_ws_server.py
```

Wait for `Starting SAM 2 WebSocket Server`.

### Terminal B: coordinator

Working directory: `provider/`, with its virtual environment activated:

```bash
python -m coordinator.server --planner stub --sam2-url ws://127.0.0.1:8766
```

Start with `stub` to test the camera, audio transport, and SAM 2 without credits:
it selects the **centre of the image regardless of what you say**. Put the
laptop at the image centre for this check.

Once that works, stop Terminal B with Ctrl+C and use the live Huawei planner:

```bash
python -m coordinator.server --planner yibu --sam2-url ws://127.0.0.1:8766
```

`YIBU_API_KEY` must already be exported in **Terminal B**. The app never reads a
`.env` file. The live command spends gateway credit; its purpose is `track-object`.
See the [provider contract](omni-provider-api.md) for credentials, expiry, and audit.

If SAM 2 is on a different GPU computer, use that computer's reachable address
in `--sam2-url`. Quest still connects only to the coordinator on port **8765**;
SAM 2 uses **8766** to avoid a port collision.

## 2. Configure Unity

1. Open **`QuestDemo/`** in Unity Hub with **6000.6.2f1**. Install that editor's
   **Android Build Support, Android SDK & NDK Tools, and OpenJDK** in Hub.
2. Let the project import its pinned Meta packages. Open
   `Assets/Scenes/SampleScene.unity`.
3. Choose **Omni → Configure Quest Tracking**. This creates/selects
   `Assets/Resources/QuestTrackingSettings.asset`; the setting survives the
   scene rebuild performed by the build script.
4. In its Inspector, use these initial values:

| Setting | Value |
| --- | --- |
| Enable Tracking | On |
| Stream FPS | `3` |
| Max Image Side | `640` |
| JPEG Quality | `70` |
| Laptop IPv4 | See below |

### USB connection

Set **Laptop IPv4** to `127.0.0.1`. The ADB reverse command in the next section
forwards that connection to the computer over USB.

### Wi-Fi connection

Set **Laptop IPv4** to the coordinator computer's LAN IPv4. Both devices must
be on a network that permits device-to-device connections; allow TCP **8765**
through the computer's firewall. `localhost` on Quest is not the computer on
Wi-Fi.

## 3. Build, install, and launch

1. Choose **Omni → Build Quest APK**. It runs the existing XR/AR setup and adds
   `QuestStreamInput` to `ARDirector`. Output is `QuestDemo/Builds/QuestDemo.apk`.
   Use this menu rather than a normal build of an unprepared scene.
2. Enable developer mode/USB debugging on the Quest and accept the USB prompt.
3. From the repository root, using Android platform-tools (`adb`):

```bash
adb devices
adb install -r QuestDemo/Builds/QuestDemo.apk
```

For the **USB connection**, also run:

```bash
adb reverse tcp:8765 tcp:8765
```

Then launch the app:

```bash
adb shell monkey -p com.omni.questdemo -c android.intent.category.LAUNCHER 1
```

Repeat `adb reverse` after USB reconnects. If `adb` isn't on PATH, use the
platform-tools executable inside the Android SDK shown in Unity's
**Preferences → External Tools**. More build-specific details are in
[questdemo-build.md](questdemo-build.md).

Changing the tracking settings asset requires rebuilding/reinstalling. Disable
**Enable Tracking** to restore the original trigger-to-spatial-mark mode.

## 4. Grant permissions and speak

Grant **camera and microphone permissions** in the headset. Face the laptop.

1. **Hold A on the right controller.**
2. Say **“Track the laptop.”**
3. **Release A.**

Speech must last **0.5–15 seconds**. Release selects a fresh snapshot and
associates it with the recorded audio. The coordinator uses Huawei's point to
seed SAM 2 on that exact snapshot, then feeds subsequent Quest frames.

**B** stops the current selection/tracking. A later A-button utterance selects
a new target. No object clicking is involved.

## How to verify

### On the headset

Use Unity's device log or:

```bash
adb logcat -s Unity
```

Look for:

- `QUEST_STREAM frame=... jpeg=... size=...`: actual camera JPEGs are sent.
- `QUEST_STREAM utterance=... frame=... pcm_bytes=...`: recorded speech is
  bound to a particular snapshot.
- `QUEST_TRACKING selecting`, `initializing`, then `tracking`.
- `QUEST_TRACKING first mask frame=...`: a SAM 2 result reached Unity.

The coordinator logs `SAM2 seed frame_id=... x=... y=... obj_id=1`. Its frame ID
must match the selected snapshot, not the latest live frame.

The existing Unity visualization consumes `CoordinatorClient.TrackingResultReceived`.
See the [result handoff](omni-sam2-streaming.md#unity-result-handoff) for fields
and subscription details. Receiving a mask does not itself draw a marker.

### Without a headset

With both servers running, from `provider/`:

```bash
# macOS synthesized speech; stub coordinator uses no gateway credit.
python -m tools.fake_quest --say "Track the laptop" --jpeg ../assets/laptop.jpg --tracking
# Other platforms: supply a 16-kHz mono signed-16-bit WAV instead of --say.
python -m tools.fake_quest --wav request.wav --jpeg ../assets/laptop.jpg --tracking
```

This repeats a still at 3 fps and waits for ten frame-addressed masks. It proves
the handoff and ongoing transport, not real camera motion or Huawei accuracy.
Recorded test results and offline test commands are in
[SAM 2 streaming verification](omni-sam2-streaming.md#integration-verification).

## Troubleshooting

- **No `QUEST_STREAM`:** check the tracking asset is enabled, camera permission,
  native Quest build, and `Laptop IPv4`/ADB reverse.
- **No utterance:** grant microphone permission and speak while holding **A**
  for at least half a second. Releasing selects the snapshot.
- **`selecting` then error:** check `YIBU_API_KEY` in the coordinator terminal,
  key expiry, visible target, and the gateway audit ledger.
- **`initializing` then error:** check SAM 2's startup completed, the correct
  8766 URL, and connectivity from the coordinator machine.
- **Catch-up timeout:** reduce Stream FPS; one object is the integration target.
- **First mask log but no visual:** connect the existing Unity visualization to
  `TrackingResultReceived`; transport success alone does not draw a marker.
