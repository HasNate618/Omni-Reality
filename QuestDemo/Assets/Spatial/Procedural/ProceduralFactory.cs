using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Quest-local procedural compositions (voice spec §3.1–3.2).
/// Closed grammar only: six element kinds, six palette colors, three size
/// classes, three material presets. No URLs, no code, no world points.
/// Anchor resolves through the same capture-time path as a mark; the
/// composition builds in the hit tangent frame.
/// </summary>
public static class ProceduralFactory
{
    public const int MaxElements = 6;
    public const float BoundingDiameterM = 1.0f;
    public const float SmallExtentM = 0.06f;
    public const float MediumExtentM = 0.12f;
    public const float LargeExtentM = 0.25f;
    public const float MinExtentM = 0.03f;
    public const float MaxExtentM = 0.40f;
    public const float EnlargeFactor = 1.25f;
    public const float ShrinkFactor = 0.8f;
    public const float RotateStepDeg = 15f;
    public const float NudgeStepM = 0.05f;

    static readonly HashSet<string> ElementKinds = new HashSet<string>
        { "arrow", "pointer", "panel", "cube", "sphere", "cylinder" };
    static readonly HashSet<string> TextKinds = new HashSet<string> { "panel", "pointer" };
    static readonly HashSet<string> Materials = new HashSet<string> { "solid", "translucent", "glow" };
    static readonly Dictionary<string, Color> Palette = new Dictionary<string, Color>
    {
        { "cyan", new Color(0x3D / 255f, 0xDC / 255f, 0xFF / 255f) },
        { "amber", new Color(0xFF / 255f, 0xB0 / 255f, 0x20 / 255f) },
        { "green", new Color(0x35 / 255f, 0xD0 / 255f, 0x7F / 255f) },
        { "magenta", new Color(0xE0 / 255f, 0x5C / 255f, 0xFF / 255f) },
        { "white", Color.white },
        { "slate", new Color(0x8A / 255f, 0x93 / 255f, 0xA6 / 255f) },
    };

    /// <summary>Null when the element list is buildable; else the reason.</summary>
    public static string ValidateElements(List<ProtocolJson.ProceduralElement> elements)
    {
        if (elements == null || elements.Count == 0)
            return "empty";
        if (elements.Count > MaxElements)
            return "too_many_elements";
        float width = 0f;
        float height = 0f;
        foreach (var el in elements)
        {
            if (el == null || !ElementKinds.Contains(el.Element))
                return "bad_element";
            if (!Palette.ContainsKey(el.Color))
                return "bad_color";
            float extent;
            if (!TryExtent(el.Size, out extent))
                return "bad_size";
            if (!Materials.Contains(el.Material))
                return "bad_material";
            if (!string.IsNullOrEmpty(el.Text))
            {
                if (!TextKinds.Contains(el.Element))
                    return "bad_text_target";
                if (el.Text.Length > 40)
                    return "text_too_long";
            }
            width += extent + 0.02f;
            if (extent > height)
                height = extent;
        }
        float diameter = Mathf.Sqrt(width * width + height * height);
        if (diameter > BoundingDiameterM)
            return "too_big";
        return null;
    }

    public static bool TryExtent(string size, out float extent)
    {
        if (size == "small") { extent = SmallExtentM; return true; }
        if (size == "medium") { extent = MediumExtentM; return true; }
        if (size == "large") { extent = LargeExtentM; return true; }
        extent = 0f;
        return false;
    }

    public static void TryHandle(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        if (client == null || op == null)
            return;
        if (op.Kind == "place_procedural")
            HandlePlace(client, op);
        else if (op.Kind == "revise_procedural")
            HandleRevise(client, op);
    }

