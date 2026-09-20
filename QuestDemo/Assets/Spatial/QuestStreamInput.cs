using System;
using Meta.XR;
using UnityEngine;

/// <summary>
/// PCA -> JPEG + existing envelope, and A-button push-to-talk -> mono PCM.
/// SDK CPU readback follows its native texture update at 3 Hz; pixels and
/// metadata are snapped together, then downsampled to a small JPEG.
/// The same socket carries all media; no model or renderer runs on this path.
/// </summary>
public class QuestStreamInput : MonoBehaviour
{
    public const int SampleRate = 16000;
    public const int MaxTalkSeconds = 15;
    QuestTrackingSettings _settings;
    PassthroughCameraAccess _pca;
    SpatialRuntime _spatial;
    Texture2D _readback;
    Color32[] _pixels;
    AudioClip _recording;
    float _talkStarted;
    float _nextCapture;
    float _pendingStarted;
    long _lastExposure;
    string _utterance;
    byte[] _pendingPcm;
    string _lastSession;
    int _framesSent;

    void Start()
    {
        _settings = QuestTrackingSettings.Load();
        if (_settings == null || !_settings.enableTracking)
        {
            enabled = false;
            return;
        }
        _pca = GetComponent<PassthroughCameraAccess>();
        _spatial = GetComponent<SpatialRuntime>();
#if UNITY_ANDROID && !UNITY_EDITOR
        if (!UnityEngine.Android.Permission.HasUserAuthorizedPermission(UnityEngine.Android.Permission.Microphone))
            UnityEngine.Android.Permission.RequestUserPermission(UnityEngine.Android.Permission.Microphone);
#endif
        Debug.Log("QUEST_STREAM enabled. Hold right A to talk (release to select). B toggles continuous conversation. Left X stops tracking.");
    }

    void LateUpdate()
    {
        var coordinator = _spatial != null ? _spatial.Coordinator : null;
        if (coordinator == null || !coordinator.IsReady)
        {
            AbortAudio();
            _lastSession = null;
            return;
        }
        if (_lastSession != coordinator.SessionId)
        {
            AbortAudio();
            _lastSession = coordinator.SessionId;
            _lastExposure = 0;
        }
        if (_pca == null || !_pca.IsPlaying)
            return;

        // B toggles continuous conversation; stop-tracking moved to left X.
        if (OVRInput.GetDown(OVRInput.Button.Two, OVRInput.Controller.RTouch))
        {
            AbortAudio();
            coordinator.SetLiveConversation(!coordinator.LiveConversation);
        }
        if (OVRInput.GetDown(OVRInput.Button.Three, OVRInput.Controller.LTouch))
        {
            AbortAudio();
            coordinator.StopTracking();
        }
        // The conversation loop owns the microphone while B-mode is on; only
        // frame streaming below keeps running, so a seeded mask keeps tracking.
        if (coordinator.LiveConversation)
        {
            AbortAudio();
        }
        else
        {
            if (OVRInput.GetDown(OVRInput.Button.One, OVRInput.Controller.RTouch)
                && _recording == null && _pendingPcm == null)
                BeginAudio();
            if (_recording != null && (OVRInput.GetUp(OVRInput.Button.One, OVRInput.Controller.RTouch)
                                      || Time.realtimeSinceStartup - _talkStarted >= MaxTalkSeconds - .25f))
                EndAudio();
        }

        bool selected = _pendingPcm != null;
        if (selected && Time.realtimeSinceStartup - _pendingStarted > 3f)
        {
            Debug.LogWarning("QUEST_STREAM no fresh snapshot for speech. Release A and try again.");
            AbortAudio();
            return;
        }
        if (!selected && Time.realtimeSinceStartup < _nextCapture)
            return;
        if (!_pca.IsUpdatedThisFrame)
            return;
        long exposure = SpatialRuntime.ToUnixNanoseconds(_pca.Timestamp);
        if (exposure <= _lastExposure)
            return;
        try
        {
            CaptureEnvelope envelope;
            byte[] jpeg;
            if (!TrySnapshot(out envelope, out jpeg))
                return;
            _lastExposure = envelope.TUnixNs;
            _nextCapture = Time.realtimeSinceStartup + 1f / Mathf.Clamp(_settings.streamFps, 1, 10);
            coordinator.EnqueueVideoFrame(envelope, jpeg, selected);
            if (++_framesSent % 30 == 1)
                Debug.Log("QUEST_STREAM frame=" + envelope.FrameId + " jpeg=" + jpeg.Length
                          + " size=" + envelope.SentW + "x" + envelope.SentH);
            if (selected)
            {
                coordinator.EnqueueUtterance(_utterance, _pendingPcm, envelope.FrameId);
                Debug.Log("QUEST_STREAM utterance=" + _utterance + " frame=" + envelope.FrameId
                          + " pcm_bytes=" + _pendingPcm.Length);
                _pendingPcm = null;
                _utterance = null;
            }
        }
        catch (Exception e)
        {
            Debug.LogWarning("QUEST_STREAM capture failed: " + e.GetType().Name);
            _nextCapture = Time.realtimeSinceStartup + 1f;
        }
    }

