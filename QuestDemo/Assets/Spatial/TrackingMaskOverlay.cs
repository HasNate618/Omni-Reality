using System;
using UnityEngine;

/// <summary>
/// Draws SAM 2 masks in the headset. Subscribes to
/// <see cref="CoordinatorClient.TrackingResultReceived"/> (the renderer the
/// tracking payload was waiting for) and paints each mask onto a quad placed
/// in the capture camera's frustum, using that frame's pose and intrinsics.
///
/// The quad is world-locked at <see cref="Distance"/> metres from where the
/// camera was when the frame was taken, so it sits still while the wearer
/// moves. It is a flat projection, not per-pixel depth: correct along the
/// capture ray, approximate off to the side.
///
/// Creates itself at startup; no scene wiring needed.
/// </summary>
public class TrackingMaskOverlay : MonoBehaviour
{
    /// <summary>Metres from the capture pose to the mask plane.</summary>
    public float Distance = 1.5f;
    public Color MaskColor = new Color(0.24f, 0.86f, 1f, 0.55f);
    /// <summary>Hide the mask if no result arrives for this long.</summary>
    public float StaleSeconds = 2f;

    const byte MaskThreshold = 127;

    SpatialRuntime _spatial;
    CoordinatorClient _subscribed;
    GameObject _quad;
    MeshFilter _filter;
    MeshRenderer _renderer;
    Material _material;
    Texture2D _decoded;   // raw PNG from the coordinator
    Texture2D _display;   // tinted RGBA shown on the quad
    Color32[] _pixels;
    int _litPixels;
    float _lastResult;
    bool _visible;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void AutoCreate()
    {
        if (FindAnyObjectByType<TrackingMaskOverlay>() == null)
            new GameObject("TrackingMaskOverlay").AddComponent<TrackingMaskOverlay>();
    }

    void Update()
    {
        // The client appears only after SpatialRuntime connects, so keep
        // looking until it exists, then subscribe exactly once.
        if (_spatial == null)
            _spatial = FindAnyObjectByType<SpatialRuntime>();
        CoordinatorClient client = _spatial != null ? _spatial.Coordinator : null;
        if (client != null && client != _subscribed)
        {
            Unsubscribe();
            client.TrackingResultReceived += OnTrackingResult;
            client.TrackingStatusReceived += OnTrackingStatus;
            _subscribed = client;
            Debug.Log("QUEST_OVERLAY subscribed to tracking results");
        }
        if (_visible && Time.realtimeSinceStartup - _lastResult > StaleSeconds)
        {
            Debug.Log("QUEST_OVERLAY no result for " + StaleSeconds + " s; hiding");
            Show(false);
        }
    }

    void OnDestroy()
    {
        Unsubscribe();
    }

    void Unsubscribe()
    {
        if (_subscribed == null)
            return;
        _subscribed.TrackingResultReceived -= OnTrackingResult;
        _subscribed.TrackingStatusReceived -= OnTrackingStatus;
        _subscribed = null;
    }

    void OnTrackingStatus(TrackingStatus status)
    {
        if (status == null)
            return;
        Debug.Log("QUEST_OVERLAY status " + status.state + ": " + status.text);
        if (status.state == "stopped" || status.state == "error")
            Show(false);
    }

    /// <summary>Main thread: CoordinatorClient pumps inbound messages in Tick.</summary>
    void OnTrackingResult(TrackingResult result)
    {
        try
        {
            if (result == null || result.objects == null || result.objects.Length == 0)
            {
                Debug.Log("QUEST_OVERLAY result with no objects; hiding");
                Show(false);
                return;
            }
            Debug.Log("QUEST_OVERLAY result frame=" + result.frame_id + " objects=" + result.objects.Length
                      + " size=" + result.width + "x" + result.height);
            var payload = JsonUtility.FromJson<MaskPayload>(result.RawPayloadJson);
            if (payload == null || payload.envelope == null || !payload.envelope.IsUsable)
            {
                Debug.LogWarning("QUEST_OVERLAY result without usable envelope; not drawing");
                Show(false);
                return;
            }
            if (!Paint(result))
                return;
            Place(payload.envelope);
            Show(true);
            _lastResult = Time.realtimeSinceStartup;
            Transform head = Camera.main != null ? Camera.main.transform : null;
            Debug.Log("QUEST_OVERLAY drawn: texture=" + (_display != null ? _display.width + "x" + _display.height : "none")
                      + " lit=" + _litPixels + "/" + (_pixels != null ? _pixels.Length : 0)
                      + " quad_at=" + _quad.transform.position.ToString("F2")
                      + " head_at=" + (head != null ? head.position.ToString("F2") : "?")
                      + " dist=" + (head != null ? Vector3.Distance(head.position, _quad.transform.position).ToString("F2") : "?")
                      + " active=" + _quad.activeSelf);
        }
        catch (Exception e)
        {
            Debug.LogWarning("QUEST_OVERLAY draw failed (" + e.GetType().Name + "): " + e.Message + "\n" + e.StackTrace);
        }
    }

