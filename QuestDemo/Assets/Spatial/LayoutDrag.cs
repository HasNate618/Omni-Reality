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
