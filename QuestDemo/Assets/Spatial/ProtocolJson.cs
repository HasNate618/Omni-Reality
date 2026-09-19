using System.Globalization;
using System.Text;

/// <summary>
/// Explicit JSON serialization for the slice 1 capture envelope.
/// Handwritten writer (no Unity package added): JsonUtility cannot emit
/// <c>null</c> for missing pointing/world_hint, which the protocol requires.
/// Key order and names mirror provider/protocol/schemas/capture_envelope.json.
/// </summary>
public static class ProtocolJson
{
    public const string CameraLeft = "left";
    public const string PoseFrame = "openxr_floor_stage";

    /// <summary>Every top-level key in valid/capture_envelope.json, in schema order.</summary>
    public static readonly string[] SpecKeys =
    {
        "frame_id", "stage_epoch", "t_unix_ns", "camera", "image_w", "image_h",
        "sent_w", "sent_h", "intrinsics", "distortion", "pose", "crop",
        "pointing", "world_hint", "capture_geometry_available",
    };

    public static string ToSpecJson(CaptureEnvelope env)
    {
        var sb = new StringBuilder(512);
        sb.Append("{\"frame_id\":\"");
        AppendEscaped(sb, env.FrameId);
        sb.Append("\",\"stage_epoch\":");
        sb.Append(env.StageEpoch);
        sb.Append(",\"t_unix_ns\":");
        sb.Append(env.TUnixNs);
        sb.Append(",\"camera\":\"");
        AppendEscaped(sb, env.Camera);
        sb.Append("\",\"image_w\":");
        sb.Append(env.ImageW);
        sb.Append(",\"image_h\":");
        sb.Append(env.ImageH);
        sb.Append(",\"sent_w\":");
        sb.Append(env.SentW);
        sb.Append(",\"sent_h\":");
        sb.Append(env.SentH);
        sb.Append(",\"intrinsics\":{\"fx\":");
        AppendFloat(sb, env.Fx);
        sb.Append(",\"fy\":");
        AppendFloat(sb, env.Fy);
        sb.Append(",\"cx\":");
        AppendFloat(sb, env.Cx);
        sb.Append(",\"cy\":");
        AppendFloat(sb, env.Cy);
        sb.Append("},\"distortion\":{\"model\":\"");
        AppendEscaped(sb, env.DistortionModel);
        sb.Append("\",\"k\":[");
        if (env.DistortionK != null)
        {
            for (int i = 0; i < env.DistortionK.Length; i++)
            {
                if (i > 0)
                    sb.Append(',');
                AppendFloat(sb, env.DistortionK[i]);
            }
        }
        sb.Append("]},\"pose\":{\"px\":");
        AppendFloat(sb, env.PosePx);
        sb.Append(",\"py\":");
        AppendFloat(sb, env.PosePy);
        sb.Append(",\"pz\":");
        AppendFloat(sb, env.PosePz);
        sb.Append(",\"qx\":");
        AppendFloat(sb, env.PoseQx);
        sb.Append(",\"qy\":");
        AppendFloat(sb, env.PoseQy);
        sb.Append(",\"qz\":");
        AppendFloat(sb, env.PoseQz);
        sb.Append(",\"qw\":");
        AppendFloat(sb, env.PoseQw);
        sb.Append(",\"frame\":\"");
        sb.Append(PoseFrame);
        sb.Append("\"},\"crop\":{\"sx\":");
        AppendFloat(sb, env.CropSx);
        sb.Append(",\"sy\":");
        AppendFloat(sb, env.CropSy);
        sb.Append(",\"tx\":");
        AppendFloat(sb, env.CropTx);
        sb.Append(",\"ty\":");
        AppendFloat(sb, env.CropTy);
        sb.Append("},\"pointing\":");
        if (env.Pointing == null)
        {
            sb.Append("null");
        }
        else
        {
            var p = env.Pointing;
            sb.Append("{\"source\":\"");
            AppendEscaped(sb, p.Source);
            sb.Append("\",\"t_unix_ns\":");
            sb.Append(p.TUnixNs);
            sb.Append(",\"origin\":{\"px\":");
            AppendFloat(sb, p.OriginPx);
            sb.Append(",\"py\":");
            AppendFloat(sb, p.OriginPy);
            sb.Append(",\"pz\":");
            AppendFloat(sb, p.OriginPz);
            sb.Append("},\"direction\":{\"x\":");
            AppendFloat(sb, p.DirX);
            sb.Append(",\"y\":");
            AppendFloat(sb, p.DirY);
            sb.Append(",\"z\":");
            AppendFloat(sb, p.DirZ);
            sb.Append("},\"frame\":\"");
            sb.Append(PoseFrame);
            sb.Append("\"}");
        }
        sb.Append(",\"world_hint\":");
        if (env.WorldHint == null)
        {
            sb.Append("null");
        }
        else
        {
            var h = env.WorldHint;
            sb.Append("{\"px\":");
            AppendFloat(sb, h.Px);
            sb.Append(",\"py\":");
            AppendFloat(sb, h.Py);
            sb.Append(",\"pz\":");
            AppendFloat(sb, h.Pz);
            sb.Append(",\"nx\":");
            AppendFloat(sb, h.Nx);
            sb.Append(",\"ny\":");
            AppendFloat(sb, h.Ny);
            sb.Append(",\"nz\":");
            AppendFloat(sb, h.Nz);
            sb.Append(",\"frame\":\"");
            sb.Append(PoseFrame);
            sb.Append("\",\"source\":\"");
            AppendEscaped(sb, h.Source);
            sb.Append("\",\"t_unix_ns\":");
            sb.Append(h.TUnixNs);
            sb.Append('}');
        }
        sb.Append(",\"capture_geometry_available\":");
        sb.Append(env.CaptureGeometryAvailable ? "true" : "false");
        sb.Append('}');
        return sb.ToString();
    }

    static void AppendFloat(StringBuilder sb, float value)
    {
        sb.Append(value.ToString("R", CultureInfo.InvariantCulture));
    }

    static void AppendEscaped(StringBuilder sb, string value)
    {
        if (string.IsNullOrEmpty(value))
            return;
        foreach (char c in value)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < 0x20)
                    {
                        sb.Append("\\u");
                        sb.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    }
                    else
                    {
                        sb.Append(c);
                    }
                    break;
            }
        }
    }
}
