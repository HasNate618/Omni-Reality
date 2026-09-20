using System;
using UnityEngine;

/// <summary>
/// SAM2 highlight overlay: one camera-facing quad painting the tracked mask
/// in green. Holds <see cref="HoldSeconds"/> fully visible, fades over
/// <see cref="FadeSeconds"/>, then hides; a new Show replaces the old track.
/// The server stops sending results at its own 10 s TTL, so this clock only
/// ever hides early on loss, never extends a track.
/// </summary>
public class TrackingMaskOverlay : MonoBehaviour
{
    public const float HoldSeconds = 10f;
    public const float FadeSeconds = 0.75f;

    public Transform Anchor;
    GameObject _quad;
    Material _material;
    float _elapsed = -1f;

    public bool IsVisible { get { return _elapsed >= 0f && _quad != null && _quad.activeSelf; } }

    public float CurrentAlpha
    {
        get
        {
            if (_material == null || _elapsed < 0f)
                return 0f;
            if (_elapsed <= HoldSeconds)
                return 1f;
            float t = (_elapsed - HoldSeconds) / FadeSeconds;
            return t >= 1f ? 0f : 1f - t;
        }
    }

    /// <summary>Paint a new mask (PNG/JPEG bytes), restarting the hold clock.</summary>
    public void Show(byte[] maskBytes, int width, int height)
    {
        Texture2D mask = new Texture2D(2, 2, TextureFormat.RGBA32, false);
        if (maskBytes == null || maskBytes.Length == 0 || !mask.LoadImage(maskBytes)
            || mask.width > 1024 || mask.height > 1024)
        {
            VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator,
                "tracking_mask_undecodable", ("bytes", maskBytes != null ? maskBytes.Length : 0));
            DestroyTemp(mask);
            Hide();
            return;
        }
        EnsureQuad(width, height);
        ApplyMask(mask);
        DestroyTemp(mask);
        _elapsed = 0f;
        _quad.SetActive(true);
        VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator,
            "tracking_shown", ("width", width), ("height", height));
    }

    public void Hide()
    {
        _elapsed = -1f;
        if (_quad != null)
            _quad.SetActive(false);
    }

    public void Tick(float dt)
    {
        if (_elapsed < 0f || _quad == null)
            return;
        _elapsed += dt;
        if (_elapsed >= HoldSeconds + FadeSeconds)
        {
            VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator, "tracking_hidden");
            Hide();
            return;
        }
        if (_material != null)
        {
            Color color = _material.color;
            color.a = CurrentAlpha;
            _material.color = color;
        }
        Billboard();
    }

    void Update()
    {
        Tick(Time.deltaTime);
    }

    void EnsureQuad(int width, int height)
    {
        if (_quad == null)
        {
            _quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
            _quad.name = "TrackingHighlight";
            var collider = _quad.GetComponent<Collider>();
            if (collider != null)
#if UNITY_EDITOR
                UnityEngine.Object.DestroyImmediate(collider);
#else
                UnityEngine.Object.Destroy(collider);
#endif
            _material = new Material(Shader.Find("Unlit/Transparent"));
            _material.color = new Color(0.2f, 1f, 0.4f, 1f);
            _quad.GetComponent<Renderer>().material = _material;
            Transform anchor = Anchor != null ? Anchor
                : (Camera.main != null ? Camera.main.transform : null);
            if (anchor != null)
            {
                _quad.transform.SetParent(anchor, false);
                _quad.transform.localPosition = new Vector3(0f, 0f, 1.2f);
                _quad.transform.localRotation = Quaternion.identity;
            }
            _quad.SetActive(false);
        }
        float aspect = height > 0 ? (float)width / Math.Max(1, height) : 1f;
        float w = 0.5f;
        _quad.transform.localScale = new Vector3(w, w / Math.Max(0.1f, aspect), 1f);
    }

    Texture2D _maskTex;

    void ApplyMask(Texture2D mask)
    {
        int w = mask.width;
        int h = mask.height;
        var pixels = mask.GetPixels32();
        var outPixels = new Color32[pixels.Length];
        for (int i = 0; i < pixels.Length; i++)
        {
            byte lum = (byte)((pixels[i].r + pixels[i].g + pixels[i].b) / 3);
            outPixels[i] = new Color32(255, 255, 255, lum);
        }
        var tex = new Texture2D(w, h, TextureFormat.RGBA32, false);
        tex.SetPixels32(outPixels);
        tex.Apply();
        var old = _maskTex;
        _maskTex = tex;
        _material.mainTexture = tex;
        if (old != null)
            DestroyTemp(old);
        Color color = _material.color;
        color.a = 1f;
        _material.color = color;
    }

    static void DestroyTemp(UnityEngine.Object obj)
    {
#if UNITY_EDITOR
        UnityEngine.Object.DestroyImmediate(obj);
#else
        UnityEngine.Object.Destroy(obj);
#endif
    }

    void Billboard()
    {
        if (_quad == null)
            return;
        Transform anchor = Anchor != null ? Anchor
            : (Camera.main != null ? Camera.main.transform : null);
        if (anchor != null && _quad.transform.parent != anchor)
        {
            _quad.transform.SetParent(anchor, false);
            _quad.transform.localPosition = new Vector3(0f, 0f, 1.2f);
            _quad.transform.localRotation = Quaternion.identity;
        }
    }
}
