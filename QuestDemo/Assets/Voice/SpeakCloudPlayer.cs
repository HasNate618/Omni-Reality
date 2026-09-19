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

    AudioSource _source;
    string _activeTurnId;

    void Awake()
    {
        _source = GetComponent<AudioSource>();
        _source.playOnAwake = false;
        _source.spatialBlend = 0f;
    }

    /// <summary>Stop playback for barge-in / stop_speak.</summary>
    public void StopPlayback()
    {
        _activeTurnId = null;
        if (_source != null && _source.isPlaying)
            _source.Stop();
    }

    /// <summary>
    /// Play cloud PCM for a turn. Returns false when audio is missing (degraded).
    /// </summary>
    public bool TryPlay(int turnId, string text, byte[] pcmS16Le)
    {
        string turnKey = turnId.ToString();
        if (pcmS16Le == null || pcmS16Le.Length < 2)
        {
            Debug.LogWarning("SpeakCloudPlayer: speak without cloud PCM (turn " + turnKey + ")");
            return false;
        }
        StopPlayback();
        _activeTurnId = turnKey;
        AudioClip clip = BuildClip(pcmS16Le);
        if (clip == null)
            return false;
        _source.clip = clip;
        _source.Play();
        if (!string.IsNullOrEmpty(text))
            Debug.Log("Speak caption (turn " + turnKey + "): " + text);
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
