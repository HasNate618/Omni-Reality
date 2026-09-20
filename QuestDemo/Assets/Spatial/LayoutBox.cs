using System;
using UnityEngine;

/// <summary>
/// One piece of furniture at true size, standing on the floor.
///
/// The mesh is built to fill the listing's w x d x h exactly, so the shape the
/// wearer sees and the footprint the fit check measures are the same volume.
/// A bright rectangle on the floor is the part that actually answers "will it
/// fit"; the furniture on top of it is there so the corner reads as a room.
///
/// Resizing is allowed. `ListingSizeM` keeps the original so scaling stays
/// anchored to it and repeated grabs cannot drift; the label always shows the
/// piece's live size.
/// </summary>
public sealed class LayoutBox : MonoBehaviour
{
    public const float MinExtentM = 0.05f;
    public const float MaxExtentM = 4.0f;
    public const float FootprintWidthM = 0.008f;
    public const float MinScale = 0.35f;
    public const float MaxScale = 2.5f;

    public string ItemLabel { get; private set; }
    public string Kind { get; private set; }
    /// <summary>Current footprint and height in metres (w = x, d = z, h = y).</summary>
    public Vector3 SizeM { get; private set; }
    /// <summary>The size the listing stated, before any resizing.</summary>
    public Vector3 ListingSizeM { get; private set; }
    public Color Tint { get; private set; }
    public bool IsResized { get; private set; }

    GameObject _furniture;
    LineRenderer _footprint;
    LayoutLabel _label;
    bool _highlighted;

    internal static void DestroySafe(UnityEngine.Object obj)
    {
        if (obj == null)
            return;
        if (Application.isPlaying)
            Destroy(obj);
        else
            DestroyImmediate(obj);
    }

    public static bool IsUsableSize(Vector3 sizeM)
    {
        return IsUsableExtent(sizeM.x) && IsUsableExtent(sizeM.y) && IsUsableExtent(sizeM.z);
    }

    static bool IsUsableExtent(float v)
    {
        return !float.IsNaN(v) && !float.IsInfinity(v) && v >= MinExtentM && v <= MaxExtentM;
    }

    /// <summary>Corner-relative slot -> world pose, with the piece on the floor.</summary>
    public static void SlotToWorld(
        LayoutRoom.CornerFrame corner, float dx, float dz, float yawDeg, float heightM,
        out Vector3 position, out Quaternion rotation)
    {
        Vector3 flat = corner.Origin + (corner.AxisX * dx) + (corner.AxisZ * dz);
        position = new Vector3(flat.x, corner.Origin.y + (heightM * 0.5f), flat.z);
        rotation = Quaternion.LookRotation(corner.AxisZ, Vector3.up) * Quaternion.Euler(0f, yawDeg, 0f);
    }

    public static LayoutBox Create(string label, Vector3 sizeM, Color tint, string kind = null)
    {
        if (!IsUsableSize(sizeM))
            return null;
        var root = new GameObject("LayoutBox_" + (string.IsNullOrEmpty(label) ? "item" : label));
        root.transform.SetParent(null, true);
        var box = root.AddComponent<LayoutBox>();
        box.ItemLabel = label;
        box.Kind = FurnitureCatalog.IsKnown(kind) ? kind : FurnitureCatalog.Sofa;
        box.SizeM = sizeM;
        box.ListingSizeM = sizeM;
        box.Tint = tint;
        box.Rebuild();
        return box;
    }

    void Rebuild()
    {
        if (_furniture != null)
            DestroySafe(_furniture);
        _furniture = FurnitureMesh.Build(Kind, SizeM, Tint, transform);
        BuildFootprint();
        BuildLabel();
    }

    internal static Material BuildMaterial(Color tint)
    {
        Shader shader = Resources.Load<Shader>("OmniGhostBox");
        if (shader == null)
        {
            Debug.LogWarning("QUEST_LAYOUT Resources/OmniGhostBox missing; falling back");
            shader = Shader.Find("Unlit/Transparent");
        }
        var mat = new Material(shader);
        mat.SetColor("_Color", tint);
        mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
        return mat;
    }

