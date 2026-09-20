using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// B-button resize: aim at a piece, hold the index trigger, move your hand
/// away to grow it and back to shrink it.
///
/// Scale is uniform and anchored to the listing size, not to whatever the
/// piece currently is, so repeated grabs cannot drift. It is clamped hard --
/// a 3 m armchair is not a fit check, it is a joke.
/// </summary>
public sealed class LayoutResize : MonoBehaviour
{
    /// <summary>Hand travel, in metres, that doubles the piece.</summary>
    public const float MetresPerDoubling = 0.35f;

    LayoutBox _held;
    float _grabDistance;
    float _grabScale;

    public bool IsResizing { get { return _held != null; } }
    public LayoutBox Held { get { return _held; } }

    public void Release()
    {
        _held = null;
    }

    /// <summary>
    /// Scale implied by how far the hand moved along the aim ray. Pure, so the
    /// mapping is testable without a controller.
    /// </summary>
    public static float ScaleFor(float grabDistance, float currentDistance, float grabScale)
    {
        float delta = currentDistance - grabDistance;
        float factor = Mathf.Pow(2f, delta / MetresPerDoubling);
        return Mathf.Clamp(grabScale * factor, LayoutBox.MinScale, LayoutBox.MaxScale);
    }

    /// <summary>Driven from LayoutMode so input ownership stays in one place.</summary>
    public void Tick(List<LayoutBox> boxes, LayoutPointer pointer, LayoutBox aimed)
    {
        if (pointer == null || !pointer.HasAim)
            return;
        bool down = OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch);
        bool held = OVRInput.Get(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch);
        Ray ray = pointer.AimRay;

        if (_held != null && !held)
        {
            Debug.Log("QUEST_LAYOUT resized " + _held.ItemLabel
                      + " to " + Mathf.RoundToInt(_held.ScaleFactor * 100f) + "% of the listing");
            _held = null;
            return;
        }

        if (_held == null)
        {
            if (down && aimed != null)
            {
                _held = aimed;
                _grabDistance = DistanceAlongRay(ray, aimed.transform.position);
                _grabScale = aimed.ScaleFactor;
            }
            return;
        }

        float now = DistanceAlongRay(ray, _held.transform.position);
        float target = ScaleFor(_grabDistance, now, _grabScale);
        float factor = _held.ScaleFactor > 0f ? target / _held.ScaleFactor : 1f;
        if (Mathf.Abs(factor - 1f) > 0.002f)
            _held.ApplyScale(factor);
    }

    static float DistanceAlongRay(Ray ray, Vector3 point)
    {
        return Vector3.Dot(point - ray.origin, ray.direction.normalized);
    }
}
