using UnityEngine;

/// <summary>
/// A world-space name plate that stays readable across the room.
///
/// VoiceCaption is deliberately small and head-locked; these have to be read
/// at three metres while the wearer walks around the corner, so they are much
/// larger and billboard to the camera every frame.
/// </summary>
public sealed class LayoutLabel : MonoBehaviour
{
    /// <summary>Metres per character unit. VoiceCaption uses 0.003.</summary>
    public const float CharacterSize = 0.010f;
    public const int FontSize = 64;

    TextMesh _text;
    MeshRenderer _renderer;
    Transform _eye;

    public static LayoutLabel Create(Transform parent, Color tint)
    {
        var go = new GameObject("LayoutLabel");
        go.transform.SetParent(parent, false);
        var label = go.AddComponent<LayoutLabel>();
        label.Build(tint);
        return label;
    }

    void Build(Color tint)
    {
        _text = gameObject.AddComponent<TextMesh>();
        _text.fontSize = FontSize;
        _text.characterSize = CharacterSize;
        _text.anchor = TextAnchor.LowerCenter;
        _text.alignment = TextAlignment.Center;
        _text.richText = false;
        _text.color = Readable(tint);
        _renderer = GetComponent<MeshRenderer>();
        if (_renderer != null)
        {
            _renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            _renderer.receiveShadows = false;
        }
        var eye = GameObject.Find("CenterEyeAnchor");
        if (eye != null)
            _eye = eye.transform;
    }

    /// <summary>Passthrough is a bright, busy background; keep text near white.</summary>
    public static Color Readable(Color tint)
    {
        return new Color(
            Mathf.Lerp(tint.r, 1f, 0.6f),
            Mathf.Lerp(tint.g, 1f, 0.6f),
            Mathf.Lerp(tint.b, 1f, 0.6f),
            1f);
    }

    public void SetText(string text)
    {
        if (_text != null)
            _text.text = text ?? "";
    }

    public void SetEmphasis(bool on)
    {
        if (_text != null)
            _text.characterSize = on ? CharacterSize * 1.25f : CharacterSize;
    }

    public void SetVisible(bool on)
    {
        if (_renderer != null)
            _renderer.enabled = on;
    }

    void LateUpdate()
    {
        if (_eye == null)
        {
            var eye = GameObject.Find("CenterEyeAnchor");
            if (eye != null)
                _eye = eye.transform;
            return;
        }
        // Face the wearer, upright: yaw only, so text never tips over.
        Vector3 flat = transform.position - _eye.position;
        flat.y = 0f;
        if (flat.sqrMagnitude > 1e-6f)
            transform.rotation = Quaternion.LookRotation(flat.normalized, Vector3.up);
    }
}
