using System;
using Meta.XR;
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
    float _centreU = -1f;
    float _centreV = -1f;
    string _depthSource = "default";
    float _lastDepth;
    EnvironmentRaycastManager _raycast;
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
            MaskEnvelope e = payload.envelope;
            Debug.Log("QUEST_OVERLAY geometry: sent=" + e.sent_w + "x" + e.sent_h
                      + " image=" + e.image_w + "x" + e.image_h
                      + " crop=" + (e.crop != null ? e.crop.sx + "," + e.crop.sy + "," + e.crop.tx + "," + e.crop.ty : "none")
                      + " fx=" + e.intrinsics.fx.ToString("F1") + " cx=" + e.intrinsics.cx.ToString("F1")
                      + " cy=" + e.intrinsics.cy.ToString("F1")
                      + " depth=" + _lastDepth.ToString("F2") + "m from " + _depthSource
                      + " mask_centre=" + _centreU.ToString("F0") + "," + _centreV.ToString("F0"));
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
        double sumX = 0, sumY = 0;
        _centreU = _centreV = -1f;
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
                    sumX += p % w;
                    sumY += p / w;
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
        _centreU = (float)(sumX / _litPixels);
        _centreV = (float)(sumY / _litPixels);
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
        float d = _lastDepth = DepthFor(env);   // one raycast per result
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

    /// <summary>
    /// Sent-image pixel (top-left origin) to a point in camera space at distance d.
    /// The intrinsics describe the FULL camera image, while masks arrive at the
    /// downscaled size, so map through the envelope's crop first.
    /// </summary>
    static Vector3 Unproject(float x, float y, MaskEnvelope env, float d)
    {
        Vector2 full = env.ToImagePixels(x, y);
        float dirX = (full.x - env.intrinsics.cx) / env.intrinsics.fx;
        float dirY = -(full.y - env.intrinsics.cy) / env.intrinsics.fy; // image y grows down
        return new Vector3(dirX * d, dirY * d, d);
    }

    /// <summary>
    /// Distance to the mask plane. The capture-time depth hit is what the
    /// wearer was actually looking at, so the plane sits on the object instead
    /// of a guessed 1.5 m, which is what makes a flat overlay line up.
    /// </summary>
    float DepthFor(MaskEnvelope env)
    {
        _depthSource = "default";
        if (env.world_hint != null && env.world_hint.IsSet && env.pose != null)
        {
            var hit = new Vector3(env.world_hint.px, env.world_hint.py, env.world_hint.pz);
            var eye = new Vector3(env.pose.px, env.pose.py, env.pose.pz);
            float measured = Vector3.Distance(hit, eye);
            if (measured > 0.25f && measured < 5f)
            {
                _depthSource = "world_hint";
                return measured;
            }
        }
        float scanned = DepthAtMaskCentre(env);
        if (scanned > 0f)
        {
            _depthSource = "depth raycast";
            return scanned;
        }
        return Mathf.Max(0.2f, Distance);
    }

    /// <summary>
    /// Distance to whatever the mask's centre points at, via the environment
    /// depth raycast. Tracking snapshots carry no world_hint (no surface hit is
    /// required to segment), so without this the plane sits at a guessed
    /// distance and the overlay slides off the object as the wearer moves.
    /// </summary>
    float DepthAtMaskCentre(MaskEnvelope env)
    {
        if (_centreU < 0f)
            return 0f;
        if (_raycast == null)
            _raycast = FindAnyObjectByType<EnvironmentRaycastManager>();
        if (_raycast == null)
            return 0f;
        var origin = new Vector3(env.pose.px, env.pose.py, env.pose.pz);
        var rotation = new Quaternion(env.pose.qx, env.pose.qy, env.pose.qz, env.pose.qw);
        Vector3 local = Unproject(_centreU, _centreV, env, 1f);
        var ray = new Ray(origin, rotation * local.normalized);
        EnvironmentRaycastHit hit;
        if (_raycast.Raycast(ray, out hit, 5f) && hit.status == EnvironmentRaycastHitStatus.Hit)
        {
            float d = Vector3.Distance(hit.point, origin);
            if (d > 0.25f && d < 5f)
                return d;
        }
        return 0f;
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
        public int image_w;
        public int image_h;
        public MaskIntrinsics intrinsics;
        public MaskPose pose;
        public MaskCrop crop;
        public MaskWorldHint world_hint;

        /// <summary>Sent-image pixel -> full-image pixel, where the intrinsics live.</summary>
        public Vector2 ToImagePixels(float x, float y)
        {
            if (crop != null && crop.sx != 0f && crop.sy != 0f)
                return new Vector2(crop.sx * x + crop.tx, crop.sy * y + crop.ty);
            // No crop block: fall back to the plain resize ratio.
            float sx = sent_w > 0 && image_w > 0 ? image_w / (float)sent_w : 1f;
            float sy = sent_h > 0 && image_h > 0 ? image_h / (float)sent_h : 1f;
            return new Vector2(sx * x + (sx - 1f) * .5f, sy * y + (sy - 1f) * .5f);
        }

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
    class MaskCrop
    {
        public float sx = 1f;
        public float sy = 1f;
        public float tx;
        public float ty;
    }

    [Serializable]
    class MaskWorldHint
    {
        public float px;
        public float py;
        public float pz;
        // JsonUtility materialises a null object as zeros, so a present hit is
        // only distinguishable by its non-empty frame name.
        public string frame;

        public bool IsSet { get { return !string.IsNullOrEmpty(frame); } }
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
