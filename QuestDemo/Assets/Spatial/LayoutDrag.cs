using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Grab a layout box with the index trigger and slide it along the floor.
///
/// This is the interaction that survives a dead key. Speech needs a model to
/// transcribe it; pushing a box does not, so direct manipulation is both the
/// primary way to rearrange the corner and the fallback for everything else.
///
/// Boxes carry no colliders (nothing in this project does), so picking is a
/// ray-vs-floor hit tested against each footprint rectangle. Pointing at the
/// floor where a box stands is also easier than hitting it in the air.
/// </summary>
public sealed class LayoutDrag : MonoBehaviour
{
    /// <summary>Grab radius past the footprint edge, so near-misses still catch.</summary>
    public const float GrabMarginM = 0.08f;
    public const float MaxReachM = 6f;

    LayoutBox _hover;
    LayoutBox _held;
    Vector2 _grabOffset;
    float _floorY;

    public bool IsDragging { get { return _held != null; } }
    public LayoutBox Held { get { return _held; } }

    public void SetFloorY(float y)
    {
        _floorY = y;
    }

    /// <summary>
    /// Drive from LayoutMode's Update so input ownership stays in one place.
    /// The aim is resolved once by the caller and shared with resize, so the
    /// two modes can never disagree about which piece you are pointing at.
    /// </summary>
    public void Tick(List<LayoutBox> boxes, bool onFloor, Vector3 floorPoint, LayoutBox aimed)
    {
        if (boxes == null)
            return;
        bool down = OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch);
        bool held = OVRInput.Get(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch);

        if (_held != null && !held)
        {
            Debug.Log("QUEST_LAYOUT dropped " + _held.ItemLabel);
            _held = null;
        }

        if (!onFloor)
        {
            SetHover(null);
            return;
        }

        if (_held != null)
        {
            Vector3 p = _held.transform.position;
            _held.transform.position = new Vector3(
                floorPoint.x + _grabOffset.x, p.y, floorPoint.z + _grabOffset.y);
            return;
        }