    /// <summary>Decode every object's mask into one tinted RGBA texture.</summary>
    bool Paint(TrackingResult result)
    {
        EnsureQuad();
        bool any = false;
        for (int i = 0; i < result.objects.Length; i++)
        {
            TrackingObject obj = result.objects[i];
            if (obj == null || string.IsNullOrEmpty(obj.mask_b64))
                continue;
            byte[] png = Convert.FromBase64String(obj.mask_b64);
            if (_decoded == null)
                _decoded = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            if (!_decoded.LoadImage(png, false))
                continue;

            int w = _decoded.width;
            int h = _decoded.height;
            if (_display == null || _display.width != w || _display.height != h)
            {
                if (_display != null)
                    Destroy(_display);
                _display = new Texture2D(w, h, TextureFormat.RGBA32, false);
                _display.wrapMode = TextureWrapMode.Clamp;
                _pixels = new Color32[w * h];
                _material.mainTexture = _display;
            }
            Color32[] src = _decoded.GetPixels32();
            Color32 tint = MaskColor;
            if (!any)
            {
                Array.Clear(_pixels, 0, _pixels.Length);
                _litPixels = 0;
            }
            for (int p = 0; p < src.Length && p < _pixels.Length; p++)
            {
                if (src[p].r > MaskThreshold)
                {
                    _pixels[p] = tint;
                    _litPixels++;
                }
            }
            any = true;
        }
        if (!any)
        {
            Debug.Log("QUEST_OVERLAY no mask could be decoded; hiding");
            Show(false);
            return false;
        }
        if (_litPixels == 0)
        {
            // An all-empty mask is SAM 2 losing the object, not a draw bug.
            Debug.Log("QUEST_OVERLAY mask is empty (0 lit pixels); hiding");
            Show(false);
            return false;
        }
        _display.SetPixels32(_pixels);
        _display.Apply(false);
        return true;
    }

    /// <summary>
    /// Build the quad in the capture camera's frame: each image corner becomes
    /// a ray through the intrinsics, taken out to Distance metres.
    /// </summary>
    void Place(MaskEnvelope env)
    {
        float d = Mathf.Max(0.2f, Distance);
        Vector3 tl = Unproject(0, 0, env, d);
        Vector3 tr = Unproject(env.sent_w, 0, env, d);
        Vector3 bl = Unproject(0, env.sent_h, env, d);
        Vector3 br = Unproject(env.sent_w, env.sent_h, env, d);

        var mesh = _filter.mesh;
        mesh.Clear();
        mesh.vertices = new[] { tl, tr, bl, br };
        // Image row 0 is the top; LoadImage puts it at v = 1.
        mesh.uv = new[] { new Vector2(0, 1), new Vector2(1, 1), new Vector2(0, 0), new Vector2(1, 0) };
        // Both windings: visible from either side, no culling surprises.
        mesh.triangles = new[] { 0, 1, 2, 2, 1, 3, 2, 1, 0, 3, 1, 2 };
        mesh.RecalculateBounds();

        _quad.transform.SetPositionAndRotation(
            new Vector3(env.pose.px, env.pose.py, env.pose.pz),
            new Quaternion(env.pose.qx, env.pose.qy, env.pose.qz, env.pose.qw));
    }

    /// <summary>Pixel (top-left origin) to a point in camera space at distance d.</summary>
    static Vector3 Unproject(float x, float y, MaskEnvelope env, float d)
    {
        float dirX = (x - env.intrinsics.cx) / env.intrinsics.fx;
        float dirY = -(y - env.intrinsics.cy) / env.intrinsics.fy; // image y grows down
        return new Vector3(dirX * d, dirY * d, d);
    }

    void EnsureQuad()
    {
        if (_quad != null)
            return;
        _quad = new GameObject("TrackingMaskQuad");
        _quad.transform.SetParent(null);
        _filter = _quad.AddComponent<MeshFilter>();
        _filter.mesh = new Mesh { name = "TrackingMask" };
        _renderer = _quad.AddComponent<MeshRenderer>();
        _renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        _renderer.receiveShadows = false;
        _material = new Material(FindShader());
        _material.color = Color.white;   // tint lives in the texture
        _renderer.sharedMaterial = _material;
        _quad.SetActive(false);
        Debug.Log("QUEST_OVERLAY quad created, shader=" + _material.shader.name
                  + " supported=" + _material.shader.isSupported);
    }

    static Shader FindShader()
    {
        // Resources first: built-in shaders are stripped from player builds
        // unless listed in Graphics > Always Included Shaders, and a missing
        // shader renders as a plain untextured quad.
        Shader own = Resources.Load<Shader>("OmniTrackingMask");
        if (own != null)
            return own;
        Debug.LogWarning("QUEST_OVERLAY Resources/OmniTrackingMask missing; falling back");
        string[] candidates = { "Unlit/Transparent", "Sprites/Default", "Unlit/Texture" };
        for (int i = 0; i < candidates.Length; i++)
        {
            Shader shader = Shader.Find(candidates[i]);
            if (shader != null)
                return shader;
        }
        throw new InvalidOperationException("no transparent unlit shader available");
    }

    void Show(bool visible)
    {
        _visible = visible;
        if (_quad != null && _quad.activeSelf != visible)
            _quad.SetActive(visible);
    }

    // Spec-shaped slice of the tracking_result payload (snake_case for JsonUtility).
    [Serializable]
    class MaskPayload
    {
        public string frame_id;
        public MaskEnvelope envelope;
    }

    [Serializable]
    class MaskEnvelope
    {
        public int sent_w;
        public int sent_h;
        public MaskIntrinsics intrinsics;
        public MaskPose pose;

        public bool IsUsable
        {
            get
            {
                return sent_w > 0 && sent_h > 0 && intrinsics != null && pose != null
                       && intrinsics.fx > 0f && intrinsics.fy > 0f;
            }
        }
    }

    [Serializable]
    class MaskIntrinsics
    {
        public float fx;
        public float fy;
        public float cx;
        public float cy;
    }

    [Serializable]
    class MaskPose
    {
        public float px;
        public float py;
        public float pz;
        public float qx;
        public float qy;
        public float qz;
        public float qw;
    }
}