    /// <summary>The bright rectangle on the floor: the "will it fit" answer.</summary>
    void BuildFootprint()
    {
        if (_footprint == null)
        {
            var holder = new GameObject("Footprint");
            holder.transform.SetParent(transform, false);
            _footprint = holder.AddComponent<LineRenderer>();
            _footprint.useWorldSpace = false;
            _footprint.loop = true;
            _footprint.positionCount = 4;
            _footprint.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            Color solid = new Color(Tint.r, Tint.g, Tint.b, 1f);
            _footprint.material = BuildMaterial(solid);
            _footprint.startColor = solid;
            _footprint.endColor = solid;
        }
        _footprint.transform.localPosition = new Vector3(0f, -SizeM.y * 0.5f + 0.003f, 0f);
        float hw = SizeM.x * 0.5f;
        float hd = SizeM.z * 0.5f;
        _footprint.SetPosition(0, new Vector3(-hw, 0f, -hd));
        _footprint.SetPosition(1, new Vector3(hw, 0f, -hd));
        _footprint.SetPosition(2, new Vector3(hw, 0f, hd));
        _footprint.SetPosition(3, new Vector3(-hw, 0f, hd));
        _footprint.startWidth = FootprintWidthM;
        _footprint.endWidth = FootprintWidthM;
    }

    void BuildLabel()
    {
        if (_label == null)
            _label = LayoutLabel.Create(transform, Tint);
        _label.SetText(SizeCaption());
        _label.transform.localPosition = new Vector3(0f, (SizeM.y * 0.5f) + 0.14f, 0f);
    }

    /// <summary>Scale the piece about its floor centre, within sane limits.</summary>
    public bool ApplyScale(float factor)
    {
        if (float.IsNaN(factor) || float.IsInfinity(factor) || factor <= 0f)
            return false;
        float current = ListingSizeM.x > 0f ? SizeM.x / ListingSizeM.x : 1f;
        float target = Mathf.Clamp(current * factor, MinScale, MaxScale);
        Vector3 next = ListingSizeM * target;
        if (!IsUsableSize(next))
            return false;
        if (Mathf.Approximately(next.x, SizeM.x))
            return false;

        // Keep the floor under the piece: the root sits at half height.
        Vector3 p = transform.position;
        float floorY = p.y - (SizeM.y * 0.5f);
        SizeM = next;
        IsResized = Mathf.Abs(target - 1f) > 0.01f;
        transform.position = new Vector3(p.x, floorY + (SizeM.y * 0.5f), p.z);
        Rebuild();
        SetHighlighted(_highlighted);
        return true;
    }

    public float ScaleFactor
    {
        get { return ListingSizeM.x > 0f ? SizeM.x / ListingSizeM.x : 1f; }
    }

    public void SetHighlighted(bool on)
    {
        _highlighted = on;
        if (_furniture == null)
            return;
        Color c = Tint;
        c.a = on ? Mathf.Min(1f, FurnitureMesh.BodyAlpha * 1.25f) : FurnitureMesh.BodyAlpha;
        var renderers = _furniture.GetComponentsInChildren<Renderer>();
        for (int i = 0; i < renderers.Length; i++)
        {
            if (renderers[i] != null && renderers[i].sharedMaterial != null)
                renderers[i].sharedMaterial.SetColor("_Color", c);
        }
        if (_label != null)
            _label.SetEmphasis(on);
    }

    public Vector2 FootprintCentre()
    {
        Vector3 p = transform.position;
        return new Vector2(p.x, p.z);
    }

    /// <summary>Floor-plane half extents after yaw, as an axis-aligned box.</summary>
    public Vector2 FootprintHalfExtents()
    {
        float yaw = transform.eulerAngles.y * Mathf.Deg2Rad;
        float c = Mathf.Abs(Mathf.Cos(yaw));
        float s = Mathf.Abs(Mathf.Sin(yaw));
        float hw = SizeM.x * 0.5f;
        float hd = SizeM.z * 0.5f;
        return new Vector2((hw * c) + (hd * s), (hw * s) + (hd * c));
    }

    /// <summary>Name and current size. Always the live numbers, resized or not.</summary>
    public string SizeCaption()
    {
        return string.Format(
            "{0}\n{1:0} x {2:0} x {3:0} cm",
            string.IsNullOrEmpty(ItemLabel) ? "item" : ItemLabel,
            SizeM.x * 100f, SizeM.z * 100f, SizeM.y * 100f);
    }

    void OnDestroy()
    {
        if (_furniture != null)
        {
            var renderers = _furniture.GetComponentsInChildren<Renderer>();
            for (int i = 0; i < renderers.Length; i++)
            {
                if (renderers[i] != null && renderers[i].sharedMaterial != null)
                    DestroySafe(renderers[i].sharedMaterial);
            }
        }
        if (_footprint != null && _footprint.sharedMaterial != null)
            DestroySafe(_footprint.sharedMaterial);
    }
}
