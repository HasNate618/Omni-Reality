using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR;

/// <summary>
/// Quest mic uplink: controller A toggles listening (on by default, green
/// dot shown while on). While listening, automatic VAD opens bounded
/// websocket utterances via <see cref="VoiceActivityGate"/>; a loud onset
/// during playback barges in. While off the mic is fully gated. Streams
/// ~100 ms 16 kHz mono s16le PCM chunks tagged with
/// <see cref="CoordinatorClient.OpenUtteranceId"/> and closes with
/// utterance_end when the gate ends; keyword hooks remain debug fallbacks.
/// </summary>
public class MicUtterance : MonoBehaviour
{
    public const int SampleRate = 16000;
    public const int ChunkSamples = 1600; // ~100 ms
    public const float SilenceRms = VoiceActivityGate.SilenceRms;
    public const int SilenceEndMs = VoiceActivityGate.SilenceChunksToEnd * 100;
    public const int MaxUtteranceMs = VoiceActivityGate.MaxUtteranceChunks * 100;
    /// <summary>Voice barge-in floor during playback (5x the VAD floor).</summary>
    public const float BargeInRms = 0.05f;

    CoordinatorClient _client;
    AudioClip _clip;
    string _utteranceId;
    int _lastPos;
    readonly List<short> _pending = new List<short>(ChunkSamples * 2);
    int _sentChunks;
    VoiceActivityGate _gate = new VoiceActivityGate();
    bool _manualCapture;
    bool _micAuthRequested;
    bool? _lastLoggedMicGrant;
    int _utterancePcmBytes;
    string _utteranceSessionId;
    bool _closing;
    internal PerceptionCapture Perception;

    /// <summary>Chunks streamed this utterance (test seam).</summary>
    public int SentChunks { get { return _sentChunks; } }
    public string UtteranceId { get { return _utteranceId; } }

    void Awake()
    {
        _client = GetComponent<CoordinatorClient>();
        if (_client == null)
            _client = FindObjectOfType<CoordinatorClient>();
    }

    /// <summary>PTT press (or keyword): open the utterance and start the mic.</summary>
    public bool BeginUtterance()
    {
        if (!CanOpenUtterance())
            return false;
        _manualCapture = true;
        OpenUtterance();
        _pending.Clear();
        EnsureMicClip();
        return _clip != null;
    }

    /// <summary>PTT release: flush, close, and let the coordinator run the turn.</summary>
    public void EndUtterance()
    {
        if (_utteranceId == null)
            return;
        try
        {
            if (_clip != null)
                PumpMicManual();
        }
        catch (Exception e)
        {
            Debug.LogWarning("MicUtterance: mic pump failed (" + e.GetType().Name + ")");
        }
        FlushPartialManual();
        CloseUtterance();
        _manualCapture = false;
    }

    /// <summary>Keyword stand-in until on-device spotting lands.</summary>
    public void SimulateKeyword()
    {
        BeginUtterance();
    }

    bool _listening;
    bool _toggleWasHeld;
    bool _replyWasPlaying;
    GameObject _listeningDot;

