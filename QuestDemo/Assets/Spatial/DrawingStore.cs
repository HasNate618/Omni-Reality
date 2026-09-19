using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// World-locked drawing store (Task 5). PlaceMark plants a pulsing ring at a
/// capture-time surface point with an OVRSpatialAnchor; every mark is a root
/// object (never a camera child). Caps at 8 drawings (oldest evicted);
/// <see cref="Clear"/> exists for the later clear_session binding.
/// </summary>
public class DrawingStore : MonoBehaviour
{
    public const int MaxDrawings = 8;

    readonly List<GameObject> _marks = new List<GameObject>();

    /// <summary>Live mark count (destroyed refs pruned).</summary>
    public int Count
    {
        get
        {
            Prune();
            return _marks.Count;
        }
    }

    public GameObject PlaceMark(Vector3 point, Vector3 normal, string drawingId)
    {
        return PlaceMark(point, normal, drawingId, PulsingRing.DefaultDiameterM);
    }

    public GameObject PlaceLabel(Vector3 point, Vector3 normal, string drawingId, string text)
    {
        Prune();
        EvictIfNeeded();
        GameObject root = new GameObject("Drawing_" + drawingId);
        root.transform.position = point;
        Vector3 n = normal.sqrMagnitude < 1e-6f ? Vector3.up : normal.normalized;
        root.transform.rotation = Quaternion.FromToRotation(Vector3.forward, n);
        root.transform.SetParent(null, true);
        var textGo = new GameObject("LabelText");
        textGo.transform.SetParent(root.transform, false);
        textGo.transform.localPosition = Vector3.zero;
        var tm = textGo.AddComponent<TextMesh>();
        tm.text = TruncateLabel(text);
        tm.characterSize = 0.02f;
        tm.anchor = TextAnchor.MiddleCenter;
        tm.color = PulsingRing.RingColor;
        textGo.AddComponent<SurfaceBillboard>();
        TryAddAnchor(root);
        _marks.Add(root);
        return root;
    }

    public GameObject PlaceGhost(Vector3 point, Vector3 normal, string drawingId, ProtocolJson.SceneOpMsg op)
    {
        Prune();
        EvictIfNeeded();
        GameObject root = GameObject.CreatePrimitive(PrimitiveType.Cube);
        root.name = "Drawing_" + drawingId;
        Destroy(root.GetComponent<Collider>());
        root.transform.localScale = Vector3.one * GhostLabelConnect.GhostSizeM;
        root.transform.position = point;
        Vector3 n = normal.sqrMagnitude < 1e-6f ? Vector3.up : normal.normalized;
        root.transform.rotation = Quaternion.FromToRotation(Vector3.up, n);
        root.transform.SetParent(null, true);
        var rend = root.GetComponent<Renderer>();
        if (rend != null)
        {
            rend.material = new Material(Shader.Find("Unlit/Color"));
            Color c = PulsingRing.RingColor;
            c.a = 0.45f;
            rend.material.color = c;
        }
        var motion = root.AddComponent<GhostMotion>();
        motion.MotionKind = op.MotionKind;
        motion.Axis = string.IsNullOrEmpty(op.MotionAxis) ? "y" : op.MotionAxis;
        motion.AngleDeg = op.MotionAngleDeg > 0f ? op.MotionAngleDeg : 45f;
        motion.DistanceM = op.MotionDistanceM > 0f ? op.MotionDistanceM : 0.1f;
        motion.PeriodS = op.HasMotion && op.MotionPeriodS > 0f ? op.MotionPeriodS : 2f;
        TryAddAnchor(root);
        _marks.Add(root);
        return root;
    }

    public GameObject PlaceConnect(Vector3 from, Vector3 to, string drawingId)
    {
        Prune();
        EvictIfNeeded();
        GameObject root = new GameObject("Drawing_" + drawingId);
        root.transform.SetParent(null, true);
        var lr = root.AddComponent<LineRenderer>();
        lr.positionCount = 2;
        lr.SetPosition(0, from);
        lr.SetPosition(1, to);
        lr.startWidth = 0.008f;
        lr.endWidth = 0.008f;
        lr.material = new Material(Shader.Find("Unlit/Color"));
        lr.startColor = PulsingRing.RingColor;
        lr.endColor = PulsingRing.RingColor;
        lr.useWorldSpace = true;
        TryAddAnchor(root);
        _marks.Add(root);
        return root;
    }

    static string TruncateLabel(string text)
    {
        if (string.IsNullOrEmpty(text))
            return "";
        return text.Length <= 48 ? text : text.Substring(0, 48);
    }

    void EvictIfNeeded()
    {
        while (_marks.Count >= MaxDrawings)
        {
            GameObject oldest = _marks[0];
            _marks.RemoveAt(0);
            if (oldest != null)
                DestroyMark(oldest);
        }
    }

    public GameObject PlaceMark(Vector3 point, Vector3 normal, string drawingId, float diameterM)
    {
        Prune();
        EvictIfNeeded();
        GameObject mark = PulsingRing.CreateRing(diameterM);
        mark.name = "Drawing_" + drawingId;
        mark.transform.position = point;
        Vector3 n = normal;
        if (n.sqrMagnitude < 1e-6f)
            n = Vector3.up;
        mark.transform.rotation = Quaternion.FromToRotation(Vector3.up, n.normalized);
        // World-locked: explicit root, never the camera.
        mark.transform.SetParent(null, true);
        TryAddAnchor(mark);
        _marks.Add(mark);
        return mark;
    }

    void TryAddAnchor(GameObject root)
    {
        try
        {
            if (root.GetComponent<OVRSpatialAnchor>() == null)
                root.AddComponent<OVRSpatialAnchor>();
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("DrawingStore: OVRSpatialAnchor unavailable (" + e.GetType().Name + ")");
        }
    }

    /// <summary>Remove all marks (later clear_session binding).</summary>
    public void Clear()
    {
        foreach (GameObject mark in _marks)
        {
            if (mark != null)
                DestroyMark(mark);
        }
        _marks.Clear();
    }

    /// <summary>Pure cap seam: true once the store holds MaxDrawings. Test-covered.</summary>
    public static bool NeedsEviction(int count)
    {
        return count >= MaxDrawings;
    }

    void Prune()
    {
        _marks.RemoveAll(g => g == null);
    }

    static void DestroyMark(GameObject mark)
    {
#if UNITY_EDITOR
        if (!Application.isPlaying)
            Object.DestroyImmediate(mark);
        else
            Object.Destroy(mark);
#else
        Object.Destroy(mark);
#endif
    }
}