    static void HandlePlace(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        List<ProtocolJson.ProceduralElement> elements;
        if (string.IsNullOrEmpty(op.ElementsJson) ||
            !ProtocolJson.TryParseProceduralElements(op.ElementsJson, out elements))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        if (ValidateElements(elements) != null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        PlacementResult result;
        if (!client.TryResolveTargetOp(op, out result))
            return;
        if (!result.ShouldPin)
        {
            client.EnqueueAckForResult(op, result);
            return;
        }
        if (client.Store != null && DrawingStore.NeedsEviction(client.Store.Count) && string.IsNullOrEmpty(op.DrawingId))
        {
            client.ShowChipText("Too many drawings on screen.");
            client.EnqueueAck(op, "rejected", null, "clutter", null);
            return;
        }
        string drawingId = !string.IsNullOrEmpty(op.DrawingId)
            ? op.DrawingId
            : (client.NewDrawingId != null ? client.NewDrawingId() : SpatialRuntime.NewFrameId());
        GameObject go = client.Store != null
            ? client.Store.PlaceProcedural(result.Point, result.Normal, drawingId, elements)
            : null;
        if (go == null)
        {
            client.EnqueueAck(op, "rejected", null, "no_surface", null);
            return;
        }
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
    }

    static void HandleRevise(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        if (string.IsNullOrEmpty(op.DrawingId) || string.IsNullOrEmpty(op.Action))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        if (op.Action == "nudge" && string.IsNullOrEmpty(op.Direction))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        if (client.Store == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        if (op.Action == "remove")
        {
            if (!client.Store.RemoveProcedural(op.DrawingId))
            {
                client.EnqueueAck(op, "rejected", null, "invalid", null);
                return;
            }
            client.EnqueueAck(op, "applied", op.DrawingId, null, null);
            return;
        }
        string error;
        if (!client.Store.ApplyProceduralRevision(op.DrawingId, op.Action, op.Direction, out error))
        {
            client.EnqueueAck(op, "rejected", null, error ?? "invalid", null);
            return;
        }
        client.EnqueueAck(op, "applied", op.DrawingId, null, null);
    }

    /// <summary>Build the composition root (already positioned by the caller).</summary>
    public static GameObject BuildComposition(
        string drawingId, List<ProtocolJson.ProceduralElement> elements)
    {
        var root = new GameObject("Drawing_" + drawingId);
        root.transform.SetParent(null, true);
        float cursor = 0f;
        foreach (var el in elements)
        {
            float extent;
            TryExtent(el.Size, out extent);
            Color color = Palette[el.Color];
            if (el.Material == "translucent")
                color.a = 0.45f;
            GameObject child = BuildElement(el, extent, color);
            float half = extent * 0.5f;
            cursor += half;
            child.transform.SetParent(root.transform, false);
            child.transform.localPosition = new Vector3(cursor, half, 0f);
            cursor += half + 0.02f;
        }
        // Centre the row on the anchor (local space; caller sets the pose).
        float total = cursor - 0.02f;
        foreach (Transform child in root.transform)
        {
            Vector3 p = child.localPosition;
            p.x -= total * 0.5f;
            child.localPosition = p;
        }
        return root;
    }

    static GameObject BuildElement(ProtocolJson.ProceduralElement el, float extent, Color color)
    {
        GameObject go;
        switch (el.Element)
        {
            case "sphere":
                go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                go.transform.localScale = Vector3.one * extent;
                break;
            case "cylinder":
                go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                go.transform.localScale = Vector3.one * extent;
                break;
            case "arrow":
                go = new GameObject("arrow");
                var shaft = GameObject.CreatePrimitive(PrimitiveType.Cube);
                shaft.transform.SetParent(go.transform, false);
                shaft.transform.localScale = new Vector3(extent * 0.7f, extent * 0.14f, extent * 0.14f);
                shaft.transform.localPosition = new Vector3(-extent * 0.15f, 0f, 0f);
                var head = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                head.transform.SetParent(go.transform, false);
                head.transform.localScale = Vector3.one * extent * 0.3f;
                head.transform.localPosition = new Vector3(extent * 0.35f, 0f, 0f);
                Paint(go, color);
                return go;
            case "pointer":
                go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                go.transform.localScale = Vector3.one * extent * 0.4f;
                break;
            case "panel":
                go = GameObject.CreatePrimitive(PrimitiveType.Quad);
                go.transform.localScale = new Vector3(extent, extent * 0.6f, 1f);
                break;
            default: // cube
                go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.transform.localScale = Vector3.one * extent;
                break;
        }
        var col = go.GetComponent<Collider>();
        if (col != null)
            Object.Destroy(col);
        Paint(go, color);
        if ((el.Element == "panel" || el.Element == "pointer") && !string.IsNullOrEmpty(el.Text))
        {
            var label = new GameObject("LabelText");
            label.transform.SetParent(go.transform, false);
            label.transform.localPosition = new Vector3(0f, extent * 0.7f, 0f);
            var tm = label.AddComponent<TextMesh>();
            tm.text = el.Text.Length <= 40 ? el.Text : el.Text.Substring(0, 40);
            tm.characterSize = 0.02f;
            tm.anchor = TextAnchor.MiddleCenter;
            tm.color = Color.white;
            label.AddComponent<SurfaceBillboard>();
        }
        return go;
    }

    static void Paint(GameObject go, Color color)
    {
        foreach (var rend in go.GetComponentsInChildren<Renderer>())
        {
            rend.material = new Material(Shader.Find("Unlit/Color"));
            rend.material.color = color;
        }
    }
}
