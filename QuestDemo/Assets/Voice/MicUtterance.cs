using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Quest mic uplink (voice spec §4 lifecycle steps 1–2): push-to-talk opens
/// an utterance, streams ~100 ms 16 kHz mono s16le PCM chunks, tags frames
/// via <see cref="CoordinatorClient.OpenUtteranceId"/>, and closes with
/// utterance_end on release. A keyword hook (<see cref="SimulateKeyword"/>)
/// stands in until on-device keyword spotting lands. An RMS gate drops
/// silent chunks; the coordinator drops turns under 0.5 s of PCM.
/// </summary>
public class MicUtterance : MonoBehaviour
{
    public const int SampleRate = 16000;
    public const int ChunkSamples = 1600; // ~100 ms
    public const float SilenceRms = 0.01f;

    CoordinatorClient _client;
    AudioClip _clip;
    string _utteranceId;
    int _lastPos;
    readonly List<short> _pending = new List<short>(ChunkSamples * 2);
    int _sentChunks;

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
        _utteranceId = Guid.NewGuid().ToString("N");
        _client.OpenUtteranceId = _utteranceId;
        _pending.Clear();
        _sentChunks = 0;
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
        return true;
    }

    /// <summary>PTT release: flush, close, and let the coordinator run the turn.</summary>
    public void EndUtterance()
    {
        if (_utteranceId == null)
            return;
        try
        {
            if (_clip != null)
            {
                PumpMic();
                Microphone.End(null);
            }
        }
        catch (Exception e)
        {
            Debug.LogWarning("MicUtterance: mic stop failed (" + e.GetType().Name + ")");
        }
        _clip = null;
        FlushPartial();
        string id = _utteranceId;
        _utteranceId = null;
        if (_client != null)
            _client.EnqueueUtteranceEnd(id);
    }

    /// <summary>Keyword stand-in until on-device spotting lands.</summary>
    public void SimulateKeyword()
    {
        BeginUtterance();
    }

    void Update()
    {
        if (_utteranceId == null || _clip == null)
            return;
        PumpMic();
    }

    void PumpMic()
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
        while (_pending.Count >= ChunkSamples)
        {
            var chunk = _pending.GetRange(0, ChunkSamples);
            _pending.RemoveRange(0, ChunkSamples);
            if (Rms(chunk) >= SilenceRms)
                SendChunk(chunk);
        }
    }

    void FlushPartial()
    {
        if (_pending.Count >= ChunkSamples / 2 && _client != null)
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

    void SendChunk(List<short> chunk)
    {
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
