using System;
using Meta.XR;
using UnityEngine;
using UnityEngine.Rendering;

/// <summary>
/// Slice 1 capture runtime on the Quest. Owns the left-camera capture envelope:
/// capture-time <see cref="PassthroughCameraAccess.GetCameraPose"/>, a capture
/// ray (the sampled right-controller ray while its trigger is held, else the
/// capture-time image-centre ray), and a same-ray
/// <see cref="EnvironmentRaycastManager"/> depth hit that fills
/// <c>world_hint</c>. Capture range is [0.25 m, 4 m]; misses are honest
/// (<c>world_hint</c> null, unchanged honesty) with no pin.
/// Coherence rule: no-trigger captures serialize <c>pointing: null</c> and use
/// the capture-time centre ray; trigger captures serialize exactly the sampled
/// controller ray stamped with its actual sample time (never the PCA image
/// time), and the hint plus cache entry derive from that same ray. Normals are
/// only emitted when the SDK supplies one (non-zero); otherwise the hint is
/// null while the internal entry preserves the hit point. Every valid capture
/// — hit, too-close, or miss — is cached for 10 s with identity crop.
/// No networking, no placement: one ENVELOPE log line per trigger press plus a
/// thin visible aim line for the slice 1 headset check.
/// </summary>
public class SpatialRuntime : MonoBehaviour
{
    public const float MaxCaptureDistanceM = 4f;
    public const float MinCaptureDistanceM = 0.25f;
    public const float AimLineWidthM = 0.005f;

    static readonly Vector2 CentreViewport = new Vector2(0.5f, 0.5f);
    const string Crockford = "0123456789abcdefghjkmnpqrstvwxyz";

    static readonly DateTime UnixEpoch =
#if NETSTANDARD2_1_OR_GREATER || UNITY_2021_2_OR_NEWER
        DateTime.UnixEpoch;
#else
        new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
#endif

    PassthroughCameraAccess _pca;
    EnvironmentRaycastManager _raycast;
    Transform _centerEye;
    Transform _rightAim;
    LineRenderer _aimLine;
    Material _aimMaterial;
    readonly CaptureGeometryCache _cache = new CaptureGeometryCache();
    int _stageEpoch = 1;

    void Awake()
    {
        _pca = GetComponent<PassthroughCameraAccess>();
        if (_pca == null)
            _pca = FindAnyObjectByType<PassthroughCameraAccess>();
        _raycast = GetComponentInChildren<EnvironmentRaycastManager>();
        if (_raycast == null)
            _raycast = FindAnyObjectByType<EnvironmentRaycastManager>();
        var centerGO = GameObject.Find("CenterEyeAnchor");
        if (centerGO != null)
            _centerEye = centerGO.transform;
        var rightGO = GameObject.Find("RightHandAnchor");
        if (rightGO != null)
            _rightAim = rightGO.transform;
        SetupAimLine();
    }

    void OnEnable()
    {
        OVRManager.TrackingOriginChangePending += OnTrackingOriginChangePending;
    }

    void OnDisable()
    {
        OVRManager.TrackingOriginChangePending -= OnTrackingOriginChangePending;
    }

    void OnTrackingOriginChangePending(OVRManager.TrackingOrigin origin, OVRPose? pose)
    {
        _stageEpoch++;
    }

