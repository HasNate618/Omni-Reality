using System;
using System.Collections.Generic;
using Meta.XR;
using UnityEngine;

/// <summary>
/// Draws SAM 2 masks in the headset. Subscribes to
/// <see cref="CoordinatorClient.TrackingResultReceived"/> (the renderer the
/// tracking payload was waiting for) and paints each mask onto a quad placed
/// in the capture camera's frustum, using that frame's pose and intrinsics.
///
/// One tracked object gets one quad, so each mask is tinted its own colour and
/// sits at its own measured depth: a laptop at 0.8 m and a poster at 3 m both
/// land on their object, which a single shared plane cannot do.
///
/// Each quad is world-locked at its object's distance from where the camera was
/// when the frame was taken, so it sits still while the wearer moves. It is a
/// flat projection, not per-pixel depth: correct along the capture ray,
/// approximate off to the side.
///
/// Creates itself at startup; no scene wiring needed.
/// </summary>
public class TrackingMaskOverlay : MonoBehaviour
{
    /// <summary>Fallback metres from the capture pose to a mask plane.</summary>
    public float Distance = 1.5f;

    /// <summary>
    /// Mask tints by object, indexed <c>obj_id % Palette.Length</c>. Same order
    /// as COLORS in sam2/sam2_ws_client.py, so the webcam demo and the headset
    /// give the same object the same colour.
    /// </summary>
    public Color[] Palette =
    {
        new Color(0.13f, 1f, 0.13f, 0.55f),   // green
        new Color(0.24f, 0.51f, 1f, 0.55f),   // blue
        new Color(1f, 0.24f, 0.24f, 0.55f),   // red
        new Color(1f, 0.93f, 0.16f, 0.55f),   // yellow
        new Color(1f, 0.31f, 1f, 0.55f),      // magenta
    };

    /// <summary>Float the model's name for each object over its mask.</summary>
    public bool ShowLabels = true;

    /// <summary>Hide the masks if no result arrives for this long.</summary>
    public float StaleSeconds = 2f;

    const byte MaskThreshold = 127;

