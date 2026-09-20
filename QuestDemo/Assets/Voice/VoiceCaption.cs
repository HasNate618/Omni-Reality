using System.Text;
using UnityEngine;

/// <summary>Small head-relative speech/status caption; never writes its text to logs.</summary>
public class VoiceCaption : MonoBehaviour
{
    TextMesh _text;
    MeshRenderer _renderer;
    Transform _eye;
    float _hideAt;

    public void Show(string text, Transform eye, float seconds = 8f)
    {
        if (_text == null)
        {
            var label = new GameObject("VoiceCaptionText");
            label.transform.SetParent(transform, false);
            _text = label.AddComponent<TextMesh>();
            _text.fontSize = 48;
            _text.characterSize = 0.003f;
            _text.anchor = TextAnchor.MiddleCenter;
            _text.alignment = TextAlignment.Center;
            _text.color = Color.white;
            _text.richText = false;
            _renderer = label.GetComponent<MeshRenderer>();
        }
        _eye = eye;
        _text.text = Wrap(text ?? "");
        _hideAt = Time.realtimeSinceStartup + Mathf.Clamp(seconds, 3f, 30f);
        _renderer.enabled = true;
        LateUpdate();
    }

    void LateUpdate()
    {
        if (_renderer == null) return;
        if (Time.realtimeSinceStartup >= _hideAt) _renderer.enabled = false;
        if (_eye == null) return;
        _text.transform.position = _eye.TransformPoint(new Vector3(0f, -0.16f, 0.85f));
        _text.transform.rotation = _eye.rotation;
    }

    static string Wrap(string text)
    {
        if (text.Length > 240) text = text.Substring(0, 237) + "...";
        var result = new StringBuilder();
        int column = 0;
        foreach (char c in text)
        {
            if (c == '\n') { result.Append(c); column = 0; continue; }
            if (column == 48) { result.Append('\n'); column = 0; }
            result.Append(c);
            column++;
        }
        return result.ToString();
    }
}