    void Update()
    {
        if (_pca == null || !_pca.IsPlaying)
            return;
        UpdateAimLine();
        if (OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
        {
            CaptureEnvelope env;
            Pose cameraPose;
            Ray ray;
            if (TryCapture(out env, out cameraPose, out ray))
                Debug.Log("ENVELOPE " + env.ToSpecJson());
            else
                Debug.Log("SpatialRuntime: capture skipped (camera or raycast unavailable)");
        }
    }

    /// <summary>
    /// Builds one capture envelope from capture-time state. Returns false when
    /// no honest capture is possible (camera not playing, raycast missing, or
    /// degenerate intrinsics); otherwise returns true with
    /// <paramref name="env"/> filled, misses recorded as null
    /// <c>world_hint</c>, and the capture-time <paramref name="cameraPose"/>
    /// and <paramref name="ray"/> used. The capture is always cached.
    /// </summary>
    public bool TryCapture(out CaptureEnvelope env, out Pose cameraPose, out Ray ray)
    {
        env = null;
        cameraPose = default(Pose);
        ray = default(Ray);
        if (_pca == null || !_pca.IsPlaying || _raycast == null)
            return false;

        // Capture-time state only: pose and timestamp belong to this frame.
        cameraPose = _pca.GetCameraPose();
        long tUnixNs = ToUnixNanoseconds(_pca.Timestamp);

        bool triggerHeld = false;
        try
        {
            triggerHeld = OVRInput.Get(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch);
        }
        catch (Exception)
        {
            triggerHeld = false;
        }

        Vector3 origin;
        Vector3 direction;
        CapturePointing pointing = null;
        string hintSource;
        if (triggerHeld && _rightAim != null)
        {
            // Exactly the sampled controller ray, stamped with its actual
            // sample time — never the PCA image time.
            origin = _rightAim.position;
            direction = _rightAim.forward;
            pointing = new CapturePointing
            {
                Source = "controller",
                TUnixNs = ToUnixNanoseconds(DateTime.UtcNow),
                OriginPx = origin.x,
                OriginPy = origin.y,
                OriginPz = origin.z,
                DirX = direction.x,
                DirY = direction.y,
                DirZ = direction.z,
            };
            hintSource = "pointing";
        }
        else
        {
            // Capture-time centre ray; no pointing is serialized.
            Ray centre = _pca.ViewportPointToRay(CentreViewport, cameraPose);
            origin = centre.origin;
            direction = centre.direction;
            hintSource = "centre";
        }
        ray = new Ray(origin, direction);

        Vector2Int resolution = _pca.CurrentResolution;
        var intrinsics = _pca.Intrinsics;
        Vector2Int sensor = intrinsics.SensorResolution;
        float fx;
        float fy;
        float cx;
        float cy;
        if (!PcaIntrinsics.TryToOutputPixels(
            intrinsics.FocalLength.x, intrinsics.FocalLength.y,
            intrinsics.PrincipalPoint.x, intrinsics.PrincipalPoint.y,
            sensor.x, sensor.y, resolution.x, resolution.y,
            out fx, out fy, out cx, out cy))
            return false;
        string frameId = NewFrameId();

        string honesty = "no_surface";
        CaptureWorldHint hint = null;
        bool hasHit = false;
        bool hasNormal = false;
        Vector3 hitPoint = default(Vector3);
        Vector3 hitNormal = default(Vector3);
        EnvironmentRaycastHit hit;
        if (_raycast.Raycast(ray, out hit, MaxCaptureDistanceM)
            && hit.status == EnvironmentRaycastHitStatus.Hit)
        {
            float distance = Vector3.Distance(origin, hit.point);
            if (distance < MinCaptureDistanceM)
            {
                honesty = "too_close";
            }
            else
            {
                honesty = "placed";
                hasHit = true;
                hitPoint = hit.point;
                // Never fabricate a normal: only a non-zero SDK normal counts.
                if (hit.normal.sqrMagnitude > 1e-6f)
                {
                    hasNormal = true;
                    hitNormal = hit.normal.normalized;
                    hint = new CaptureWorldHint
                    {
                        Px = hit.point.x,
                        Py = hit.point.y,
                        Pz = hit.point.z,
                        Nx = hitNormal.x,
                        Ny = hitNormal.y,
                        Nz = hitNormal.z,
                        Source = hintSource,
                        TUnixNs = tUnixNs,
                    };
                }
            }
        }

        // Every valid capture is cached, including misses and too-close
        // frames; availability is cache existence, not hit success.
        _cache.Store(frameId, new CaptureGeometryCache.Entry
        {
            CameraPose = cameraPose,
            FocalLength = new Vector2(fx, fy),
            PrincipalPoint = new Vector2(cx, cy),
            ImageResolution = resolution,
            CropSx = 1f,
            CropSy = 1f,
            CropTx = 0f,
            CropTy = 0f,
            RayOrigin = origin,
            RayDirection = direction,
            HasHit = hasHit,
            HitPoint = hitPoint,
            HasNormal = hasNormal,
            HitNormal = hitNormal,
            TUnixNs = tUnixNs,
        });

        env = new CaptureEnvelope
        {
            FrameId = frameId,
            StageEpoch = _stageEpoch,
            TUnixNs = tUnixNs,
            Camera = ProtocolJson.CameraLeft,
            ImageW = resolution.x,
            ImageH = resolution.y,
            SentW = resolution.x,
            SentH = resolution.y,
            Fx = fx,
            Fy = fy,
            Cx = cx,
            Cy = cy,
            PosePx = cameraPose.position.x,
            PosePy = cameraPose.position.y,
            PosePz = cameraPose.position.z,
            PoseQx = cameraPose.rotation.x,
            PoseQy = cameraPose.rotation.y,
            PoseQz = cameraPose.rotation.z,
            PoseQw = cameraPose.rotation.w,
            CropSx = 1f,
            CropSy = 1f,
            CropTx = 0f,
            CropTy = 0f,
            Pointing = pointing,
            WorldHint = hint,
            CaptureGeometryAvailable = _cache.Available(frameId),
            Honesty = honesty,
        };
        return true;
    }

    void SetupAimLine()
    {
        _aimLine = gameObject.AddComponent<LineRenderer>();
        _aimLine.positionCount = 2;
        _aimLine.useWorldSpace = true;
        _aimLine.startWidth = AimLineWidthM;
        _aimLine.endWidth = AimLineWidthM;
        _aimLine.shadowCastingMode = ShadowCastingMode.Off;
        _aimLine.receiveShadows = false;
        Shader shader = Shader.Find("Unlit/Color");
        if (shader == null)
        {
            Debug.LogWarning("SpatialRuntime: Unlit/Color shader missing, aim line disabled");
            _aimLine.enabled = false;
            return;
        }
        _aimMaterial = new Material(shader);
        _aimLine.material = _aimMaterial;
    }

    void UpdateAimLine()
    {
        if (_aimLine == null || !_aimLine.enabled)
            return;
        bool triggerHeld = false;
        try
        {
            triggerHeld = OVRInput.Get(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch);
        }
        catch (Exception)
        {
            triggerHeld = false;
        }
        Vector3 origin;
        Vector3 direction;
        Color color;
        if (triggerHeld && _rightAim != null)
        {
            origin = _rightAim.position;
            direction = _rightAim.forward;
            color = Color.green;
        }
        else if (_centerEye != null)
        {
            origin = _centerEye.position;
            direction = _centerEye.forward;
            color = Color.cyan;
        }
        else
        {
            return;
        }
        _aimLine.SetPosition(0, origin);
        _aimLine.SetPosition(1, origin + direction * MaxCaptureDistanceM);
        if (_aimMaterial != null)
            _aimMaterial.color = color;
    }

    internal static long ToUnixNanoseconds(DateTime timestamp)
    {
        return (timestamp.ToUniversalTime().Ticks - UnixEpoch.Ticks) * 100L;
    }

    internal static string NewFrameId()
    {
        return NewFrameId(DateTime.UtcNow, Guid.NewGuid().ToByteArray());
    }

    /// <summary>
    /// ULID shape (mirrors provider/protocol/ids.py): 48-bit unix millis in
    /// Crockford base32 (10 chars) + 80 random bits (16 chars). All 10 random
    /// bytes are encoded LSB-first via a bit cursor; short inputs zero-pad.
    /// Public as a deterministic test seam.
    /// </summary>
    public static string NewFrameId(DateTime utcNow, byte[] randomness)
    {
        long ms = (long)(utcNow.Subtract(UnixEpoch).TotalMilliseconds);
        var chars = new char[26];
        for (int i = 9; i >= 0; i--)
        {
            chars[i] = Crockford[(int)(ms & 31)];
            ms >>= 5;
        }
        int bitPos = 0;
        for (int i = 25; i >= 10; i--)
        {
            int val = 0;
            for (int b = 0; b < 5; b++, bitPos++)
            {
                int byteIdx = randomness.Length - 1 - (bitPos / 8);
                int bit = byteIdx >= 0 ? ((randomness[byteIdx] >> (bitPos % 8)) & 1) : 0;
                val |= bit << b;
            }
            chars[i] = Crockford[val];
        }
        return new string(chars);
    }
}
