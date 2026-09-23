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

    /// <summary>
    /// Break `text` so no line exceeds `maxChars`, on word boundaries.
    ///
    /// TextMesh does not wrap, so a long line just runs off the side of
    /// whatever it is labelling. Existing newlines are kept.
    /// </summary>
    public static string Wrap(string text, int maxChars)
    {
        if (string.IsNullOrEmpty(text) || maxChars <= 0)
            return text ?? "";
        var outp = new System.Text.StringBuilder();
        string[] paragraphs = text.Split('\n');
        for (int p = 0; p < paragraphs.Length; p++)
        {
            if (p > 0)
                outp.Append('\n');
            int column = 0;
            string[] words = paragraphs[p].Split(' ');
            for (int w = 0; w < words.Length; w++)
            {
                string word = words[w];
                if (word.Length == 0)
                    continue;
                if (column > 0 && column + 1 + word.Length > maxChars)
                {
                    outp.Append('\n');
                    column = 0;
                }
                else if (column > 0)
                {
                    outp.Append(' ');
                    column++;
                }
                outp.Append(word);
                column += word.Length;
            }
        }
        return outp.ToString();
    }

    /// <summary>Set text, wrapped so it stays inside its card.</summary>
    public void SetTextWrapped(string text, int maxChars)
    {
        SetText(Wrap(text, maxChars));
    }

    public void SetText(string text)
    {
        if (_text != null)
            _text.text = text ?? "";
    }

    float _scale = 1f;

    /// <summary>Base size multiplier, for labels that must fit something.</summary>
    public void SetScale(float scale)
    {
        _scale = Mathf.Clamp(scale, 0.3f, 3f);
        if (_text != null)
            _text.characterSize = CharacterSize * _scale;
    }

    public void SetEmphasis(bool on)
    {
        if (_text != null)
            _text.characterSize = CharacterSize * _scale * (on ? 1.25f : 1f);
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