    /// <summary>Controller A press: flip listening. Off cuts any reply and
    /// gates the mic; an in-flight utterance still finishes its turn.</summary>
    internal void ToggleListening()
    {
        _listening = !_listening;
        VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentMic, "listen_toggled",
            ("on", _listening));
        UpdateListeningDot();
        if (_client != null)
            _client.ShowVoiceFeedback(_listening
                ? "Listening on. Just talk."
                : "Listening off.");
        if (!_listening)
        {
            if (_utteranceId != null)
                EndUtterance();
            SpeakCloudPlayer player = _client != null ? _client.SpeakPlayer : null;
            if (player != null)
                player.StopPlayback();
        }
    }

    /// <summary>Green dot while listening: small unlit sphere pinned below
    /// the center-eye view. Hidden when listening is off.</summary>
    internal void UpdateListeningDot()
    {
        if (_listeningDot == null)
            BuildListeningDot();
        if (_listeningDot != null)
            _listeningDot.SetActive(_listening);
    }

    void BuildListeningDot()
    {
        try
        {
            Transform anchor = _client != null ? _client.CenterEye : null;
            if (anchor == null && Camera.main != null)
                anchor = Camera.main.transform;
            _listeningDot = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            _listeningDot.name = "ListeningDot";
            var renderer = _listeningDot.GetComponent<Renderer>();
            if (renderer != null)
            {
                var mat = new Material(Shader.Find("Unlit/Color"));
                mat.color = Color.green;
                renderer.material = mat;
            }
            var collider = _listeningDot.GetComponent<Collider>();
            if (collider != null)
#if UNITY_EDITOR
                UnityEngine.Object.DestroyImmediate(collider);
#else
                UnityEngine.Object.Destroy(collider);
#endif
            _listeningDot.transform.localScale = Vector3.one * 0.015f;
            if (anchor != null)
            {
                _listeningDot.transform.SetParent(anchor, false);
                _listeningDot.transform.localPosition = new Vector3(0f, -0.06f, 0.6f);
            }
            _listeningDot.SetActive(_listening);
        }
        catch (Exception)
        {
            _listeningDot = null;
        }
    }

    /// <summary>Primary button held on either controller. OVRInput first,
    /// OpenXR input devices as fallback (dead under some loaders).</summary>
    internal static bool PttHeld()
    {
        try
        {
            if (OVRInput.Get(OVRInput.Button.One))
                return true;
        }
        catch (Exception)
        {
        }
        try
        {
            var dev = InputDevices.GetDeviceAtXRNode(XRNode.RightHand);
            if (dev.TryGetFeatureValue(CommonUsages.primaryButton, out bool p) && p)
                return true;
            var left = InputDevices.GetDeviceAtXRNode(XRNode.LeftHand);
            return left.TryGetFeatureValue(CommonUsages.primaryButton, out bool q) && q;
        }
        catch (Exception)
        {
            return false;
        }
    }

    void PollToggleButton()
    {
        bool held = PttHeld();
        if (held && !_toggleWasHeld)
            ToggleListening();
        _toggleWasHeld = held;
    }

    void Update()
    {
        TryEnsureAutoCapture();
        PollToggleButton();
        if (_client == null || !_client.IsConnected
            || (_utteranceId != null && _utteranceSessionId != _client.SessionId))
        {
            DropLocalTurn();
            return;
        }
        if (_closing || _client.AwaitingReply)
        {
            DiscardMicWindow();
            return;
        }
        bool playing = _client.SpeakPlayer != null && _client.SpeakPlayer.IsPlaying;
        if (playing)
        {
            _replyWasPlaying = true;
            if (_listening)
                PumpMicBargeIn();
            else
                DiscardMicWindow();
            return;
        }
        if (_replyWasPlaying && _listening && !_client.AwaitingReply)
        {
            // One-shot: the reply just finished, stand down until next press.
            _replyWasPlaying = false;
            ToggleListening();
            return;
        }
        _replyWasPlaying = false;
        if (!_listening || _clip == null)
        {
            if (!_listening)
                DiscardMicWindow();
            return;
        }
        PumpMicVad();
    }

    void DiscardMicWindow()
    {
        _pending.Clear();
        _gate = new VoiceActivityGate();
        if (_clip != null) _lastPos = Mathf.Max(0, Microphone.GetPosition(null));
    }

    void DropLocalTurn()
    {
        if (_closing && Perception != null) Perception.Cancel();
        _closing = false;
        _utteranceId = null;
        _utteranceSessionId = null;
        _manualCapture = false;
        if (_client != null) _client.OpenUtteranceId = null;
        DiscardMicWindow();
    }

    void OnDisable()
    {
        DropLocalTurn();
        if (_clip != null)
        {
            Microphone.End(null);
            Destroy(_clip);
            _clip = null;
        }
    }

    void TryEnsureAutoCapture()
    {
        if (_clip != null)
            return;
#if UNITY_EDITOR
        EnsureMicClip();
        return;
#endif
        MicRecordPermission.RequestOnce(ref _micAuthRequested);
        bool granted = MicRecordPermission.IsGranted();
        MaybeLogMicPermission(granted);
        if (!granted)
            return;
        EnsureMicClip();
    }

    void EnsureMicClip()
    {
        if (_clip != null)
            return;
#if UNITY_ANDROID && !UNITY_EDITOR
        if (!MicRecordPermission.IsGranted())
            return;
#endif
        try
        {
            _clip = Microphone.Start(null, true, 10, SampleRate);
            _lastPos = 0;
        }
        catch (Exception e)
        {
            VoiceBootstrapLog.MicFailed(e.GetType().Name);
            _clip = null;
        }
        if (_clip != null)
            VoiceBootstrapLog.MicStarted();
    }

    void MaybeLogMicPermission(bool granted)
    {
        if (!MicRecordPermission.ShouldLogGrantTransition(
                _micAuthRequested, _lastLoggedMicGrant, granted))
            return;
        _lastLoggedMicGrant = granted;
        VoiceBootstrapLog.MicPermission(granted);
    }

    /// <summary>Loud onset during playback interrupts (listening only).</summary>
    void PumpMicBargeIn()
    {
        if (_clip == null || _utteranceId != null || !_listening)
            return;
        AppendMicSamples();
        while (_pending.Count >= ChunkSamples)
        {
            var chunk = TakeChunk();
            VoiceActivityDecision decision = _gate.Observe(chunk);
            if (decision.State == VoiceActivityState.Started)
            {
                if (!IsBargeInLoud(decision.Chunks))
                {
                    _gate = new VoiceActivityGate();
                    continue;
                }
                SpeakCloudPlayer player = _client.SpeakPlayer;
                if (player != null)
                    player.StopPlayback();
                _client.AbandonReply();
                OpenUtterance();
                VoiceBootstrapLog.VadOpened(decision.Chunks != null ? decision.Chunks.Count : 0);
                SendChunks(decision.Chunks);
            }
            else if (decision.State == VoiceActivityState.Ended)
            {
                _gate = new VoiceActivityGate();
            }
        }
    }

    /// <summary>Loud-enough pre-roll to count as a voice interruption.</summary>
    public static bool IsBargeInLoud(List<List<short>> chunks)
    {
        if (chunks == null || chunks.Count == 0)
            return false;
        double sum = 0;
        long count = 0;
        foreach (List<short> chunk in chunks)
        {
            if (chunk == null)
                continue;
            foreach (short s in chunk)
            {
                double v = s / 32768.0;
                sum += v * v;
                count++;
            }
        }
        if (count == 0)
            return false;
        return (float)System.Math.Sqrt(sum / count) >= BargeInRms;
    }

    void PumpMicVad()
    {
        AppendMicSamples();
        while (_pending.Count >= ChunkSamples)
        {
            var chunk = TakeChunk();
            VoiceActivityDecision decision = _gate.Observe(chunk);
            ApplyVadDecision(decision);
        }
    }

    void PumpMicManual()
    {
        AppendMicSamples();
        while (_pending.Count >= ChunkSamples)
        {
            var chunk = TakeChunk();
            if (Rms(chunk) >= SilenceRms)
                SendChunk(chunk);
        }
    }

    void AppendMicSamples()
    {
        int pos = Microphone.GetPosition(null);
        if (pos < 0)
            return;
        int available = pos >= _lastPos
            ? pos - _lastPos
            : (_clip.samples - _lastPos) + pos;
        if (available <= 0)
            return;
        var buf = new float[available];
        _clip.GetData(buf, _lastPos);
        _lastPos = pos;
        foreach (float f in buf)
        {
            float clamped = Mathf.Clamp(f, -1f, 1f);
            _pending.Add((short)(clamped * 32767f));
        }
    }

    List<short> TakeChunk()
    {
        var chunk = _pending.GetRange(0, ChunkSamples);
        _pending.RemoveRange(0, ChunkSamples);
        return chunk;
    }

    void ApplyVadDecision(VoiceActivityDecision decision)
    {
        switch (decision.State)
        {
            case VoiceActivityState.Idle:
                break;
            case VoiceActivityState.Started:
                if (!CanOpenUtterance())
                {
                    VoiceBootstrapLog.OnsetDropped(DroppedOnsetReason());
                    _gate = new VoiceActivityGate();
                    break;
                }
                OpenUtterance();
                VoiceBootstrapLog.VadOpened(decision.Chunks != null ? decision.Chunks.Count : 0);
                SendChunks(decision.Chunks);
                break;
            case VoiceActivityState.Streaming:
                if (_utteranceId != null)
                    SendChunks(decision.Chunks);
                break;
            case VoiceActivityState.Ended:
                if (_utteranceId != null)
                {
                    SendChunks(decision.Chunks);
                    CloseUtterance();
                }
                break;
        }
    }

    bool CanOpenUtterance()
    {
        if (_client == null || !_client.IsConnected || _client.AwaitingReply || _utteranceId != null)
            return false;
        SpeakCloudPlayer player = _client.SpeakPlayer;
        return player == null || !player.IsPlaying;
    }

    string DroppedOnsetReason()
    {
        if (_client == null || !_client.IsConnected)
            return "socket_unavailable";
        SpeakCloudPlayer player = _client.SpeakPlayer;
        if (player != null && player.IsPlaying)
            return "playback_active";
        return "socket_unavailable";
    }

    void OpenUtterance()
    {
        _utteranceId = Guid.NewGuid().ToString("N");
        _client.OpenUtteranceId = _utteranceId;
        _utteranceSessionId = _client.SessionId;
        _sentChunks = 0;
        _utterancePcmBytes = 0;
    }

    void CloseUtterance()
    {
        if (_closing || _utteranceId == null) return;
        string id = _utteranceId;
        VoiceBootstrapLog.VadEnded(_sentChunks, _utterancePcmBytes);
        if (_client == null || !_client.IsConnected || _client.SessionId != _utteranceSessionId)
        {
            DropLocalTurn();
            return;
        }
        _closing = true;
        bool longEnough = _utterancePcmBytes >= SampleRate; // 0.5 seconds s16le.
        if (!longEnough)
        {
            _client.ShowVoiceFeedback("That was too short. Please ask again.");
            FinishUtterance(id, null, null, null, false);
        }
        else if (_client.PerceptionEnabled && Perception != null)
        {
            Perception.Capture((env, jpeg, reason) => FinishUtterance(id, env, jpeg, reason, true));
        }
        else
        {
            FinishUtterance(id, null, null, _client.PerceptionEnabled ? "camera_down" : null, true);
        }
    }

    void FinishUtterance(string id, CaptureEnvelope env, byte[] jpeg, string reason, bool expectReply)
    {
        if (_utteranceId != id || _client == null || !_client.IsConnected
            || _client.SessionId != _utteranceSessionId)
        {
            DropLocalTurn();
            return;
        }
        if (jpeg != null && !_client.EnqueuePerceptionFrame(id, env, jpeg))
        {
            DropLocalTurn();
            return;
        }
        if (reason != null)
            _client.ShowVoiceFeedback(reason == "dark_image"
                ? "Camera view is dark. Uncover the camera or try more light."
                : "Camera image unavailable. Check camera access and try again.");
        _client.EnqueueUtteranceEnd(id, expectReply);
        _utteranceId = null;
        _utteranceSessionId = null;
        _closing = false;
    }

    void FlushPartialManual()
    {
        if (_pending.Count >= ChunkSamples / 2 && _client != null && _utteranceId != null)
        {
            var chunk = new List<short>(_pending);
            _pending.Clear();
            if (Rms(chunk) >= SilenceRms)
                SendChunk(chunk);
        }
        else
        {
            _pending.Clear();
        }
    }

    void SendChunks(List<List<short>> chunks)
    {
        if (chunks == null)
            return;
        foreach (List<short> chunk in chunks)
            SendChunk(chunk);
    }

    void SendChunk(List<short> chunk)
    {
        if (_utteranceId == null || _client == null || chunk == null || chunk.Count == 0)
            return;
        var bytes = new byte[chunk.Count * 2];
        for (int i = 0; i < chunk.Count; i++)
        {
            bytes[i * 2] = (byte)(chunk[i] & 0xFF);
            bytes[i * 2 + 1] = (byte)((chunk[i] >> 8) & 0xFF);
        }
        _client.EnqueueAudioChunk(_utteranceId, Convert.ToBase64String(bytes));
        _sentChunks++;
        _utterancePcmBytes += bytes.Length;
    }

    static float Rms(List<short> chunk)
    {
        double sum = 0;
        foreach (short s in chunk)
        {
            double v = s / 32768.0;
            sum += v * v;
        }
        return (float)Math.Sqrt(sum / Math.Max(1, chunk.Count));
    }
}
