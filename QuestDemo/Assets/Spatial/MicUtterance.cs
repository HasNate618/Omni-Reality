using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Quest mic uplink: automatic VAD opens bounded websocket utterances via
/// <see cref="VoiceActivityGate"/>; push-to-talk and keyword hooks remain
/// debug fallbacks. Streams ~100 ms 16 kHz mono s16le PCM chunks tagged
/// with <see cref="CoordinatorClient.OpenUtteranceId"/> and closes with
/// utterance_end when the gate ends or PTT releases.
/// </summary>
public class MicUtterance : MonoBehaviour
{
    public const int SampleRate = 16000;
    public const int ChunkSamples = 1600; // ~100 ms
    public const float SilenceRms = VoiceActivityGate.SilenceRms;
    public const int SilenceEndMs = VoiceActivityGate.SilenceChunksToEnd * 100;
    public const int MaxUtteranceMs = VoiceActivityGate.MaxUtteranceChunks * 100;

    CoordinatorClient _client;
    AudioClip _clip;
    string _utteranceId;
    int _lastPos;
    readonly List<short> _pending = new List<short>(ChunkSamples * 2);
    int _sentChunks;
    VoiceActivityGate _gate = new VoiceActivityGate();
    bool _manualCapture;
    bool _micAuthRequested;

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
        if (_utteranceId != null || _client == null)
            return false;
        _manualCapture = true;
        _utteranceId = Guid.NewGuid().ToString("N");
        _client.OpenUtteranceId = _utteranceId;
        _pending.Clear();
        _sentChunks = 0;
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

    void Update()
    {
        if (_manualCapture)
        {
            if (_utteranceId == null || _clip == null)
                return;
            PumpMicManual();
            return;
        }

        TryEnsureAutoCapture();
        if (_clip == null)
            return;
        PumpMicVad();
    }

    void TryEnsureAutoCapture()
    {
        if (_clip != null)
            return;
#if UNITY_ANDROID && !UNITY_EDITOR
        if (!_micAuthRequested)
        {
            _micAuthRequested = true;
            Application.RequestUserAuthorization(UserAuthorization.Microphone);
        }
        if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
            return;
#endif
        EnsureMicClip();
    }

    void EnsureMicClip()
    {
        if (_clip != null)
            return;
        try
        {
            _clip = Microphone.Start(null, true, 10, SampleRate);
            _lastPos = 0;
        }
        catch (Exception e)
        {
            Debug.LogWarning("MicUtterance: mic unavailable (" + e.GetType().Name + ")");
            _clip = null;
        }
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
                    _gate = new VoiceActivityGate();
                    break;
                }
                OpenUtterance();
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
        if (_client == null || !_client.ConnectRequested || _utteranceId != null)
            return false;
        SpeakCloudPlayer player = _client.SpeakPlayer;
        return player == null || !player.IsPlaying;
    }

    void OpenUtterance()
    {
        _utteranceId = Guid.NewGuid().ToString("N");
        _client.OpenUtteranceId = _utteranceId;
        _sentChunks = 0;
    }

    void CloseUtterance()
    {
        string id = _utteranceId;
        _utteranceId = null;
        if (_client != null && id != null)
            _client.EnqueueUtteranceEnd(id);
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
