using UnityEngine;

/// <summary>
/// World-locked pulsing surface ring: the Task 5 pin visual.
/// Unlit cyan (#3DDCFF) annulus lying in the local XZ plane (normal +Y),
/// looping scale and material alpha with period <see cref="DefaultPeriodS"/>
/// (1.2 s). Never parented to a camera: <see cref="CreateRing"/> returns a
/// root object and <see cref="Awake"/> warns if a camera ancestor is found.
/// Diameter defaults to 0.06 m and clamps to [0.03 m, 0.25 m].
/// </summary>
[RequireComponent(typeof(MeshFilter), typeof(MeshRenderer))]
public class PulsingRing : MonoBehaviour
{
    public const float DefaultPeriodS = 1.2f;
    public const float DefaultDiameterM = 0.06f;
    public const float MinDiameterM = 0.03f;
    public const float MaxDiameterM = 0.25f;
    public const float PulseAmplitude = 0.15f;

    /// <summary>Unlit cyan #3DDCFF.</summary>
    public static readonly Color RingColor = new Color(0x3D / 255f, 0xDC / 255f, 1f, 1f);

    /// <summary>Pulse loop period in seconds. Reset to 1.2 s when unset.</summary>
    public float periodS = DefaultPeriodS;

    /// <summary>Ring outer diameter in metres, clamped to [0.03, 0.25].</summary>
    public float baseDiameterM = DefaultDiameterM;

    Material _material;
    Vector3 _baseScale = Vector3.one;

    void Awake()
    {
        // Brief step 1 fallback: when EditMode is unavailable this reset is
        // the executable form of the pulse-default test (verify visually).
        if (periodS <= 0f)
            periodS = DefaultPeriodS;
        baseDiameterM = ClampDiameter(baseDiameterM);
        EnsureMaterial();
        _baseScale = Vector3.one * baseDiameterM;
        transform.localScale = _baseScale;
        if (IsUnderCamera(transform))
            Debug.LogWarning("PulsingRing: ring parented under a camera; pins must be world-locked.");
    }

    void Update()
    {
        if (periodS <= 0f)
            return;
        float phase = (Time.time % periodS) / periodS;
        transform.localScale = _baseScale * PulseScaleFactor(phase);
        if (_material != null)
        {
            Color c = _material.color;
            c.a = PulseAlpha(phase);
            _material.color = c;
        }
    }

    /// <summary>Clamp a diameter to the [0.03 m, 0.25 m] pin range.</summary>
    public static float ClampDiameter(float diameterM)
    {
        return Mathf.Clamp(diameterM, MinDiameterM, MaxDiameterM);
    }

    /// <summary>Scale multiplier for a loop phase in [0, 1).</summary>
    public static float PulseScaleFactor(float phase01)
    {
        return 1f + PulseAmplitude * Mathf.Sin(phase01 * Mathf.PI * 2f);
    }

    /// <summary>Material alpha for a loop phase in [0, 1): 0.55..1.0.</summary>
    public static float PulseAlpha(float phase01)
    {
        return 0.775f + 0.225f * Mathf.Sin(phase01 * Mathf.PI * 2f);
    }

    /// <summary>Set the ring diameter (clamped); keeps the pulse base scale.</summary>
    public void SetDiameter(float diameterM)
    {
        baseDiameterM = ClampDiameter(diameterM);
        _baseScale = Vector3.one * baseDiameterM;
        transform.localScale = _baseScale;
    }

    /// <summary>
    /// Build a world-locked ring: root object with an annulus mesh, never a
    /// camera child. The caller positions/orients it (see DrawingStore).
    /// </summary>
    public static GameObject CreateRing(float diameterM)
    {
        var go = new GameObject("PulseRing");
        // World-locked by construction: explicit root, never the camera.
        go.transform.SetParent(null, false);
        var filter = go.AddComponent<MeshFilter>();
        filter.sharedMesh = BuildRingMesh(0.5f, 0.38f, 64);
        go.AddComponent<MeshRenderer>();
        var ring = go.AddComponent<PulsingRing>();
        ring.SetDiameter(diameterM);
        return go;
    }

    /// <summary>Flat annulus in the XZ plane (normal +Y), unit outer radius.</summary>
    public static Mesh BuildRingMesh(float outerRadius, float innerRadius, int segments)
    {
        if (segments < 3)
            segments = 3;
        if (innerRadius <= 0f)
            innerRadius = outerRadius * 0.5f;
        if (innerRadius >= outerRadius)
            innerRadius = outerRadius * 0.75f;
        var mesh = new Mesh();
        mesh.name = "PulseRing";
        var verts = new Vector3[(segments + 1) * 2];
        var normals = new Vector3[verts.Length];
        var uvs = new Vector2[verts.Length];
        var tris = new int[segments * 6];
        for (int i = 0; i <= segments; i++)
        {
            float a = (i % segments) / (float)segments * Mathf.PI * 2f;
            float c = Mathf.Cos(a);
            float s = Mathf.Sin(a);
            verts[i * 2] = new Vector3(c * outerRadius, 0f, s * outerRadius);
            verts[i * 2 + 1] = new Vector3(c * innerRadius, 0f, s * innerRadius);
            normals[i * 2] = Vector3.up;
            normals[i * 2 + 1] = Vector3.up;
            uvs[i * 2] = new Vector2(i / (float)segments, 1f);
            uvs[i * 2 + 1] = new Vector2(i / (float)segments, 0f);
        }
        for (int i = 0; i < segments; i++)
        {
            int b = i * 2;
            tris[i * 6] = b;
            tris[i * 6 + 1] = b + 2;
            tris[i * 6 + 2] = b + 1;
            tris[i * 6 + 3] = b + 1;
            tris[i * 6 + 4] = b + 2;
            tris[i * 6 + 5] = b + 3;
        }
        mesh.vertices = verts;
        mesh.normals = normals;
        mesh.uv = uvs;
        mesh.triangles = tris;
        return mesh;
    }

    void EnsureMaterial()
    {
        var renderer = GetComponent<MeshRenderer>();
        if (renderer == null)
            return;
        Shader shader = Shader.Find("Unlit/Color");
        if (shader == null)
        {
            Debug.LogWarning("PulsingRing: Unlit/Color shader missing, ring hidden");
            renderer.enabled = false;
            return;
        }
        _material = new Material(shader);
        _material.color = RingColor;
        // Transparent pulse: alpha animates every frame. Cull off so the
        // ring stays visible from either side regardless of triangle winding.
        _material.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
        _material.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
        _material.SetInt("_ZWrite", 0);
        _material.SetInt("_Cull", (int)UnityEngine.Rendering.CullMode.Off);
        _material.DisableKeyword("_ALPHATEST_ON");
        _material.EnableKeyword("_ALPHABLEND_ON");
        _material.DisableKeyword("_ALPHAPREMULTIPLY_ON");
        _material.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
        renderer.material = _material;
        renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        renderer.receiveShadows = false;
    }

    static bool IsUnderCamera(Transform t)
    {
        for (Transform p = t; p != null; p = p.parent)
        {
            if (p.GetComponent<Camera>() != null)
                return true;
        }
        return false;
    }
}
