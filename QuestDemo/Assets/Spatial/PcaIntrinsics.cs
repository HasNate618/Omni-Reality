/// <summary>
/// Converts raw PCA sensor intrinsics into the envelope's agreed convention:
/// top-left pixel-centre coordinates in original camera (output texture)
/// pixels. The SDK reports focal length / principal point in full-sensor
/// pixels, but the delivered texture (<see cref="CurrentResolution"/>) may be
/// a centred crop/scale of that sensor. The crop math below mirrors
/// <c>PassthroughCameraAccess</c>'s internal sensor-crop region
/// (scale output/sensor, normalize by the dominant axis, centre the window).
/// Sensor space here is the SDK's own space: viewport (u bottom-left origin)
/// maps to sensor as (ox + cw*u, oy + ch*v), and the principal point lives in
/// that same space (y-up). The envelope reports y-down top-left pixels, so the
/// vertical axis is mirrored while x passes through unchanged.
/// Deliberately free of UnityEngine types so EditMode tests can pin the math.
/// </summary>
public static class PcaIntrinsics
{
    /// <summary>SDK-equivalent sensor crop window for an output size.</summary>
    public static void SensorCropRegion(float sensorW, float sensorH,
        float outW, float outH,
        out float ox, out float oy, out float cw, out float ch)
    {
        float sx = outW / sensorW;
        float sy = outH / sensorH;
        float m = sx > sy ? sx : sy;
        sx /= m;
        sy /= m;
        ox = sensorW * (1f - sx) * 0.5f;
        oy = sensorH * (1f - sy) * 0.5f;
        cw = sensorW * sx;
        ch = sensorH * sy;
    }

    /// <summary>
    /// Converts sensor-pixel intrinsics to output-pixel intrinsics. Returns
    /// false when any dimension is degenerate (caller must not emit).
    /// </summary>
    public static bool TryToOutputPixels(float sensorFx, float sensorFy,
        float sensorCx, float sensorCy,
        float sensorW, float sensorH, float outW, float outH,
        out float fx, out float fy, out float cx, out float cy)
    {
        fx = 0f;
        fy = 0f;
        cx = 0f;
        cy = 0f;
        if (sensorW <= 0f || sensorH <= 0f || outW <= 0f || outH <= 0f)
            return false;
        SensorCropRegion(sensorW, sensorH, outW, outH,
            out float ox, out float oy, out float cw, out float ch);
        if (cw <= 0f || ch <= 0f)
            return false;
        fx = sensorFx * outW / cw;
        fy = sensorFy * outH / ch;
        cx = (sensorCx - ox) * outW / cw;
        cy = (oy + ch - sensorCy) * outH / ch;
        return true;
    }

    /// <summary>
    /// SDK-equivalent local ray direction for a viewport point (u, v with a
    /// bottom-left origin): direction = ((ox + cw*u - cx)/fx, (oy + ch*v -
    /// cy)/fy, 1). Matches ViewportPointToLocalRay semantics.
    /// </summary>
    public static void SensorRayDirection(float u, float v,
        float sensorFx, float sensorFy, float sensorCx, float sensorCy,
        float ox, float oy, float cw, float ch,
        out float dx, out float dy)
    {
        dx = (ox + cw * u - sensorCx) / sensorFx;
        dy = (oy + ch * v - sensorCy) / sensorFy;
    }

    /// <summary>
    /// Camera-space ray direction (y-up, matching the SDK convention) for an
    /// output pixel in top-left pixel-centre coordinates.
    /// </summary>
    public static void OutputRayDirection(float px, float py,
        float fx, float fy, float cx, float cy,
        out float dx, out float dy)
    {
        dx = (px - cx) / fx;
        dy = (cy - py) / fy;
    }
}