        LayoutBox pick = aimed;
        SetHover(pick);
        if (down && pick != null)
        {
            _held = pick;
            Vector2 centre = pick.FootprintCentre();
            _grabOffset = new Vector2(centre.x - floorPoint.x, centre.y - floorPoint.z);
            Debug.Log("QUEST_LAYOUT grabbed " + pick.ItemLabel);
        }
    }

    void SetHover(LayoutBox next)
    {
        if (ReferenceEquals(_hover, next))
            return;
        if (_hover != null)
            _hover.SetHighlighted(false);
        _hover = next;
        if (_hover != null)
            _hover.SetHighlighted(true);
    }

    public void Release()
    {
        _held = null;
        SetHover(null);
    }

    /// <summary>Where the aim ray meets the floor, if it points downward at all.</summary>
    public static bool TryFloorPoint(Ray ray, float floorY, out Vector3 point)
    {
        point = Vector3.zero;
        if (ray.direction.y >= -1e-4f)
            return false;
        float t = (floorY - ray.origin.y) / ray.direction.y;
        if (t < 0f || t > MaxReachM)
            return false;
        point = ray.origin + (ray.direction * t);
        return true;
    }

    /// <summary>How far past a footprint edge a near-miss still locks on.</summary>
    public const float SnapRadiusM = 0.45f;

    /// <summary>
    /// What the wearer is aiming at, in the order a person would expect:
    /// the piece the ray actually passes through, else the piece standing on
    /// the floor point, else the nearest piece within arm's reach of it.
    ///
    /// Footprint-only picking meant you had to aim at the floor and ignore the
    /// sofa in front of you, which is why lock-on felt fussy.
    /// </summary>
    public static LayoutBox PickByRay(
        List<LayoutBox> boxes, Ray ray, bool onFloor, Vector2 floorXz)
    {
        if (boxes == null)
            return null;

        LayoutBox best = null;
        float bestT = float.MaxValue;
        for (int i = 0; i < boxes.Count; i++)
        {
            float t;
            if (boxes[i] == null || !RayHitsBody(boxes[i], ray, out t))
                continue;
            if (t < bestT)
            {
                bestT = t;
                best = boxes[i];
            }
        }
        if (best != null)
            return best;
        if (!onFloor)
            return null;

        LayoutBox onFoot = PickAt(boxes, floorXz);
        if (onFoot != null)
            return onFoot;
        return NearestWithin(boxes, floorXz, SnapRadiusM);
    }

    /// <summary>Ray against the piece's own box, in the piece's local frame.</summary>
    public static bool RayHitsBody(LayoutBox box, Ray ray, out float distance)
    {
        distance = 0f;
        Transform t = box.transform;
        Vector3 o = t.InverseTransformPoint(ray.origin);
        Vector3 d = t.InverseTransformDirection(ray.direction);
        Vector3 half = box.SizeM * 0.5f;

        float near = 0f;
        float far = MaxReachM;
        for (int axis = 0; axis < 3; axis++)
        {
            float origin = axis == 0 ? o.x : (axis == 1 ? o.y : o.z);
            float dir = axis == 0 ? d.x : (axis == 1 ? d.y : d.z);
            float h = axis == 0 ? half.x : (axis == 1 ? half.y : half.z);
            if (Mathf.Abs(dir) < 1e-6f)
            {
                if (origin < -h || origin > h)
                    return false;
                continue;
            }
            float t1 = (-h - origin) / dir;
            float t2 = (h - origin) / dir;
            if (t1 > t2)
            {
                float swap = t1;
                t1 = t2;
                t2 = swap;
            }
            near = Mathf.Max(near, t1);
            far = Mathf.Min(far, t2);
            if (near > far)
                return false;
        }
        distance = near;
        return true;
    }

    /// <summary>
    /// Closest piece whose footprint *edge* is within `radius` of the point.
    ///
    /// Measuring to the centre instead would mean a 1.8 m sofa refused to lock
    /// on while you pointed right beside it, because its centre is nearly a
    /// metre away. Edge distance is what "near the sofa" actually means.
    /// </summary>
    public static LayoutBox NearestWithin(List<LayoutBox> boxes, Vector2 floorXz, float radius)
    {
        LayoutBox best = null;
        float bestDist = radius;
        for (int i = 0; i < boxes.Count; i++)
        {
            if (boxes[i] == null)
                continue;
            float d = EdgeDistance(boxes[i], floorXz);
            if (d <= bestDist)
            {
                bestDist = d;
                best = boxes[i];
            }
        }
        return best;
    }

    /// <summary>Metres from a point to the footprint, 0 when inside it.</summary>
    public static float EdgeDistance(LayoutBox box, Vector2 floorXz)
    {
        Vector2 c = box.FootprintCentre();
        Vector2 h = box.FootprintHalfExtents();
        float dx = Mathf.Max(0f, Mathf.Abs(c.x - floorXz.x) - h.x);
        float dz = Mathf.Max(0f, Mathf.Abs(c.y - floorXz.y) - h.y);
        return Mathf.Sqrt((dx * dx) + (dz * dz));
    }

    /// <summary>Topmost box whose footprint contains the point; nearest centre wins ties.</summary>
    /// <summary>Topmost box whose footprint contains the point; nearest centre wins ties.</summary>
    public static LayoutBox PickAt(List<LayoutBox> boxes, Vector2 floorXz)
    {
        LayoutBox best = null;
        float bestDist = float.MaxValue;
        for (int i = 0; i < boxes.Count; i++)
        {
            LayoutBox b = boxes[i];
            if (b == null)
                continue;
            if (!ContainsOnFloor(b, floorXz))
                continue;
            float d = (b.FootprintCentre() - floorXz).sqrMagnitude;
            if (d < bestDist)
            {
                bestDist = d;
                best = b;
            }
        }
        return best;
    }

    /// <summary>Point-in-rotated-rectangle, in the box's own yaw frame.</summary>
    public static bool ContainsOnFloor(LayoutBox box, Vector2 floorXz)
    {
        Vector2 centre = box.FootprintCentre();
        float yaw = -box.transform.eulerAngles.y * Mathf.Deg2Rad;
        Vector2 d = floorXz - centre;
        float c = Mathf.Cos(yaw);
        float s = Mathf.Sin(yaw);
        float lx = (d.x * c) - (d.y * s);
        float lz = (d.x * s) + (d.y * c);
        return Mathf.Abs(lx) <= (box.SizeM.x * 0.5f) + GrabMarginM
            && Mathf.Abs(lz) <= (box.SizeM.z * 0.5f) + GrabMarginM;
    }
}