    bool TrySnapshot(out CaptureEnvelope envelope, out byte[] jpeg)
    {
        envelope = null;
        jpeg = null;
        long timestamp = SpatialRuntime.ToUnixNanoseconds(_pca.Timestamp);
        Pose pose;
        Ray ray;
        // Segmentation doesn't require a surface hit or add a geometry-cache entry.
        if (!_spatial.TryCapture(out envelope, out pose, out ray, false))
            return false;
        Texture source = _pca.GetTexture();
        if (source == null || source.width < 1 || source.height < 1)
            return false;
        float scale = Mathf.Min(1f, Mathf.Clamp(_settings.maxImageSide, 320, 1024)
                                   / (float)Mathf.Max(source.width, source.height));
        int w = Mathf.Max(1, Mathf.RoundToInt(source.width * scale));
        int h = Mathf.Max(1, Mathf.RoundToInt(source.height * scale));
        EnsureTextures(w, h);
        // PCA queues its native texture update in Update. Its GetColors uses
        // AsyncGPUReadback + WaitForCompletion, ordered after that update.
        // A blocking Graphics.Blit before it would capture the PREVIOUS image
        // (documented on PassthroughCameraAccess.GetTexture in MRUK 205).
        var colors = _pca.GetColors();
        if (!colors.IsCreated || colors.Length != source.width * source.height)
            return false;
        for (int y = 0; y < h; y++)
        {
            int sy = Mathf.Min(source.height - 1, (int)((y + .5f) * source.height / h));
            if (_settings.flipImageVertically) sy = source.height - 1 - sy;
            for (int x = 0; x < w; x++)
            {
                int sx = Mathf.Min(source.width - 1, (int)((x + .5f) * source.width / w));
                _pixels[y * w + x] = colors[sy * source.width + sx];
            }
        }
        _readback.SetPixels32(_pixels);
        _readback.Apply(false, false);
        if (timestamp != envelope.TUnixNs || timestamp != SpatialRuntime.ToUnixNanoseconds(_pca.Timestamp))
            return false; // never pair old metadata with a newer camera texture
        jpeg = _readback.EncodeToJPG(Mathf.Clamp(_settings.jpegQuality, 30, 90));
        if (jpeg.Length > 350000)
        {
            Debug.LogWarning("QUEST_STREAM JPEG exceeds 350 KB. Lower Max Image Side or JPEG Quality.");
            return false;
        }
        envelope.SentW = w;
        envelope.SentH = h;
        envelope.CropSx = envelope.ImageW / (float)w;
        envelope.CropSy = envelope.ImageH / (float)h;
        // Pixel-centre mapping through a resize (not an edge-coordinate mapping).
        envelope.CropTx = (envelope.CropSx - 1f) * .5f;
        envelope.CropTy = (envelope.CropSy - 1f) * .5f;
        if (_settings.flipImageVertically)
        {
            envelope.CropSy = -envelope.CropSy;
            envelope.CropTy = envelope.ImageH - 1f - envelope.CropTy;
        }
        return true;
    }

