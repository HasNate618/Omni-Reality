using System;
using Meta.XR;
using UnityEngine;

/// <summary>
/// Finds the corner the wearer is standing in, so layout slots can be
/// corner-relative and the laptop never needs room geometry.
///
/// Two wall hits from EnvironmentRaycastManager, intersected on the floor.
/// MRUK would give this more cheaply, but it needs Scene permission and a
/// captured room -- risk we do not need when two raycasts and a fallback
/// place boxes either way. A failed detection never blocks the demo: the
/// fallback frame sits in front of the wearer and everything still works.
/// </summary>
public sealed class LayoutRoom : MonoBehaviour
{
    public const float MaxWallDistanceM = 6f;
    public const float MinWallSeparationDeg = 40f;
    /// <summary>Fallback corner sits this far in front of the wearer.</summary>
    public const float FallbackAheadM = 0.6f;

    public struct CornerFrame
    {
        public Vector3 Origin;
        /// <summary>Unit, horizontal, pointing into the room away from wall B.</summary>
        public Vector3 AxisX;
        /// <summary>Unit, horizontal, pointing into the room away from wall A.</summary>
        public Vector3 AxisZ;
        public bool FromWalls;
    }

    EnvironmentRaycastManager _raycast;
    Transform _centerEye;

    void Awake()
    {
        _raycast = FindAnyObjectByType<EnvironmentRaycastManager>();
        var centerGO = GameObject.Find("CenterEyeAnchor");
        if (centerGO != null)
            _centerEye = centerGO.transform;
    }

    public bool HasEye { get { return _centerEye != null; } }

    /// <summary>Best available corner frame. Always returns something usable.</summary>
    public CornerFrame Resolve()
    {
        CornerFrame corner;
        if (TryResolveFromWalls(out corner))
            return corner;
        return FallbackFrame();
    }

    bool TryResolveFromWalls(out CornerFrame corner)
    {
        corner = default(CornerFrame);
        if (_raycast == null || _centerEye == null)
            return false;

        Vector3 eye = _centerEye.position;
        Vector3 forward = Flatten(_centerEye.forward);
        if (forward.sqrMagnitude < 1e-6f)
            return false;
        Vector3 right = Vector3.Cross(Vector3.up, forward).normalized;

        // A fan either side of where the wearer is looking. Walls meeting in a
        // corner are roughly 90 degrees apart, so the outer rays find them.
        Vector3 a, na, b, nb;
        if (!TryWall(eye, forward, right, new float[] { -55f, -35f, -15f }, out a, out na))
            return false;
        if (!TryWall(eye, forward, right, new float[] { 55f, 35f, 15f }, out b, out nb))
            return false;
        if (Vector3.Angle(na, nb) < MinWallSeparationDeg)
            return false;

        Vector3 origin;
        if (!TryIntersectWalls(a, na, b, nb, out origin))
            return false;

        // Floor height comes from the tracking origin, which ARSetup pins to
        // FloorLevel and aborts the build if it is not.
        origin.y = 0f;
        Vector3 axisZ = na;
        Vector3 axisX = Vector3.ProjectOnPlane(nb, axisZ).normalized;
        if (axisX.sqrMagnitude < 1e-6f)
            return false;
        corner.Origin = origin;
        corner.AxisZ = axisZ;
        corner.AxisX = axisX;
        corner.FromWalls = true;
        return true;
    }

    bool TryWall(Vector3 eye, Vector3 forward, Vector3 right, float[] anglesDeg,
                 out Vector3 point, out Vector3 normal)
    {
        point = Vector3.zero;
        normal = Vector3.zero;
        for (int i = 0; i < anglesDeg.Length; i++)
        {
            Vector3 dir = Quaternion.AngleAxis(anglesDeg[i], Vector3.up) * forward;
            EnvironmentRaycastHit hit;
            try
            {
                if (!_raycast.Raycast(new Ray(eye, dir), out hit, MaxWallDistanceM))
                    continue;
                if (hit.status != EnvironmentRaycastHitStatus.Hit)
                    continue;
            }
            catch (Exception)
            {
                return false;
            }
            Vector3 flat = Flatten(hit.normal);
            // A near-vertical normal is floor or ceiling, not a wall.
            if (flat.sqrMagnitude < 0.25f)
                continue;
            flat = flat.normalized;
            // Point the normal back towards the wearer: that is "into the room".
            if (Vector3.Dot(flat, Flatten(eye - hit.point)) < 0f)
                flat = -flat;
            point = hit.point;
            normal = flat;
            return true;
        }
        return false;
    }

    /// <summary>Intersect two vertical planes on the floor plane (2D solve in xz).</summary>
    public static bool TryIntersectWalls(
        Vector3 pa, Vector3 na, Vector3 pb, Vector3 nb, out Vector3 corner)
    {
        corner = Vector3.zero;
        float a1 = na.x, b1 = na.z, c1 = (na.x * pa.x) + (na.z * pa.z);
        float a2 = nb.x, b2 = nb.z, c2 = (nb.x * pb.x) + (nb.z * pb.z);
        float det = (a1 * b2) - (a2 * b1);
        if (Mathf.Abs(det) < 1e-4f)
            return false;
        corner = new Vector3(((c1 * b2) - (c2 * b1)) / det, 0f, ((a1 * c2) - (a2 * c1)) / det);
        return !float.IsNaN(corner.x) && !float.IsNaN(corner.z);
    }

    /// <summary>No walls found: put the corner just ahead of the wearer.</summary>
    public CornerFrame FallbackFrame()
    {
        Vector3 forward = Vector3.forward;
        Vector3 origin = Vector3.zero;
        if (_centerEye != null)
        {
            Vector3 flat = Flatten(_centerEye.forward);
            if (flat.sqrMagnitude > 1e-6f)
                forward = flat.normalized;
            Vector3 feet = _centerEye.position;
            origin = new Vector3(feet.x, 0f, feet.z) + (forward * FallbackAheadM);
        }
        return new CornerFrame
        {
            Origin = origin,
            AxisZ = forward,
            AxisX = Vector3.Cross(Vector3.up, forward).normalized,
            FromWalls = false,
        };
    }

    static Vector3 Flatten(Vector3 v)
    {
        return new Vector3(v.x, 0f, v.z);
    }
}
