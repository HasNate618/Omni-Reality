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
            // Unlit/Transparent, not Unlit/Color: Unlit/Color is an opaque pass
            // whose fragment forces alpha to 1, which would render the ghost solid.
            rend.sharedMaterial = new Material(Shader.Find("Unlit/Transparent"));
            Color c = PulsingRing.RingColor;
            c.a = 0.45f;
            rend.sharedMaterial.color = c;
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
        lr.sharedMaterial = new Material(Shader.Find("Unlit/Color"));
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
            ForgetGenerated(oldest);
            if (oldest != null)
                DestroyMark(oldest);
        }
    }

    /// <summary>
    /// Drop the generated record for an evicted root: HasGenerated must not
    /// keep claiming a drawing the clutter cap already removed.
    /// </summary>
    void ForgetGenerated(GameObject root)
    {
        // Unity's == treats two destroyed objects as equal, so compare only
        // while the evicted root is still alive.
        if (root == null)
            return;
        string key = null;
        foreach (KeyValuePair<string, GeneratedRecord> entry in _generated)
        {
            if (entry.Value.Root == root)
            {
                key = entry.Key;
                break;
            }
        }
        if (key != null)
            _generated.Remove(key);
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

    /// <summary>Procedural composition record for revise ops (voice spec §3.2).</summary>
    sealed class ProceduralRecord
    {
        public GameObject Root;
        public float BaseBoundingM;
    }

    readonly Dictionary<string, ProceduralRecord> _procedural = new Dictionary<string, ProceduralRecord>();

    /// <summary>Generated furniture record (spec §6.1): box, mesh, one id.</summary>
    sealed class GeneratedRecord
    {
        public GameObject Root;
        public GameObject Mesh;
        public Vector3 BoxSize;
        public bool Approximate;
    }

    readonly Dictionary<string, GeneratedRecord> _generated = new Dictionary<string, GeneratedRecord>();

    /// <summary>
    /// Register one generated placement as a normal drawing so remove, undo,
    /// the clutter cap, and revise all see it. Returns the root.
    /// </summary>
    /// <param name="offsetM">
    /// Frame-relative centre offset along the width axis (spec §7.1). The
    /// first item in a row is 0, so a lone placement lands on the hit.
    /// </param>
    public GameObject PlaceGenerated(
        Vector3 point, Vector3 normal, string drawingId, Vector3? extentM, float offsetM = 0f)
    {
        Prune();
        EvictIfNeeded();
        GameObject root = new GameObject("Drawing_" + drawingId);
        Vector3 n = normal.sqrMagnitude < 1e-6f ? Vector3.up : normal.normalized;
        Vector3 facing = -n;
        Vector3 boxSize = extentM.HasValue
            ? ListingBox.ToBoxSize(extentM.Value)
            : Vector3.one * 0.12f;
        Vector3 planted = point;
        if (Mathf.Abs(offsetM) > 1e-6f)
        {
            Vector3 flat = new Vector3(facing.x, 0f, facing.z);
            if (flat.sqrMagnitude < 1e-6f)
                flat = Vector3.forward;
            // Right-hand width axis of the placement frame: rows run along it.
            Vector3 widthAxis = Vector3.Cross(Vector3.up, flat.normalized);
            planted += widthAxis * offsetM;
        }
        GameObject box = ListingBox.Create(boxSize, planted, facing, drawingId);
        // Root and box coincide: the root carries the pose, the box is its
        // visual at local zero. Grab locks the root's Y, so the two must not
        // be offset from each other or a drag would double the height.
        root.transform.position = box.transform.position;
        root.transform.rotation = box.transform.rotation;
        box.transform.SetParent(root.transform, false);
        box.transform.localPosition = Vector3.zero;
        // SetParent(false) keeps the box's local rotation, which still equals
        // its former world rotation; the root carries that rotation already,
        // so leaving it would render the box at twice the yaw.
        box.transform.localRotation = Quaternion.identity;
        root.transform.SetParent(null, true);
        TryAddAnchor(root);
        _marks.Add(root);
        _generated[drawingId] = new GeneratedRecord
        {
            Root = root,
            Mesh = null,
            BoxSize = boxSize,
            Approximate = false,
        };
        // The collider must exist before the grab interactable: the
        // interactable caches its colliders in Awake, so a body added after
        // it would never be grabbable. Root and box coincide, so a collider
        // at the root's local zero wraps the box exactly.
        BoxCollider body = root.GetComponent<BoxCollider>();
        if (body == null)
            body = root.AddComponent<BoxCollider>();
        body.size = boxSize;
        body.center = Vector3.zero;
        // Generated furniture is grabbable on the floor plane. Root and box
        // coincide, so the locked height is simply the root's own Y.
        FloorPlaneGrab.Attach(root, root.transform.position.y);
        return root;
    }

    /// <summary>True when this drawing id is a generated placement.</summary>
    public bool HasGenerated(string drawingId)
    {
        return !string.IsNullOrEmpty(drawingId) && _generated.ContainsKey(drawingId);
    }

    /// <summary>
    /// Attach the imported mesh under the listed box and fit it uniformly.
    /// The box stays as the size claim; only its opacity changes.
    /// </summary>
    public void MarkMeshFitted(string drawingId, bool approximate)
    {
        GeneratedRecord rec = null;
        if (string.IsNullOrEmpty(drawingId) || !_generated.TryGetValue(drawingId, out rec))
            return;
        rec.Approximate = approximate;
        if (rec.Root == null)
            return;
        Renderer box = rec.Root.GetComponentInChildren<Renderer>();
        if (box != null && box.sharedMaterial != null)
        {
            Color c = box.sharedMaterial.color;
            c.a = approximate ? 0.35f : 0.12f;
            box.sharedMaterial.color = c;
        }
    }

    /// <summary>Fit an imported mesh object inside the recorded box.</summary>
    public bool FitMeshIntoBox(string drawingId, GameObject mesh)
    {
        GeneratedRecord rec = null;
        if (mesh == null || string.IsNullOrEmpty(drawingId) ||
            !_generated.TryGetValue(drawingId, out rec) || rec.Root == null)
            return false;
        Renderer[] renderers = mesh.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0)
            return false;
        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++)
            bounds.Encapsulate(renderers[i].bounds);
        // The imported holder sits at the world origin, so its bounds centre
        // is not its pivot. Capture both in the world frame before the
        // reparent, which is the only frame where they are still meaningful.
        Vector3 centreOffset = bounds.center - mesh.transform.position;
        float scale = ListingBox.FitScale(bounds.size, rec.BoxSize);
        mesh.transform.localScale = mesh.transform.localScale * scale;
        // worldPositionStays: false, so the mesh keeps its local transform and
        // would otherwise stay at the origin it was imported at. Uniform
        // scaling leaves the centre offset parallel to itself, only scaled.
        mesh.transform.SetParent(rec.Root.transform, false);
        mesh.transform.position = rec.Root.transform.position - centreOffset * scale;
        rec.Mesh = mesh;
        MarkMeshFitted(drawingId, ListingBox.IsApproximate(bounds.size, rec.BoxSize));
        return true;
    }

    /// <summary>
    /// Revise one generated placement. Nudge, rotate, and remove apply;
    /// enlarge and shrink are refused because the listed size is the claim
    /// (spec §7.3) and voice must not quietly break it.
    /// </summary>
    public bool ApplyGeneratedRevision(
        string drawingId, string action, string direction, out string error)
    {
        error = null;
        GeneratedRecord rec = null;
        if (string.IsNullOrEmpty(drawingId) || !_generated.TryGetValue(drawingId, out rec))
        {
            error = "invalid";
            return false;
        }
        if (rec.Root == null)
        {
            _generated.Remove(drawingId);
            error = "invalid";
            return false;
        }
        if (action == "remove")
        {
            _generated.Remove(drawingId);
            _marks.Remove(rec.Root);
            DestroyMark(rec.Root);
            return true;
        }
        if (action == "enlarge" || action == "shrink")
        {
            error = "invalid";
            return false;
        }
        Transform t = rec.Root.transform;
        if (action == "rotate_cw" || action == "rotate_ccw")
        {
            float step = action == "rotate_cw"
                ? -ProceduralFactory.RotateStepDeg
                : ProceduralFactory.RotateStepDeg;
            t.Rotate(Vector3.up, step, Space.Self);
            return true;
        }
        if (action == "nudge")
        {
            Vector3 flat = new Vector3(t.forward.x, 0f, t.forward.z);
            if (flat.sqrMagnitude < 1e-6f)
                flat = Vector3.forward;
            // The floor-plane frame the root is re-laid in. The direction is
            // resolved against that frame before it is written, so a rejected
            // nudge cannot rewrite the root's rotation — the same order the
            // procedural path uses.
            Quaternion frame = Quaternion.LookRotation(flat.normalized, Vector3.up);
            Vector3 dir;
            if (!NudgeDirection(direction, frame, out dir))
            {
                error = "invalid";
                return false;
            }
            t.rotation = frame;
            // Floor plane only: generated furniture is not lifted.
            dir.y = 0f;
            t.position += dir.normalized * ProceduralFactory.NudgeStepM;
            return true;
        }
        error = "invalid";
        return false;
    }

    public GameObject PlaceProcedural(
        Vector3 point, Vector3 normal, string drawingId,
        System.Collections.Generic.List<ProtocolJson.ProceduralElement> elements)
    {
        Prune();
        EvictIfNeeded();
        GameObject root = ProceduralFactory.BuildComposition(drawingId, elements);
        root.transform.position = point;
        Vector3 n = normal.sqrMagnitude < 1e-6f ? Vector3.up : normal.normalized;
        root.transform.rotation = Quaternion.FromToRotation(Vector3.up, n);
        root.transform.SetParent(null, true);
        TryAddAnchor(root);
        _marks.Add(root);
        _procedural[drawingId] = new ProceduralRecord
        {
            Root = root,
            BaseBoundingM = EstimateBounding(root),
        };
        return root;
    }

    static float EstimateBounding(GameObject root)
    {
        var renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0)
            return 0.1f;
        var bounds = new Bounds(root.transform.position, Vector3.zero);
        foreach (var rend in renderers)
            bounds.Encapsulate(rend.bounds);
        return Mathf.Max(bounds.size.x, Mathf.Max(bounds.size.y, bounds.size.z));
    }

    /// <summary>Apply one semantic revision; false with an ACK reason on reject.</summary>
    public bool ApplyProceduralRevision(string drawingId, string action, string direction, out string error)
    {
        error = null;
        ProceduralRecord rec = null;
        if (string.IsNullOrEmpty(drawingId) || !_procedural.TryGetValue(drawingId, out rec) || rec.Root == null)
        {
            if (rec != null && rec.Root == null)
                _procedural.Remove(drawingId);
            error = "invalid";
            return false;
        }
        Transform t = rec.Root.transform;
        if (action == "enlarge" || action == "shrink")
        {
            float factor = action == "enlarge"
                ? ProceduralFactory.EnlargeFactor
                : ProceduralFactory.ShrinkFactor;
            Vector3 next = t.localScale * factor;
            float bounding = rec.BaseBoundingM * next.x;
            if (bounding > ProceduralFactory.BoundingDiameterM || next.x <= 0f)
            {
                error = "invalid";
                return false;
            }
            t.localScale = next;
            return true;
        }
        if (action == "rotate_cw" || action == "rotate_ccw")
        {
            float step = action == "rotate_cw"
                ? -ProceduralFactory.RotateStepDeg
                : ProceduralFactory.RotateStepDeg;
            t.Rotate(Vector3.up, step, Space.Self);
            return true;
        }
        if (action == "nudge")
        {
            Vector3 dir;
            if (!NudgeDirection(direction, t.rotation, out dir))
            {
                error = "invalid";
                return false;
            }
            t.position += dir * ProceduralFactory.NudgeStepM;
            return true;
        }
        error = "invalid";
        return false;
    }

    /// <summary>
    /// The frame's axis for a direction word; false for an unknown word. The
    /// axis is the rotation applied to the world unit vector, which is exactly
    /// the transform's own right/up/forward for that rotation — so the
    /// procedural path is unchanged, and a caller can resolve a direction
    /// before writing the rotation it belongs to.
    /// </summary>
    static bool NudgeDirection(string direction, Quaternion frame, out Vector3 dir)
    {
        Vector3 right = frame * Vector3.right;
        Vector3 up = frame * Vector3.up;
        Vector3 forward = frame * Vector3.forward;
        switch (direction)
        {
            case "left": dir = -right; return true;
            case "right": dir = right; return true;
            case "up": dir = up; return true;
            case "down": dir = -up; return true;
            case "forward": dir = forward; return true;
            case "back": dir = -forward; return true;
            default: dir = Vector3.zero; return false;
        }
    }

    /// <summary>Delete a procedural drawing; false when unknown.</summary>
    public bool RemoveProcedural(string drawingId)
    {
        ProceduralRecord rec = null;
        if (string.IsNullOrEmpty(drawingId) || !_procedural.TryGetValue(drawingId, out rec))
            return false;
        _procedural.Remove(drawingId);
        _marks.Remove(rec.Root);
        if (rec.Root != null)
            DestroyMark(rec.Root);
        return true;
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
        _procedural.Clear();
        _generated.Clear();
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