    void EnsureTextures(int w, int h)
    {
        if (_readback != null && _readback.width == w && _readback.height == h)
            return;
        ReleaseTextures();
        _readback = new Texture2D(w, h, TextureFormat.RGB24, false);
        _pixels = new Color32[w * h];
    }

    void BeginAudio()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        if (!UnityEngine.Android.Permission.HasUserAuthorizedPermission(UnityEngine.Android.Permission.Microphone))
        {
            UnityEngine.Android.Permission.RequestUserPermission(UnityEngine.Android.Permission.Microphone);
            Debug.LogWarning("QUEST_STREAM grant microphone permission, then hold A again.");
            return;
        }
#endif
        if (Microphone.devices.Length == 0)
        {
            Debug.LogWarning("QUEST_STREAM no microphone available.");
            return;
        }
        _recording = Microphone.Start(null, false, MaxTalkSeconds, SampleRate);
        if (_recording == null)
        {
            Debug.LogWarning("QUEST_STREAM microphone did not start.");
            return;
        }
        _talkStarted = Time.realtimeSinceStartup;
        _utterance = SpatialRuntime.NewFrameId();
        Debug.Log("QUEST_STREAM listening (hold A)…");
    }

    void EndAudio()
    {
        var clip = _recording;
        int frames = Microphone.GetPosition(null);
        // A completed non-looping clip can report position zero.
        if (frames <= 0 && Time.realtimeSinceStartup - _talkStarted >= MaxTalkSeconds - .25f)
            frames = clip.samples;
        Microphone.End(null);
        _recording = null;
        if (frames < clip.frequency / 2)
        {
            Destroy(clip);
            _utterance = null;
            Debug.LogWarning("QUEST_STREAM speech must be at least half a second.");
            return;
        }
        var samples = new float[Mathf.Min(frames, clip.samples) * clip.channels];
        if (!clip.GetData(samples, 0))
        {
            Destroy(clip);
            _utterance = null;
            Debug.LogWarning("QUEST_STREAM microphone samples unavailable.");
            return;
        }
        _pendingPcm = ToMonoPcm16(samples, clip.channels, clip.frequency);
        _pendingStarted = Time.realtimeSinceStartup;
        Destroy(clip);
    }

    public static byte[] ToMonoPcm16(float[] samples, int channels, int sourceRate)
    {
        if (channels < 1 || sourceRate < 1)
            throw new ArgumentOutOfRangeException();
        int inputFrames = samples.Length / channels;
        int count = (int)((long)inputFrames * SampleRate / sourceRate);
        var pcm = new byte[count * 2];
        for (int i = 0; i < count; i++)
        {
            double position = i * (double)sourceRate / SampleRate;
            int left = Math.Min((int)position, inputFrames - 1);
            int right = Math.Min(left + 1, inputFrames - 1);
            float fraction = (float)(position - left);
            float sample = 0f;
            for (int channel = 0; channel < channels; channel++)
                sample += Mathf.Lerp(samples[left * channels + channel], samples[right * channels + channel], fraction);
            sample = Mathf.Clamp(sample / channels, -1f, 1f);
            short value = (short)Mathf.RoundToInt(sample * (sample < 0 ? 32768f : 32767f));
            pcm[i * 2] = (byte)(value & 255);
            pcm[i * 2 + 1] = (byte)((value >> 8) & 255);
        }
        return pcm;
    }

    void AbortAudio()
    {
        if (_recording != null)
        {
            Microphone.End(null);
            Destroy(_recording);
            _recording = null;
        }
        _pendingPcm = null;
        _utterance = null;
    }

    void ReleaseTextures()
    {
        if (_readback != null) { Destroy(_readback); _readback = null; }
        _pixels = null;
    }

    void OnDisable() { AbortAudio(); ReleaseTextures(); }
    void OnApplicationPause(bool paused) { if (paused) AbortAudio(); }
}
