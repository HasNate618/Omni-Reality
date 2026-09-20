using UnityEngine;

/// <summary>Placement verdict for one capture-time resolve.</summary>
public enum PlacementOutcome
{
    Placed,
    TooClose,
    NoSurface,
    Stale,
}

/// <summary>Result of <see cref="PlacementResolver.TryPlaceFromCapture"/>.</summary>
public struct PlacementResult
{
    public PlacementOutcome Outcome;
    public Vector3 Point;
    public Vector3 Normal;
    public bool HasTarget;
    public string ChipText;

    /// <summary>True only for a confident surface pin. All honest misses are false.</summary>
    public bool ShouldPin
    {
        get { return Outcome == PlacementOutcome.Placed && HasTarget; }
    }
}

/// <summary>
/// Capture-time placement verdicts (Task 5). Consumes the capture-time cache
/// entry/ray and <see cref="StaleMath"/> — never the live head pose.
/// too_close / no_surface / stale return the exact honest chip copy and never
/// a confident pin; forced miss (null delayed hit) returns chip + no pin.
/// </summary>
public static class PlacementResolver
{
    public const string ChipStaleText = "That moved, look again.";
    public const string ChipNoSurfaceText = "I can't plant that on a surface.";
    public const string ChipTooCloseText = "Too close for depth.";

    /// <summary>
    /// Brief-named entry point: resolve one capture from its cache entry.
    /// A cached hit pins only when the delayed re-query (same capture-time
    /// ray) agrees via <see cref="StaleMath"/>; the pin lands on the
    /// capture-time point. Capture misses, too-close frames, stale geometry,
    /// forced misses, and unknown/expired entries all return honest copy
    /// with <c>ShouldPin == false</c>.
    /// </summary>
    public static PlacementResult TryPlaceFromCapture(
        CaptureGeometryCache.Entry entry, string captureHonesty,
        Vector3? delayedPoint, bool sameRay)
    {
        // No capture-time geometry (unknown/expired frame): honest miss,
        // never a confident pin even if a delayed point is supplied.
        if (entry == null)
        {
            if (IsTooCloseHonesty(captureHonesty))
                return new PlacementResult { Outcome = PlacementOutcome.TooClose, ChipText = ChipTooCloseText };
            return new PlacementResult { Outcome = PlacementOutcome.NoSurface, ChipText = ChipNoSurfaceText };
        }
        Vector3? cachedPoint = entry.HasHit ? (Vector3?)entry.HitPoint : null;
        Vector3? cachedNormal = entry.HasHit && entry.HasNormal ? (Vector3?)entry.HitNormal : null;
        return Resolve(cachedPoint, cachedNormal, delayedPoint, sameRay, IsTooCloseHonesty(captureHonesty));
    }

    /// <summary>Pure verdict core mapping StaleMath onto outcomes. Test seam.</summary>
    public static PlacementResult Resolve(
        Vector3? cachedPoint, Vector3? cachedNormal,
        Vector3? delayedPoint, bool sameRay, bool captureWasTooClose)
    {
        if (captureWasTooClose)
        {
            return new PlacementResult { Outcome = PlacementOutcome.TooClose, ChipText = ChipTooCloseText };
        }
        string verdict = StaleMath.Classify(cachedPoint, delayedPoint, sameRay);
        if (verdict == "placed")
        {
            // Classify returns "placed" only with a delayed hit, so one of
            // the two points exists; prefer the capture-time point.
            Vector3 point = cachedPoint.HasValue ? cachedPoint.Value : delayedPoint.Value;
            Vector3 normal = Vector3.up;
            if (cachedNormal.HasValue && cachedNormal.Value.sqrMagnitude > 1e-6f)
                normal = cachedNormal.Value.normalized;
            return new PlacementResult
            {
                Outcome = PlacementOutcome.Placed,
                Point = point,
                Normal = normal,
                HasTarget = true,
            };
        }
        if (verdict == "stale")
        {
            return new PlacementResult { Outcome = PlacementOutcome.Stale, ChipText = ChipStaleText };
        }
        return new PlacementResult { Outcome = PlacementOutcome.NoSurface, ChipText = ChipNoSurfaceText };
    }

    public static bool IsTooCloseHonesty(string honesty)
    {
        return honesty == "too_close";
    }

    /// <summary>The capture-time ray to re-query for delayed validation (never live head).</summary>
    public static Ray CachedRay(CaptureGeometryCache.Entry entry)
    {
        return new Ray(entry.RayOrigin, entry.RayDirection);
    }
}