    SpatialRuntime _spatial;
    CoordinatorClient _subscribed;
    Texture2D _decoded;   // shared scratch for the raw PNG from the coordinator
    readonly Dictionary<int, MaskLayer> _layers = new Dictionary<int, MaskLayer>();
    readonly HashSet<int> _seen = new HashSet<int>();
    EnvironmentRaycastManager _raycast;
    Shader _shader;
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
            HideAll();
        }
    }

    void OnDestroy()
    {
        Unsubscribe();
        foreach (MaskLayer layer in _layers.Values)
            layer.Dispose();
        _layers.Clear();
        if (_decoded != null)
            Destroy(_decoded);
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
            HideAll();
    }

    /// <summary>Main thread: CoordinatorClient pumps inbound messages in Tick.</summary>
    void OnTrackingResult(TrackingResult result)
    {
        try
        {
            if (result == null || result.objects == null || result.objects.Length == 0)
            {
                Debug.Log("QUEST_OVERLAY result with no objects; hiding");
                HideAll();
                return;
            }
            Debug.Log("QUEST_OVERLAY result frame=" + result.frame_id + " objects=" + result.objects.Length
                      + " size=" + result.width + "x" + result.height);
            var payload = JsonUtility.FromJson<MaskPayload>(result.RawPayloadJson);
            if (payload == null || payload.envelope == null || !payload.envelope.IsUsable)
            {
                Debug.LogWarning("QUEST_OVERLAY result without usable envelope; not drawing");
                HideAll();
                return;
            }

            _seen.Clear();
            for (int i = 0; i < result.objects.Length; i++)
            {
                TrackingObject obj = result.objects[i];
                if (obj == null || string.IsNullOrEmpty(obj.mask_b64))
                    continue;
                MaskLayer layer = LayerFor(obj.obj_id);
                if (!Paint(layer, obj))
                {
                    layer.Show(false);
                    continue;
                }
                Place(layer, payload.envelope, obj.label);
                layer.Show(true);
                _seen.Add(obj.obj_id);
            }
            // An object that stopped coming back is gone, not frozen in place.
            foreach (KeyValuePair<int, MaskLayer> entry in _layers)
                if (!_seen.Contains(entry.Key))
                    entry.Value.Show(false);

            if (_seen.Count == 0)
            {
                // All-empty masks are SAM 2 losing the objects, not a draw bug.
                Debug.Log("QUEST_OVERLAY no mask could be drawn; hiding");
                _visible = false;
                return;
            }
            _visible = true;
            _lastResult = Time.realtimeSinceStartup;
            LogDrawn(payload.envelope);
        }
        catch (Exception e)
        {
            Debug.LogWarning("QUEST_OVERLAY draw failed (" + e.GetType().Name + "): " + e.Message + "\n" + e.StackTrace);
        }
    }

    void LogDrawn(MaskEnvelope e)
    {
        Transform head = Camera.main != null ? Camera.main.transform : null;
        Debug.Log("QUEST_OVERLAY geometry: sent=" + e.sent_w + "x" + e.sent_h
                  + " image=" + e.image_w + "x" + e.image_h
                  + " crop=" + (e.crop != null ? e.crop.sx + "," + e.crop.sy + "," + e.crop.tx + "," + e.crop.ty : "none")
                  + " fx=" + e.intrinsics.fx.ToString("F1") + " cx=" + e.intrinsics.cx.ToString("F1")
                  + " cy=" + e.intrinsics.cy.ToString("F1"));
        foreach (int objId in _seen)
        {
            MaskLayer layer = _layers[objId];
            Debug.Log("QUEST_OVERLAY drawn obj=" + objId
                      + " label=" + (string.IsNullOrEmpty(layer.LabelText) ? "none" : layer.LabelText)
                      + " lit=" + layer.LitPixels + "/" + (layer.Pixels != null ? layer.Pixels.Length : 0)
                      + " centre=" + layer.CentreU.ToString("F0") + "," + layer.CentreV.ToString("F0")
                      + " depth=" + layer.Depth.ToString("F2") + "m from " + layer.DepthSource
                      + " quad_at=" + layer.Quad.transform.position.ToString("F2")
                      + " dist=" + (head != null
                          ? Vector3.Distance(head.position, layer.Quad.transform.position).ToString("F2") : "?"));
        }
    }

    MaskLayer LayerFor(int objId)
    {
        MaskLayer layer;
        if (_layers.TryGetValue(objId, out layer))
            return layer;
        layer = new MaskLayer(objId, ColorFor(objId), ShaderForMasks(), ShowLabels);
        _layers[objId] = layer;
        return layer;
    }

    Color ColorFor(int objId)
    {
        if (Palette == null || Palette.Length == 0)
            return new Color(0.24f, 0.86f, 1f, 0.55f);
        // Mirrors COLORS[obj_id % len(COLORS)] in sam2/sam2_ws_client.py.
        int index = objId % Palette.Length;
        if (index < 0)
            index += Palette.Length;
        return Palette[index];
    }

    /// <summary>Decode one object's mask PNG into its own tinted RGBA texture.</summary>
    bool Paint(MaskLayer layer, TrackingObject obj)
    {
        byte[] png = Convert.FromBase64String(obj.mask_b64);
        if (_decoded == null)
            _decoded = new Texture2D(2, 2, TextureFormat.RGBA32, false);
        if (!_decoded.LoadImage(png, false))
            return false;

        layer.Resize(_decoded.width, _decoded.height);
        Color32[] src = _decoded.GetPixels32();
        Color32[] pixels = layer.Pixels;
        Color32 tint = layer.Tint;
        Array.Clear(pixels, 0, pixels.Length);
        int lit = 0;
        double sumX = 0, sumY = 0;
        int w = _decoded.width;
        int count = Math.Min(src.Length, pixels.Length);
        for (int p = 0; p < count; p++)
        {
            if (src[p].r <= MaskThreshold)
                continue;
            pixels[p] = tint;
            lit++;
            sumX += p % w;
            sumY += p / w;
        }
        layer.LitPixels = lit;
        if (lit == 0)
        {
            layer.CentreU = layer.CentreV = -1f;
            return false;
        }
        layer.CentreU = (float)(sumX / lit);
        layer.CentreV = (float)(sumY / lit);
        layer.Display.SetPixels32(pixels);
        layer.Display.Apply(false);
        return true;
    }

    /// <summary>
    /// Build the quad in the capture camera's frame: each image corner becomes
    /// a ray through the intrinsics, taken out to this object's depth.
    /// </summary>
    void Place(MaskLayer layer, MaskEnvelope env, string label)
    {
        float d = layer.Depth = DepthFor(layer, env);   // one raycast per object
        Vector3 tl = Unproject(0, 0, env, d);
        Vector3 tr = Unproject(env.sent_w, 0, env, d);
        Vector3 bl = Unproject(0, env.sent_h, env, d);
        Vector3 br = Unproject(env.sent_w, env.sent_h, env, d);

        var mesh = layer.Filter.mesh;
        mesh.Clear();
        mesh.vertices = new[] { tl, tr, bl, br };
        // Image row 0 is the top; LoadImage puts it at v = 1.
        mesh.uv = new[] { new Vector2(0, 1), new Vector2(1, 1), new Vector2(0, 0), new Vector2(1, 0) };
        // Both windings: visible from either side, no culling surprises.
        mesh.triangles = new[] { 0, 1, 2, 2, 1, 3, 2, 1, 0, 3, 1, 2 };
        mesh.RecalculateBounds();

        layer.Quad.transform.SetPositionAndRotation(
            new Vector3(env.pose.px, env.pose.py, env.pose.pz),
            new Quaternion(env.pose.qx, env.pose.qy, env.pose.qz, env.pose.qw));
        // Slightly in front of the mask plane so the text never z-fights it.
        layer.SetLabel(label, Unproject(layer.CentreU, layer.CentreV, env, d) * 0.97f);
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
    /// Distance to this object's mask plane. The capture-time depth hit is what
    /// the wearer was actually looking at, so the plane sits on the object
    /// instead of a guessed 1.5 m, which is what makes a flat overlay line up.
    /// </summary>
    float DepthFor(MaskLayer layer, MaskEnvelope env)
    {
        layer.DepthSource = "default";
        if (env.world_hint != null && env.world_hint.IsSet && env.pose != null)
        {
            var hit = new Vector3(env.world_hint.px, env.world_hint.py, env.world_hint.pz);
            var eye = new Vector3(env.pose.px, env.pose.py, env.pose.pz);
            float measured = Vector3.Distance(hit, eye);
            if (measured > 0.25f && measured < 5f)
            {
                layer.DepthSource = "world_hint";
                return measured;
            }
        }
        float scanned = DepthAtMaskCentre(layer, env);
        if (scanned > 0f)
        {
            layer.DepthSource = "depth raycast";
            return scanned;
        }
        return Mathf.Max(0.2f, Distance);
    }

    /// <summary>
    /// Distance to whatever this mask's centre points at, via the environment
    /// depth raycast. Tracking snapshots carry no world_hint (no surface hit is
    /// required to segment), so without this the plane sits at a guessed
    /// distance and the overlay slides off the object as the wearer moves.
    /// </summary>
    float DepthAtMaskCentre(MaskLayer layer, MaskEnvelope env)
    {
        if (layer.CentreU < 0f)
            return 0f;
        if (_raycast == null)
            _raycast = FindAnyObjectByType<EnvironmentRaycastManager>();
        if (_raycast == null)
            return 0f;
        var origin = new Vector3(env.pose.px, env.pose.py, env.pose.pz);
        var rotation = new Quaternion(env.pose.qx, env.pose.qy, env.pose.qz, env.pose.qw);
        Vector3 local = Unproject(layer.CentreU, layer.CentreV, env, 1f);
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

    void HideAll()
    {
        _visible = false;
        _seen.Clear();
        foreach (MaskLayer layer in _layers.Values)
            layer.Show(false);
    }

    Shader ShaderForMasks()
    {
        if (_shader == null)
            _shader = FindShader();
        return _shader;
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

    /// <summary>One tracked object: its own quad, tinted texture, and depth.</summary>
    class MaskLayer
    {
        public readonly Color32 Tint;
        public readonly GameObject Quad;
        public readonly MeshFilter Filter;
        public Texture2D Display;
        public Color32[] Pixels;
        public int LitPixels;
        public float CentreU = -1f;
        public float CentreV = -1f;
        public float Depth;
        public string DepthSource = "default";
        public string LabelText;

        readonly Material _material;
        readonly bool _wantsLabel;
        TextMesh _label;

        public MaskLayer(int objId, Color tint, Shader shader, bool wantsLabel)
        {
            Tint = tint;
            _wantsLabel = wantsLabel;
            Quad = new GameObject("TrackingMaskQuad" + objId);
            Quad.transform.SetParent(null);
            Filter = Quad.AddComponent<MeshFilter>();
            Filter.mesh = new Mesh { name = "TrackingMask" + objId };
            var renderer = Quad.AddComponent<MeshRenderer>();
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            renderer.receiveShadows = false;
            _material = new Material(shader);
            _material.color = Color.white;   // tint lives in the texture
            renderer.sharedMaterial = _material;
            Quad.SetActive(false);
            Debug.Log("QUEST_OVERLAY quad created for obj=" + objId + ", shader=" + shader.name
                      + " supported=" + shader.isSupported);
        }

        public void Resize(int w, int h)
        {
            if (Display != null && Display.width == w && Display.height == h)
                return;
            if (Display != null)
                UnityEngine.Object.Destroy(Display);
            Display = new Texture2D(w, h, TextureFormat.RGBA32, false);
            Display.wrapMode = TextureWrapMode.Clamp;
            Pixels = new Color32[w * h];
            _material.mainTexture = Display;
        }

        /// <summary>Float the object's name at a point in the quad's local space.</summary>
        public void SetLabel(string text, Vector3 localPosition)
        {
            LabelText = text;
            if (!_wantsLabel || string.IsNullOrEmpty(text))
            {
                if (_label != null)
                    _label.gameObject.SetActive(false);
                return;
            }
            if (_label == null)
            {
                var go = new GameObject("TrackingMaskLabel");
                go.transform.SetParent(Quad.transform, false);
                _label = go.AddComponent<TextMesh>();
                _label.anchor = TextAnchor.MiddleCenter;
                _label.alignment = TextAlignment.Center;
                _label.fontSize = 48;
                _label.characterSize = 0.004f;
                _label.color = new Color(Tint.r / 255f, Tint.g / 255f, Tint.b / 255f, 1f);
            }
            _label.gameObject.SetActive(true);
            _label.text = text;
            _label.transform.localPosition = localPosition;
            _label.transform.localRotation = Quaternion.identity;
        }

        public void Show(bool visible)
        {
            if (Quad != null && Quad.activeSelf != visible)
                Quad.SetActive(visible);
        }

        public void Dispose()
        {
            if (Display != null)
                UnityEngine.Object.Destroy(Display);
            if (_material != null)
                UnityEngine.Object.Destroy(_material);
            if (Quad != null)
                UnityEngine.Object.Destroy(Quad);
        }
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
