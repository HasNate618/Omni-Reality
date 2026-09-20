using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Layout mode: the cart lands in your corner as true-size boxes.
///
/// Owns the box set, resolves one corner frame per summon so every box in a
/// turn shares it, and reports clearance. Every number shown here comes from
/// a listing or from depth sensing, so the caption says "approximate" and the
/// copy never states a figure as measured. That is not decoration: the repo's
/// own research brief warns that fit guidance needs known dimensions and real
/// calibration, and this has neither.
/// </summary>
public sealed class LayoutMode : MonoBehaviour
{
    public const float BoxAlpha = 0.28f;
    public const float TightClearanceM = 0.10f;
    public static readonly Color DefaultTint = new Color(0.24f, 0.86f, 1f, BoxAlpha);

    public const string HonestyCopy =
        "Approximate. Sizes from the listing, room from depth sensing.";

    static LayoutMode _instance;

    readonly List<LayoutBox> _boxes = new List<LayoutBox>();
    LayoutRoom _room;
    LayoutDrag _drag;
    LayoutResize _resize;
    LayoutPointer _pointer;
    FurnitureSelector _selector;
    RotateMenu _rotate;
    float _lastTapAt = -1f;
    LayoutBox _lastTapBox;
    LayoutRoom.CornerFrame _corner;
    bool _hasCorner;
    int _cornerTurn = -1;
    Transform _centerEye;
    LayoutLabel _status;
    float _statusUntil;
    bool _resizeMode;
    bool _armed;

    public int BoxCount { get { return _boxes.Count; } }
    public IList<LayoutBox> Boxes { get { return _boxes; } }
    public bool ResizeMode { get { return _resizeMode; } }

    /// <summary>Two taps closer together than this open the rotate menu.</summary>
    public const float DoubleTapSeconds = 0.35f;

    /// <summary>Pure so the gesture can be tested without a controller.</summary>
    public static bool IsDoubleTap(float previousTapAt, float now, bool sameTarget)
    {
        return sameTarget && previousTapAt > 0f && (now - previousTapAt) <= DoubleTapSeconds;
    }

