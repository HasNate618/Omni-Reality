# QuestDemo build (Unity on NixOS → Quest 3S)

What it is: the Unity 6 LTS demo app in `QuestDemo/` and the scripted pipeline that builds, deploys, and verifies it on Quest 3S from NixOS. Unity renders on-device; this doc covers getting an APK there.

## Environment (flake shell)

- `flake.nix` provides a `buildFHSEnv` shell: unityhub, pinned Unity CLI, adb, and compat libs Unity 6 needs (`libxml2_13` for legacy `.so.2`, icu, ncurses, gtk3, …).
- Unity CLI is shell-scoped, never installed to `~/.local/bin`.
- Run commands with `nix run .# -- -c '...'` — `nix develop --command` does not forward commands through FHS shells.
- Editor: Unity 6 LTS `6000.6.2f1` via Hub + Android Build Support (OpenJDK 17, NDK r27c, SDK platforms 34/36/37) installed with `unity install-modules`.

## Licensing workaround (hard)

Hub 3.19.5's licensing client speaks an older protocol than Editor 6000.6 (v1.18.3 mismatch → 505 error). Every batchmode invocation must first launch a compatible client, then build:

```bash
nix run .# -- -c '
  $HOME/Unity/Hub/Editor/6000.6.2f1/Editor/Data/Resources/Licensing/Client/Unity.Licensing.Client \
    --namedPipe Unity-LicenseClient-nate-6000.6.2 > /tmp/lic-serv.log 2>&1 & LIC_PID=$!;
  sleep 6;
  $UNITY_EDITOR_6000 -batchmode -nographics -quit -projectPath <path> -executeMethod BuildAndroid.Build -logFile <log>;
  kill $LIC_PID 2>/dev/null'
```

## Build pipeline (all scripted, batchmode-safe)

`QuestDemo/Assets/Editor/BuildAndroid.Build` is the single entry point — setup, validate, and build in one Editor run:

1. `XRSetup.EnsureXR` — XR Management 4.7 + OpenXR loader, ARM64-only.
2. `BuildAndroid` enables `MetaQuestFeature` + `MetaXRFeature` (the OVRPlugin bridge) for Android and sets `OVRProjectConfig.insightPassthroughSupport = Supported`. The SDK's auto-enable dialogs never fire headless, so the script does it.
3. `ARSetup.EnsureAR` — wipes AR remnants, instantiates a fresh `OVRCameraRig` (unpacked), sets `OVRManager.isInsightPassthroughEnabled`, adds an `OVRPassthroughLayer` (Underlay), sets all rig cameras to SolidColor + alpha-0, disables the legacy Main Camera, anchors DemoCube. Fails loudly if the rig is incomplete.
4. `ARGradleFix` (`IPostGenerateGradleAndroidProject`) + `mainTemplate.gradle` — `pickFirst` for the duplicate `libopenxr_loader.so` (Unity OpenXR vs Meta OVRPlugin; Meta's own loader wins once MetaXRFeature is on).

Meta XR SDK 205 (`core` + `mrutilitykit`) comes from the scoped registry `https://npm.developer.oculus.com`.

## Manifest / project-config contract

- `AndroidManifest.xml`: `com.oculus.intent.category.VR`, `HEADSET_CAMERA` permission, `BaseUnityGameActivityTheme` (avoids the AppCompat crash), `com.oculus.supportedDevices` incl. quest3s.
- `com.oculus.feature.PASSTHROUGH` is injected at build time by `OVRManifestPreprocessor` **only if** `OculusProjectConfig.insightPassthroughSupport != None`. Without the tag the runtime never initializes Insight passthrough (compositor shows `app 0`, background stays black). Verify in the merged manifest under `Library/Bee/.../packaged_manifests/`.
- Secrets: the YIBU key never enters Unity, code, or the repo. `DevAgentSettings.asset`'s SDK-bundled default `accessToken` is blanked (trips secret scanners).

## How to verify

### Unity Editor menus (Mac / Windows / Linux)

- **Omni → Configure Quest Tracking** creates a persistent Resources settings
  asset for the laptop endpoint and video cadence. **Omni → Build Quest APK**
  invokes the existing `BuildAndroid.Build` setup/build pipeline. **Omni → Setup
  Quest Scene** prepares the scene for inspection without an APK build.
- `ARSetup` now attaches `QuestStreamInput` alongside `SpatialRuntime`. It is
  active only when the tracking settings asset is enabled. This mode uses
  right-controller **A** for push-to-talk and **B** for stop.
- The Android manifest explicitly declares `INTERNET` and `RECORD_AUDIO`,
  permits the existing local `ws://` transport, and keeps `HEADSET_CAMERA`.
  The app requests microphone permission; the existing camera permission
  path is reused.
- Install Android Build Support with its SDK/NDK/OpenJDK through Unity Hub.
  The full two-server, USB/Wi-Fi, and headset setup is documented once in
  [Quest camera + push-to-talk setup](quest-audio-setup.md).

### Existing build/device checks

- Build: the licensing-wrapped command above; expect `BUILD SUCCEEDED: Builds/QuestDemo.apk` in the log (~52 MB).
- Deploy/launch: `adb install -r QuestDemo/Builds/QuestDemo.apk`, then `adb shell monkey -p com.omni.questdemo -c android.intent.category.LAUNCHER 1`.
- On-device: real room visible (passthrough), cube world-locked, no skybox.
- Logs: `adb logcat -d | grep -i unity | grep -iE "OVR|passthrough|error"` — expect `XR_FB_passthrough` from "Meta XR Feature", no `Failed to initialize Insight Passthrough`. Compositor `app 0` in `Passthrough usage state` lines means no layer submitted (check the manifest tag + `isInsightPassthroughEnabled`).
- Perception Q&A adds no scene edits: `SpatialRuntime` wires one `PerceptionCapture`
  (single AsyncGPUReadback frame, never a video loop) to `MicUtterance` at
  coordinator startup, plus a head-relative `VoiceCaption` for speech/recovery text.
- Do not "fix" vendored files or SDK package-cache sources to satisfy linters.
