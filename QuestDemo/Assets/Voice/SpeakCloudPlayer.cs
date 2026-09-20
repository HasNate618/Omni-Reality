using System;
using UnityEngine;

/// <summary>
/// Plays coordinator cloud speech (16 kHz mono PCM s16le in speak.audio).
/// Voice milestone requires non-null audio; caption-only is degraded mode.
/// </summary>
[RequireComponent(typeof(AudioSource))]
public class SpeakCloudPlayer : MonoBehaviour
{
    public const int SampleRate = 16000;
    public const string EncodingPcmS16Le = "pcm_s16le";
    /// <summary>Streaming ring capacity: 30 s of 16 kHz mono.</summary>
    public const int StreamMaxSamples = 16000 * 30;

    AudioSource _source;
    string _activeTurnId;
    int _activeTurnNumeric;
    bool _wasPlaying;
    AudioClip _streamClip;
    int _streamPos;
    int _streamTurn;

    /// <summary>Turn currently streaming, or 0 when idle.</summary>
    public int StreamingTurnId { get { return _streamTurn; } }
    /// <summary>Samples written for the streaming turn.</summary>
    public int StreamedSamples { get { return _streamPos; } }

    /// <summary>True while cloud PCM is actively playing (barge-in / mic gate).</summary>
    public bool IsPlaying
    {
        get { return _source != null && _source.isPlaying; }
    }

    void Awake()
    {
        _source = GetComponent<AudioSource>();
        _source.playOnAwake = false;
        _source.spatialBlend = 0f;
    }

    void Update()
    {
        bool playing = IsPlaying;
        if (_wasPlaying && !playing && _activeTurnNumeric > 0)
            VoiceBootstrapLog.PlaybackFinished(_activeTurnNumeric);
        _wasPlaying = playing;
    }

    /// <summary>Stop playback for barge-in / stop_speak. Keeps the single
    /// ring clip for reuse; only playback state resets.</summary>
    public void StopPlayback()
    {
        if (_wasPlaying && _activeTurnNumeric > 0)
            VoiceBootstrapLog.PlaybackFinished(_activeTurnNumeric);
        if (_source != null && _source.isPlaying)
            _source.Stop();
        _activeTurnId = null;
        _activeTurnNumeric = 0;
        _wasPlaying = false;
        _streamTurn = 0;
        _streamPos = 0;
    }

    /// <summary>Release a finished turn without cutting its audible tail.
    /// Without this, the next turn's chunks are rejected as foreign and
    /// the reply goes silent while the server believes it played.</summary>
    public void FinishTurn(int turnId)
    {
        if (turnId != 0 && turnId == _streamTurn)
            _streamTurn = 0;
    }

    /// <summary>
    /// Append one streamed speech chunk. Starts a 30 s ring clip on the
    /// first chunk and plays while later chunks arrive. Returns false for
    /// empty audio, a foreign turn, or overflow (old turn keeps playing).
    /// </summary>
    public bool AppendChunk(int turnId, byte[] pcmS16Le)
    {
        int pcmBytes = pcmS16Le != null ? pcmS16Le.Length : 0;
        if (pcmS16Le == null || pcmS16Le.Length < 2 || pcmS16Le.Length % 2 != 0)
            return false;
        if (_streamTurn != 0 && turnId != _streamTurn)
            return false;
        int samples = pcmS16Le.Length / 2;
        bool isNewTurn = _streamTurn == 0;
        int writePos = isNewTurn ? 0 : _streamPos;
        if (writePos + samples > StreamMaxSamples)
        {
            VoiceBootstrapLog.PlaybackRejected(turnId, pcmBytes, "stream_overflow");
            return false;
        }
        if (_streamClip == null)
            _streamClip = AudioClip.Create("live_speak", StreamMaxSamples, 1, SampleRate, false);
        if (_streamClip == null)
        {
            VoiceBootstrapLog.PlaybackRejected(turnId, pcmBytes, "clip_build_failed");
            return false;
        }
        float[] floats;
        if (!TryConvertPcm(pcmS16Le, out floats))
            return false;
        _streamClip.SetData(floats, writePos);
        _streamPos = writePos + samples;
        if (isNewTurn)
        {
            _streamTurn = turnId;
            _activeTurnId = turnId.ToString();
            _activeTurnNumeric = turnId;
            VoiceBootstrapLog.PlaybackAccepted(turnId, pcmBytes);
            _source.clip = _streamClip;
            _source.Play();
            _wasPlaying = true;
            VoiceBootstrapLog.PlaybackStarted(turnId);
        }
        return true;
    }

    /// <summary>Pure s16le → float conversion. False for empty/odd input.</summary>
    public static bool TryConvertPcm(byte[] pcmS16Le, out float[] floats)
    {
        floats = null;
        if (pcmS16Le == null || pcmS16Le.Length < 2 || pcmS16Le.Length % 2 != 0)
            return false;
        floats = new float[pcmS16Le.Length / 2];
        for (int i = 0; i < floats.Length; i++)
            floats[i] = (short)(pcmS16Le[i * 2] | (pcmS16Le[i * 2 + 1] << 8)) / 32768f;
        return true;
    }

    /// <summary>
    /// Play cloud PCM for a turn. Returns false when audio is missing (degraded).
    /// </summary>
    public bool TryPlay(int turnId, string text, byte[] pcmS16Le)
    {
        int pcmBytes = pcmS16Le != null ? pcmS16Le.Length : 0;
        if (pcmS16Le == null || pcmS16Le.Length < 2)
        {
            VoiceBootstrapLog.PlaybackRejected(turnId, pcmBytes, "missing_pcm");
            return false;
        }
        StopPlayback();
        _activeTurnId = turnId.ToString();
        _activeTurnNumeric = turnId;
        AudioClip clip = BuildClip(pcmS16Le);
        if (clip == null)
        {
            VoiceBootstrapLog.PlaybackRejected(turnId, pcmBytes, "clip_build_failed");
            return false;
        }
        VoiceBootstrapLog.PlaybackAccepted(turnId, pcmBytes);
        _source.clip = clip;
        _source.Play();
        _wasPlaying = true;
        VoiceBootstrapLog.PlaybackStarted(turnId);
        return true;
    }

    /// <summary>Decode speak.audio.data_b64 to PCM bytes. Test seam.</summary>
    public static bool TryDecodePcmBase64(string dataB64, out byte[] pcm)
    {
        pcm = null;
        if (string.IsNullOrEmpty(dataB64))
            return false;
        try
        {
            pcm = Convert.FromBase64String(dataB64);
            return pcm != null && pcm.Length >= 2;
        }
        catch (FormatException)
        {
            return false;
        }
    }

    static AudioClip BuildClip(byte[] pcm)
    {
        int sampleCount = pcm.Length / 2;
        if (sampleCount <= 0)
            return null;
        var floats = new float[sampleCount];
        for (int i = 0; i < sampleCount; i++)
        {
            int idx = i * 2;
            short s = (short)(pcm[idx] | (pcm[idx + 1] << 8));
            floats[i] = s / 32768f;
        }
        var clip = AudioClip.Create("cloud_speak", sampleCount, 1, SampleRate, false);
        clip.SetData(floats, 0);
        return clip;
    }
}
