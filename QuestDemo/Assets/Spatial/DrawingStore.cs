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

    public GameObject PlaceMark(Vector3 point, Vector3 normal, string drawingId, float diameterM)
    {
        Prune();
        while (_marks.Count >= MaxDrawings)
        {
            GameObject oldest = _marks[0];
            _marks.RemoveAt(0);
            if (oldest != null)
                DestroyMark(oldest);
        }
        GameObject mark = PulsingRing.CreateRing(diameterM);
        mark.name = "Drawing_" + drawingId;
        mark.transform.position = point;
        Vector3 n = normal;
        if (n.sqrMagnitude < 1e-6f)
            n = Vector3.up;
        mark.transform.rotation = Quaternion.FromToRotation(Vector3.up, n.normalized);
        // World-locked: explicit root, never the camera.
        mark.transform.SetParent(null, true);
        try
        {
            if (mark.GetComponent<OVRSpatialAnchor>() == null)
                mark.AddComponent<OVRSpatialAnchor>();
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("DrawingStore: OVRSpatialAnchor unavailable (" + e.GetType().Name + ")");
        }
        _marks.Add(mark);
        return mark;
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
