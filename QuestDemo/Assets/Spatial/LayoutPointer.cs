using UnityEngine;

/// <summary>
/// The line out of the right controller, so aiming at a piece is obvious.
///
/// SpatialRuntime has an aim line of its own, but it is switched off whenever
/// tracking owns the controller, which is every shipped build. This one
/// belongs to Layout mode and is driven from LayoutMode.Update.
/// </summary>
public sealed class LayoutPointer : MonoBehaviour
{
    public const float WidthM = 0.004f;
    public const float MaxLengthM = 6f;
    public static readonly Color IdleColor = new Color(0.24f, 0.86f, 1f, 0.55f);
    public static readonly Color HitColor = new Color(0.55f, 1f, 0.6f, 0.95f);
    public static readonly Color ResizeColor = new Color(1f, 0.78f, 0.3f, 0.95f);

    LineRenderer _line;
    Transform _aim;
    GameObject _dot;

    void Awake()
    {
        var rightGO = GameObject.Find("RightHandAnchor");
        if (rightGO != null)
            _aim = rightGO.transform;

        var holder = new GameObject("PointerLine");
        holder.transform.SetParent(transform, false);
        _line = holder.AddComponent<LineRenderer>();
        _line.useWorldSpace = true;
        _line.positionCount = 2;
        _line.startWidth = WidthM;
        _line.endWidth = WidthM * 0.6f;
        _line.material = LayoutBox.BuildMaterial(IdleColor);
        _line.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        SetColor(IdleColor);

        _dot = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        _dot.name = "PointerDot";
        Collider col = _dot.GetComponent<Collider>();
        if (col != null)
            LayoutBox.DestroySafe(col);
        _dot.transform.SetParent(transform, false);
        _dot.transform.localScale = Vector3.one * 0.035f;
        var rend = _dot.GetComponent<Renderer>();
        if (rend != null)
        {
            rend.sharedMaterial = LayoutBox.BuildMaterial(IdleColor);
            rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        }
        SetVisible(false);
    }

    public bool HasAim { get { return _aim != null; } }
    public Ray AimRay { get { return new Ray(_aim.position, _aim.forward); } }

    public void SetVisible(bool on)
    {
        if (_line != null)
            _line.enabled = on;
        if (_dot != null)
            _dot.SetActive(on);
    }

    /// <summary>Draw to `end`; `hit` picks the colour, `resizing` overrides it.</summary>
    public void Draw(Vector3 end, bool hit, bool resizing)
    {
        if (_aim == null || _line == null)
            return;
        SetVisible(true);
        Vector3 start = _aim.position;
        if ((end - start).magnitude > MaxLengthM)
            end = start + ((end - start).normalized * MaxLengthM);
        _line.SetPosition(0, start);
        _line.SetPosition(1, end);
        SetColor(resizing ? ResizeColor : (hit ? HitColor : IdleColor));
        if (_dot != null)
            _dot.transform.position = end;
    }

    public void DrawMiss()
    {
        if (_aim == null)
            return;
        Draw(_aim.position + (_aim.forward * 2.5f), false, false);
        if (_dot != null)
            _dot.SetActive(false);
    }

    void SetColor(Color c)
    {
        if (_line != null)
        {
            _line.startColor = c;
            _line.endColor = c;
            if (_line.sharedMaterial != null)
                _line.sharedMaterial.SetColor("_Color", c);
        }
        if (_dot != null)
        {
            var rend = _dot.GetComponent<Renderer>();
            if (rend != null && rend.sharedMaterial != null)
                rend.sharedMaterial.SetColor("_Color", c);
        }
    }

    void OnDestroy()
    {
        if (_line != null && _line.sharedMaterial != null)
            LayoutBox.DestroySafe(_line.sharedMaterial);
        if (_dot != null)
        {
            var rend = _dot.GetComponent<Renderer>();
            if (rend != null && rend.sharedMaterial != null)
                LayoutBox.DestroySafe(rend.sharedMaterial);
        }
    }
}
