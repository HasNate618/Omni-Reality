using UnityEngine;

/// <summary>
/// The listed-size claim for one placed product (spec §6). The box is the
/// size the page stated; a generated mesh is fitted inside it, never the
/// other way round. All fit math is static and pure so it is EditMode
/// testable without a headset.
/// </summary>
public static class ListingBox
{
    /// <summary>Mesh may differ from the listed box by this much per axis.</summary>
    public const float AspectTolerance = 0.25f;

    /// <summary>Wire extent order is (w, d, h); Unity wants (x, y, z) = (w, h, d).</summary>
    public static Vector3 ToBoxSize(Vector3 extentM)
    {
        return new Vector3(extentM.x, extentM.z, extentM.y);
    }

    /// <summary>
    /// One uniform factor that fits <paramref name="meshSize"/> inside
    /// <paramref name="boxSize"/>. Never enlarges; never stretches an axis,
    /// because a per-axis fit would make a wrong mesh look right.
    /// </summary>
    public static float FitScale(Vector3 meshSize, Vector3 boxSize)
    {
        if (meshSize.x <= 0f || meshSize.y <= 0f || meshSize.z <= 0f)
            return 1f;
        if (boxSize.x <= 0f || boxSize.y <= 0f || boxSize.z <= 0f)
            return 1f;
        float factor = Mathf.Min(
            boxSize.x / meshSize.x,
            Mathf.Min(boxSize.y / meshSize.y, boxSize.z / meshSize.z));
        return factor > 0f && factor < 1f ? factor : 1f;
    }

    /// <summary>True when the mesh's proportions miss the listing by too much.</summary>
    public static bool IsApproximate(Vector3 meshSize, Vector3 boxSize)
    {
        if (meshSize.x <= 0f || meshSize.y <= 0f || meshSize.z <= 0f)
            return false;
        if (boxSize.x <= 0f || boxSize.y <= 0f || boxSize.z <= 0f)
            return false;
        return WorstAxisDrift(meshSize, boxSize) > AspectTolerance;
    }

    static float WorstAxisDrift(Vector3 meshSize, Vector3 boxSize)
    {
        float worst = 0f;
        float[] mesh = { meshSize.x, meshSize.y, meshSize.z };
        float[] box = { boxSize.x, boxSize.y, boxSize.z };
        for (int i = 0; i < 3; i++)
        {
            float ratio = mesh[i] / box[i];
            float drift = Mathf.Abs(ratio - 1f);
            if (drift > worst)
                worst = drift;
        }
        return worst;
    }

    /// <summary>
    /// A translucent box at the stated size, resting on the surface: its
    /// centre is half its height above <paramref name="surfacePoint"/> so the
    /// base sits on the floor rather than the middle sinking into it.
    /// </summary>
    public static GameObject Create(
        Vector3 boxSize, Vector3 surfacePoint, Vector3 facing, string drawingId)
    {
        GameObject root = GameObject.CreatePrimitive(PrimitiveType.Cube);
        root.name = "Listing_" + drawingId;
        Collider collider = root.GetComponent<Collider>();
        if (collider != null)
        {
            // The box is a visual: Task 10 gives the root its own collider.
            // DestroyImmediate in EditMode, where Object.Destroy logs an
            // error that fails an EditMode test run.
            if (Application.isPlaying)
                Object.Destroy(collider);
            else
                Object.DestroyImmediate(collider);
        }
        root.transform.localScale = boxSize;
        Vector3 fwd = new Vector3(facing.x, 0f, facing.z);
        if (fwd.sqrMagnitude < 1e-6f)
            fwd = Vector3.forward;
        root.transform.rotation = Quaternion.LookRotation(fwd.normalized, Vector3.up);
        root.transform.position = surfacePoint + Vector3.up * (boxSize.y / 2f);
        Renderer rend = root.GetComponent<Renderer>();
        if (rend != null)
        {
            // Unlit/Transparent, not Unlit/Color: Unlit/Color is an opaque pass
            // whose fragment forces alpha to 1, so the alpha below would be
            // inert and the box would render solid. TrackingMaskOverlay already
            // uses Unlit/Transparent for the same reason.
            rend.material = new Material(Shader.Find("Unlit/Transparent"));
            Color c = PulsingRing.RingColor;
            c.a = 0.25f;
            rend.material.color = c;
        }
        return root;
    }
}
