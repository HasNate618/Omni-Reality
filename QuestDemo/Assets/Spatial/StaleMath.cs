using UnityEngine;

public static class StaleMath
{
    public const float ThresholdM = 0.12f;

    public static string Classify(Vector3? cached, Vector3? delayed, bool sameRay)
    {
        if (!delayed.HasValue)
        {
            return "no_surface";
        }
        if (!sameRay)
        {
            return "placed";
        }
        if (!cached.HasValue)
        {
            return "stale";
        }
        if (Vector3.Distance(cached.Value, delayed.Value) > ThresholdM)
        {
            return "stale";
        }
        return "placed";
    }
}