    /// <summary>
    /// True once a layout turn has placed anything this session. A and B are
    /// Layout's while this holds; before it, they still belong to the
    /// push-to-talk and conversation modes.
    /// </summary>
    public static bool IsArmed
    {
        get { return _instance != null && _instance._armed; }
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Bootstrap()
    {
        Ensure();
    }

    public static LayoutMode Ensure()
    {
        if (_instance != null)
            return _instance;
        var go = new GameObject("LayoutMode");
        DontDestroyOnLoad(go);
        _instance = go.AddComponent<LayoutMode>();
        return _instance;
    }

    void Awake()
    {
        _instance = this;
        _room = gameObject.AddComponent<LayoutRoom>();
        _drag = gameObject.AddComponent<LayoutDrag>();
        _resize = gameObject.AddComponent<LayoutResize>();
        _pointer = gameObject.AddComponent<LayoutPointer>();
        var menu = new GameObject("FurnitureSelector");
        menu.transform.SetParent(null, true);
        _selector = menu.AddComponent<FurnitureSelector>();
        var rotateGO = new GameObject("RotateMenu");
        rotateGO.transform.SetParent(null, true);
        _rotate = rotateGO.AddComponent<RotateMenu>();
        var centerGO = GameObject.Find("CenterEyeAnchor");
        if (centerGO != null)
            _centerEye = centerGO.transform;
    }

    void Update()
    {
        if (_centerEye == null)
        {
            var centerGO = GameObject.Find("CenterEyeAnchor");
            if (centerGO != null)
                _centerEye = centerGO.transform;
        }
        Prune();
        if (!_armed)
            return;

        // A opens the furniture menu; B toggles resize. Button.Two is the one
        // that reads as A on this headset -- measured on device, not assumed.
        // Both only once Layout owns them, so a normal A/B run is untouched.
        if (OVRInput.GetDown(OVRInput.Button.Two, OVRInput.Controller.RTouch))
            ToggleSelector();
        if (OVRInput.GetDown(OVRInput.Button.One, OVRInput.Controller.RTouch))
            ToggleResizeMode();

        if (_pointer == null || !_pointer.HasAim)
            return;
        Ray ray = _pointer.AimRay;

        if (_selector != null && _selector.IsOpen)
        {
            TickSelector(ray);
            return;
        }
        if (_rotate != null && _rotate.IsOpen)
        {
            TickRotate(ray);
            return;
        }

        Vector3 floorPoint;
        bool onFloor = LayoutDrag.TryFloorPoint(ray, _hasCorner ? _corner.Origin.y : 0f,
                                                out floorPoint);
        LayoutBox aimed = LayoutDrag.PickByRay(
            _boxes, ray, onFloor, new Vector2(floorPoint.x, floorPoint.z));

        // Double tap on a piece opens rotate. Checked before drag and resize
        // consume the press, and only on a piece, so a tap at empty floor
        // never opens a menu attached to nothing.
        if (OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
        {
            float now = Time.realtimeSinceStartup;
            if (aimed != null && IsDoubleTap(_lastTapAt, now, ReferenceEquals(aimed, _lastTapBox)))
            {
                _lastTapAt = -1f;
                _lastTapBox = null;
                if (_drag != null)
                    _drag.Release();
                if (_resize != null)
                    _resize.Release();
                _rotate.Open(aimed);
                ShowStatus("Rotate " + aimed.ItemLabel + ". Pick an angle, or done.", 6f);
                return;
            }
            _lastTapAt = now;
            _lastTapBox = aimed;
        }

        if (onFloor)
            _pointer.Draw(aimed != null ? aimed.transform.position : floorPoint,
                          aimed != null, _resizeMode);
        else
            _pointer.DrawMiss();

        if (_boxes.Count == 0)
            return;

        if (_resizeMode)
        {
            _resize.Tick(_boxes, _pointer, aimed);
            if (aimed != null)
                aimed.SetHighlighted(true);
            if (OVRInput.GetUp(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
                AnnounceFit();
            return;
        }

        _drag.Tick(_boxes, onFloor, floorPoint, aimed);
        // Clearance is only worth recomputing once the wearer lets go.
        if (!_drag.IsDragging && OVRInput.GetUp(
                OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
            AnnounceFit();
    }

    void TickSelector(Ray ray)
    {
        Vector3 hit;
        int index = _selector.IndexUnderRay(ray, out hit);
        _selector.SetHover(index);
        if (index >= 0)
            _pointer.Draw(hit, true, false);
        else
            _pointer.DrawMiss();
        if (index >= 0 && OVRInput.GetDown(
                OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
        {
            AddFromCatalog(index);
            _selector.Close();
        }
    }

    void TickRotate(Ray ray)
    {
        _rotate.Reposition();
        Vector3 hit;
        int index = _rotate.IndexUnderRay(ray, out hit);
        _rotate.SetHover(index);
        if (index >= 0)
            _pointer.Draw(hit, true, false);
        else
            _pointer.DrawMiss();
        if (index < 0 || !OVRInput.GetDown(
                OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
            return;
        if (!_rotate.Apply(index))
        {
            _rotate.Close();
            AnnounceFit();
        }
    }

    void ToggleSelector()
    {
        if (_selector == null)
            return;
        if (_resizeMode && !_selector.IsOpen)
            SetResizeMode(false);
        if (_rotate != null && _rotate.IsOpen)
            _rotate.Close();
        _selector.Toggle();
        ShowStatus(_selector.IsOpen
            ? "Pick a piece. Point and pull the trigger."
            : HonestyCopy, 4f);
    }

    void ToggleResizeMode()
    {
        SetResizeMode(!_resizeMode);
    }

    void SetResizeMode(bool on)
    {
        if (_resizeMode == on)
            return;
        _resizeMode = on;
        if (_resize != null)
            _resize.Release();
        if (_drag != null)
            _drag.Release();
        if (on && _selector != null && _selector.IsOpen)
            _selector.Close();
        if (on && _rotate != null && _rotate.IsOpen)
            _rotate.Close();
        Debug.Log("QUEST_LAYOUT resize mode " + (on ? "ON" : "OFF"));
        ShowStatus(on
            ? "Resize: hold the trigger on a piece, move your hand in and out."
            : "Move: hold the trigger on a piece to slide it.", 4f);
    }

    /// <summary>Add a catalog piece, dropped where the wearer is looking.</summary>
    public LayoutBox AddFromCatalog(int index)
    {
        FurnitureCatalog.Entry e = FurnitureCatalog.At(index);
        LayoutBox box = LayoutBox.Create(e.Label, e.SizeM, e.Tint, e.Kind);
        if (box == null)
            return null;
        if (!_hasCorner)
        {
            _corner = _room.Resolve();
            _hasCorner = true;
            if (_drag != null)
                _drag.SetFloorY(_corner.Origin.y);
        }
        Vector3 spot = FreeSpot(e.SizeM);
        box.transform.SetPositionAndRotation(
            new Vector3(spot.x, _corner.Origin.y + (e.SizeM.y * 0.5f), spot.z),
            Quaternion.LookRotation(_corner.AxisZ, Vector3.up));
        _boxes.Add(box);
        Debug.Log("QUEST_LAYOUT added " + e.Label + " from the selector");
        ShowStatus(e.Label + " added. " + HonestyCopy, 5f);
        return box;
    }

    /// <summary>A spot in front of the wearer that no existing piece occupies.</summary>
    Vector3 FreeSpot(Vector3 sizeM)
    {
        Vector3 basePoint = _corner.Origin + (_corner.AxisX * 0.9f) + (_corner.AxisZ * 0.9f);
        if (_centerEye != null)
        {
            Vector3 forward = _centerEye.forward;
            forward.y = 0f;
            if (forward.sqrMagnitude > 1e-6f)
            {
                Vector3 eye = _centerEye.position;
                basePoint = new Vector3(eye.x, _corner.Origin.y, eye.z)
                            + (forward.normalized * 1.3f);
            }
        }
        // Walk sideways until the footprint stops overlapping something.
        for (int step = 0; step < 8; step++)
        {
            Vector3 candidate = basePoint + (_corner.AxisX * (step * 0.45f));
            if (!Occupied(candidate, sizeM))
                return candidate;
        }
        return basePoint;
    }

    bool Occupied(Vector3 point, Vector3 sizeM)
    {
        var centre = new Vector2(point.x, point.z);
        var half = new Vector2(sizeM.x * 0.5f, sizeM.z * 0.5f);
        for (int i = 0; i < _boxes.Count; i++)
        {
            if (_boxes[i] == null)
                continue;
            Vector2 c = _boxes[i].FootprintCentre();
            Vector2 h = _boxes[i].FootprintHalfExtents();
            if (Mathf.Abs(c.x - centre.x) < (h.x + half.x)
                && Mathf.Abs(c.y - centre.y) < (h.y + half.y))
                return true;
        }
        return false;
    }

    /// <summary>Handle one `place_box` op. Returns false when it is unusable.</summary>
    public bool TryPlaceBox(ProtocolJson.SceneOpMsg op)
    {
        if (op == null || !op.HasSize || !op.HasSlot)
        {
            Debug.LogWarning("QUEST_LAYOUT place_box missing size_m or layout_slot");
            return false;
        }
        var size = new Vector3(op.SizeW, op.SizeH, op.SizeD);
        if (!LayoutBox.IsUsableSize(size))
        {
            Debug.LogWarning("QUEST_LAYOUT place_box rejected: implausible size");
            return false;
        }
        // One corner per turn: boxes from the same summon must share a frame,
        // or they land in three slightly different rooms.
        if (!_hasCorner || op.TurnId != _cornerTurn)
        {
            ClearBoxes();
            _corner = _room.Resolve();
            _hasCorner = true;
            _cornerTurn = op.TurnId;
            _drag.SetFloorY(_corner.Origin.y);
            Debug.Log("QUEST_LAYOUT corner " + (_corner.FromWalls ? "from walls" : "fallback (no walls found)")
                      + " at " + _corner.Origin.ToString("F2"));
        }

        Vector3 position;
        Quaternion rotation;
        LayoutBox.SlotToWorld(_corner, op.SlotDx, op.SlotDz, op.SlotYawDeg, size.y,
                              out position, out rotation);
        LayoutBox box = LayoutBox.Create(op.StyleLabel, size, ParseTint(op.StyleColor),
                                         op.Furniture);
        if (box == null)
            return false;
        box.transform.SetPositionAndRotation(position, rotation);
        _boxes.Add(box);
        _armed = true;
        Debug.Log("QUEST_LAYOUT placed " + box.SizeCaption().Replace("\n", " "));
        ShowStatus("A: add furniture    B: resize    double-tap: rotate\n" + HonestyCopy, 8f);
        return true;
    }

    public static Color ParseTint(string hex)
    {
        Color parsed;
        if (!string.IsNullOrEmpty(hex) && ColorUtility.TryParseHtmlString(hex, out parsed))
        {
            parsed.a = BoxAlpha;
            return parsed;
        }
        return DefaultTint;
    }

    public void ClearBoxes()
    {
        if (_drag != null)
            _drag.Release();
        if (_resize != null)
            _resize.Release();
        if (_rotate != null)
            _rotate.Close();
        _lastTapBox = null;
        for (int i = 0; i < _boxes.Count; i++)
        {
            if (_boxes[i] != null)
                LayoutBox.DestroySafe(_boxes[i].gameObject);
        }
        _boxes.Clear();
    }

    void Prune()
    {
        for (int i = _boxes.Count - 1; i >= 0; i--)
        {
            if (_boxes[i] == null)
                _boxes.RemoveAt(i);
        }
    }

    void AnnounceFit()
    {
        string verdict = FitVerdict(_boxes);
        if (string.IsNullOrEmpty(verdict))
            return;
        Debug.Log("QUEST_LAYOUT fit " + verdict);
        ShowStatus(verdict + "\n" + HonestyCopy, 6f);
    }

    /// <summary>
    /// Plain-language clearance between footprints. Hedged on purpose: these
    /// are listing sizes on a depth-sensed floor, not a measurement.
    /// </summary>
    public static string FitVerdict(IList<LayoutBox> boxes)
    {
        if (boxes == null || boxes.Count < 2)
            return "";
        float tightest = float.MaxValue;
        string a = "", b = "";
        for (int i = 0; i < boxes.Count; i++)
        {
            for (int j = i + 1; j < boxes.Count; j++)
            {
                if (boxes[i] == null || boxes[j] == null)
                    continue;
                float gap = Clearance(boxes[i], boxes[j]);
                if (gap < tightest)
                {
                    tightest = gap;
                    a = boxes[i].ItemLabel;
                    b = boxes[j].ItemLabel;
                }
            }
        }
        if (tightest == float.MaxValue)
            return "";
        if (tightest < 0f)
            return string.Format("Roughly -- the {0} and the {1} overlap.", a, b);
        if (tightest < TightClearanceM)
            return string.Format("Roughly -- about {0:0} cm between the {1} and the {2}. Tight.",
                                 tightest * 100f, a, b);
        return string.Format("Roughly -- about {0:0} cm between the {1} and the {2}.",
                             tightest * 100f, a, b);
    }

    /// <summary>
    /// Gap between two footprints in metres; negative when they overlap.
    /// Axis-aligned on the yaw-expanded extents, which over-estimates a
    /// rotated box slightly -- erring towards "tight" rather than "it fits".
    /// </summary>
    public static float Clearance(LayoutBox a, LayoutBox b)
    {
        Vector2 ca = a.FootprintCentre();
        Vector2 cb = b.FootprintCentre();
        Vector2 ha = a.FootprintHalfExtents();
        Vector2 hb = b.FootprintHalfExtents();
        float gapX = Mathf.Abs(ca.x - cb.x) - (ha.x + hb.x);
        float gapZ = Mathf.Abs(ca.y - cb.y) - (ha.y + hb.y);
        // Separating-axis gap: boxes overlap only when both axes do, so the
        // larger value is the real separation and is negative on overlap.
        return Mathf.Max(gapX, gapZ);
    }

    /// <summary>
    /// Layout's own status line. VoiceCaption is small and sits low; this has
    /// to be read while walking around the corner, so it is a LayoutLabel at
    /// three times the character size, held in front of the wearer.
    /// </summary>
    void ShowStatus(string text, float seconds)
    {
        if (_status == null)
        {
            _status = LayoutLabel.Create(transform, Color.white);
            _status.transform.SetParent(null, true);
        }
        _status.SetText(text);
        _status.SetVisible(true);
        _statusUntil = Time.realtimeSinceStartup + Mathf.Max(3f, seconds);
    }

    void LateUpdate()
    {
        if (_status == null)
            return;
        if (Time.realtimeSinceStartup >= _statusUntil)
        {
            _status.SetVisible(false);
            return;
        }
        if (_centerEye == null)
            return;
        Vector3 forward = _centerEye.forward;
        forward.y = 0f;
        if (forward.sqrMagnitude < 1e-6f)
            return;
        // High and far: tips sitting in front of the furniture block the very
        // thing they describe, so this rides near the top of the view instead.
        _status.transform.position = _centerEye.position
                                     + (forward.normalized * 2.0f)
                                     + (Vector3.up * 0.62f);
    }
}
