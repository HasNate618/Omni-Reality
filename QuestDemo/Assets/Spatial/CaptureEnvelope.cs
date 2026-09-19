/// <summary>
/// Slice 1 left-camera capture envelope. Plain data container; every field maps
/// to a key in provider/protocol/schemas/capture_envelope.json via
/// <see cref="ProtocolJson.ToSpecJson"/>. <see cref="Honesty"/> is deliberately
/// NOT serialized: it is the local capture-range verdict
/// (<c>"placed"</c>, <c>"too_close"</c>, <c>"no_surface"</c>) and never a pin.
/// </summary>
public class CaptureEnvelope
{
    public string FrameId;
    public int StageEpoch;
    public long TUnixNs;
    public string Camera = ProtocolJson.CameraLeft;
    public int ImageW;
    public int ImageH;
    public int SentW;
    public int SentH;
    public float Fx;
    public float Fy;
    public float Cx;
    public float Cy;
    public string DistortionModel = "none";
    public float[] DistortionK = new float[0];
    public float PosePx;
    public float PosePy;
    public float PosePz;
    public float PoseQx;
    public float PoseQy;
    public float PoseQz;
    public float PoseQw = 1f;
    public float CropSx = 1f;
    public float CropSy = 1f;
    public float CropTx;
    public float CropTy;
    public CapturePointing Pointing;
    public CaptureWorldHint WorldHint;
    public bool CaptureGeometryAvailable;
    public string Honesty = "no_surface";

    public string ToSpecJson()
    {
        return ProtocolJson.ToSpecJson(this);
    }
}

/// <summary>Pointing ray at capture time. Null when no pointing source applied.</summary>
public class CapturePointing
{
    public string Source;
    public long TUnixNs;
    public float OriginPx;
    public float OriginPy;
    public float OriginPz;
    public float DirX;
    public float DirY;
    public float DirZ;
}

/// <summary>Single surface point from the capture-time ray. Null on any miss.</summary>
public class CaptureWorldHint
{
    public float Px;
    public float Py;
    public float Pz;
    public float Nx;
    public float Ny;
    public float Nz;
    public string Source;
    public long TUnixNs;
}
