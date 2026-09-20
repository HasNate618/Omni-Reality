using System;
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

    // ---- Task 7 coordinator wire (Quest <-> laptop, slice 2) ----
    // Hand-written like ToSpecJson (no Unity package added). Outbound
    // builders emit v1 message wrappers mirroring
    // provider/protocol/schemas/message.json. Inbound parsing is a minimal
    // string scanner for laptop-authored frames only; not a general parser.

    /// <summary>Coordinator port (ws://{laptop_ipv4}:8765).</summary>
    public const int CoordinatorPort = 8765;

    /// <summary>Omitted/null mark motion defaults to a 1.2 s pulse.</summary>
    public const float DefaultMarkPeriodS = 1.2f;

    /// <summary>Quest hello capabilities (mirrors the hello fixture).</summary>
    public const string HelloPayload =
        "{\"device\":\"quest3s\",\"app\":\"QuestDemo\",\"os_version\":\"74\"," +
        "\"capabilities\":{\"pca\":true,\"depth\":true,\"tts\":true}}";

    static void AppendSessionId(StringBuilder sb, string sessionId)
    {
        if (sessionId == null)
            sb.Append("null");
        else
        {
            sb.Append('\"');
            AppendEscaped(sb, sessionId);
            sb.Append('\"');
        }
    }
    static void AppendNullable(StringBuilder sb, string value)
    {
        if (value == null)
            sb.Append("null");
        else
        {
            sb.Append('\"');
            AppendEscaped(sb, value);
            sb.Append('\"');
        }
    }

    static string WrapMessage(string type, string sessionId, int turnId, string payloadJson)
    {
        var sb = new StringBuilder(payloadJson.Length + 96);
        sb.Append("{\"v\":1,\"type\":\"");
        AppendEscaped(sb, type);
        sb.Append("\",\"session_id\":");
        AppendSessionId(sb, sessionId);
        sb.Append(",\"turn_id\":");
        sb.Append(turnId);
        sb.Append(",\"utterance_id\":null,\"payload\":");
        sb.Append(payloadJson);
        sb.Append('}');
        return sb.ToString();
    }

    /// <summary>Quest hello wrapper (session null until hello_ok assigns one).</summary>
    public static string BuildHello(string sessionId)
    {
        return WrapMessage("hello", sessionId, 0, HelloPayload);
    }

    /// <summary>Quest ping wrapper (server replies pong).</summary>
    public static string BuildPing(string sessionId)
    {
        return WrapMessage("ping", sessionId, 0, "{}");
    }

    /// <summary>
    /// Quest frame wrapper carrying the capture envelope only. Slice 2
    /// omits jpeg_b64: ack/cancel travel as separate WS messages and must
    /// never wait on JPEG encoding.
    /// </summary>
    public static string BuildFrame(string sessionId, CaptureEnvelope env)
    {
        return BuildFrame(sessionId, env, null);
    }

    /// <summary>Frame wrapper tagged with an open utterance_id (voice turn).</summary>
    public static string BuildFrame(string sessionId, CaptureEnvelope env, string utteranceId)
    {
        return BuildFrame(sessionId, env, utteranceId, null);
    }

    /// <summary>SAM2 stream frame: small JPEG, no utterance, tracking flag set.</summary>
    public static string BuildTrackingFrame(string sessionId, CaptureEnvelope env, string jpegBase64)
    {
        var sb = new StringBuilder(640);
        sb.Append("{\"envelope\":");
        sb.Append(ToSpecJson(env));
        if (!string.IsNullOrEmpty(jpegBase64))
        {
            sb.Append(",\"jpeg_b64\":\"");
            AppendEscaped(sb, jpegBase64);
            sb.Append('"');
        }
        sb.Append(",\"tracking\":true}");
        return WrapMessage("frame", sessionId, 0, sb.ToString());
    }

    /// <summary>One per-question JPEG with the matching capture-time envelope.</summary>
    public static string BuildFrame(string sessionId, CaptureEnvelope env, string utteranceId, string jpegBase64)
    {
        var sb = new StringBuilder(640);
        sb.Append("{\"envelope\":");
        sb.Append(ToSpecJson(env));
        if (!string.IsNullOrEmpty(jpegBase64))
        {
            sb.Append(",\"jpeg_b64\":\"");
            AppendEscaped(sb, jpegBase64);
            sb.Append('"');
        }
        sb.Append('}');
        if (string.IsNullOrEmpty(utteranceId))
            return WrapMessage("frame", sessionId, 0, sb.ToString());
        return WrapUtteranceMessage("frame", sessionId, utteranceId, sb.ToString());
    }

    /// <summary>
    /// Quest ack wrapper with a placement_ack payload. Callers pass placed:
    /// (drawingId, pin "surface"); rejected/stale: (null drawingId, reason).
    /// Nulls serialize as JSON null per the schema.
    /// </summary>
    public static string BuildAck(
        string sessionId, int turnId, string opId, int stageEpoch,
        string status, string drawingId, string reason, string pin)
    {
        var sb = new StringBuilder(256);
        sb.Append("{\"op_id\":\"");
        AppendEscaped(sb, opId);
        sb.Append("\",\"turn_id\":");
        sb.Append(turnId);
        sb.Append(",\"stage_epoch\":");
        sb.Append(stageEpoch);
        sb.Append(",\"drawing_id\":");
        AppendNullable(sb, drawingId);
        sb.Append(",\"status\":\"");
        AppendEscaped(sb, status);
        sb.Append("\",\"reason\":");
        AppendNullable(sb, reason);
        sb.Append(",\"pin\":");
        AppendNullable(sb, pin);
        sb.Append('}');
        return WrapMessage("ack", sessionId, turnId, sb.ToString());
    }

    /// <summary>Wrap a voice message carrying a top-level utterance_id.</summary>
    public static string WrapUtteranceMessage(string type, string sessionId, string utteranceId, string payloadJson)
    {
        var sb = new StringBuilder(128);
        sb.Append("{\"v\":1,\"type\":\"");
        AppendEscaped(sb, type);
        sb.Append("\",\"session_id\":");
        AppendSessionId(sb, sessionId);
        sb.Append(",\"turn_id\":0,\"utterance_id\":\"");
        AppendEscaped(sb, utteranceId);
        sb.Append("\",\"payload\":");
        sb.Append(payloadJson);
        sb.Append('}');
        return sb.ToString();
    }

    /// <summary>~100 ms PCM chunk (16 kHz mono s16le base64).</summary>
    public static string BuildAudioChunk(string sessionId, string utteranceId, string dataB64)
    {
        var sb = new StringBuilder(64);
        sb.Append("{\"utterance_id\":\"");
        AppendEscaped(sb, utteranceId);
        sb.Append(
            "\",\"audio\":{\"encoding\":\"pcm_s16le\",\"sample_rate\":16000,\"channels\":1,\"data_b64\":\"");
        AppendEscaped(sb, dataB64);
        sb.Append("\"}}");
        return WrapUtteranceMessage("audio_chunk", sessionId, utteranceId, sb.ToString());
    }

    /// <summary>Close an utterance; the coordinator then runs the turn.</summary>
    public static string BuildUtteranceEnd(string sessionId, string utteranceId)
    {
        var sb = new StringBuilder(64);
        sb.Append("{\"utterance_id\":\"");
        AppendEscaped(sb, utteranceId);
        sb.Append("\"}");
        return WrapUtteranceMessage("utterance_end", sessionId, utteranceId, sb.ToString());
    }

    /// <summary>Quest cancel wrapper (payload carries the cancelled op_id).</summary>
    public static string BuildCancel(string sessionId, int turnId, string opId)
    {
        var sb = new StringBuilder(96);
        sb.Append("{\"op_id\":\"");
        AppendEscaped(sb, opId);
        sb.Append("\"}");
        return WrapMessage("cancel", sessionId, turnId, sb.ToString());
    }

    /// <summary>Top-level message type of an inbound frame, if present.</summary>
    public static bool TryGetMessageType(string json, out string type)
    {
        type = null;
        string value;
        bool found;
        if (!TryGetStringOrNull(json, "type", out value, out found))
            return false;
        if (!found || value == null)
            return false;
        type = value;
        return true;
    }

    /// <summary>Top-level session_id (null when JSON null or missing).</summary>
    public static bool TryGetSessionId(string json, out string sessionId)
    {
        bool found;
        return TryGetStringOrNull(json, "session_id", out sessionId, out found);
    }

    public static bool TryGetUtteranceId(string json, out string utteranceId)
    {
        bool found;
        return TryGetStringOrNull(json, "utterance_id", out utteranceId, out found);
    }

    /// <summary>Brace-matched payload object substring, if present.</summary>
    public static bool TryGetPayloadObject(string json, out string payloadJson)
    {
        payloadJson = null;
        int keyAt = IndexOfKey(json, "payload", 0);
        if (keyAt < 0)
            return false;
        int braceAt = json.IndexOf('{', keyAt);
        if (braceAt < 0)
            return false;
        string obj;
        int endAt;
        if (!ExtractBraced(json, braceAt, out obj, out endAt))
            return false;
        payloadJson = obj;
        return true;
    }

    /// <summary>Parsed laptop scene_op (mark path only in slice 2).</summary>
    public sealed class SceneOpMsg
    {
        public string OpId;
        public bool HasTurnId;
        public int TurnId;
        public bool HasStageEpoch;
        public int StageEpoch;
        public string Kind;
        public string JobId;
        public string DrawingId;
        public string TargetFrameId;
        public string FromTargetFrameId;
        public string ToTargetFrameId;
        public string Text;
        public string ElementsJson;
        public string Action;
        public string Direction;
        public string MotionKind;
        public string MotionAxis;
        public float MotionAngleDeg;
        public float MotionDistanceM;
        public bool HasMotion;
        public float MotionPeriodS;
    }

    /// <summary>One closed-grammar procedural element (voice spec §3.1).</summary>
    public sealed class ProceduralElement
    {
        public string Element;
        public string Color;
        public string Size;
        public string Material;
        public string Text;
    }

    public sealed class SpeakMsg
    {
        public bool HasTurnId;
        public int TurnId;
        public string Text;
        public string AudioEncoding;
        public string AudioDataB64;
    }

    /// <summary>
    /// Parse a scene_op payload object. False when required keys are missing
    /// or malformed; the caller then ignores the frame.
    /// </summary>
    public static bool TryParseSceneOp(string payloadJson, out SceneOpMsg op)
    {
        op = null;
        if (string.IsNullOrEmpty(payloadJson))
            return false;
        var parsed = new SceneOpMsg();
        string s;
        bool found;
        if (!TryGetStringOrNull(payloadJson, "op_id", out s, out found) || !found || s == null)
            return false;
        parsed.OpId = s;
        long turnId;
        if (TryGetLong(payloadJson, "turn_id", out turnId))
        {
            parsed.HasTurnId = true;
            parsed.TurnId = (int)turnId;
        }
        long epoch;
        if (TryGetLong(payloadJson, "stage_epoch", out epoch))
        {
            parsed.HasStageEpoch = true;
            parsed.StageEpoch = (int)epoch;
        }
        if (!TryGetStringOrNull(payloadJson, "kind", out s, out found) || !found || s == null)
            return false;
        parsed.Kind = s;
        if (TryGetStringOrNull(payloadJson, "job_id", out s, out found) && found)
            parsed.JobId = s;
        if (TryGetStringOrNull(payloadJson, "drawing_id", out s, out found) && found)
            parsed.DrawingId = s;
        if (TryGetStringOrNull(payloadJson, "text", out s, out found) && found)
            parsed.Text = s;
        if (TryGetStringOrNull(payloadJson, "action", out s, out found) && found)
            parsed.Action = s;
        if (TryGetStringOrNull(payloadJson, "direction", out s, out found) && found)
            parsed.Direction = s;
        int elementsAt = IndexOfKey(payloadJson, "elements", 0);
        if (elementsAt >= 0)
        {
            int bracketAt = payloadJson.IndexOf('[', elementsAt);
            string arr;
            int endAt;
            if (bracketAt >= 0 && ExtractBracketed(payloadJson, bracketAt, out arr, out endAt))
                parsed.ElementsJson = arr;
        }
        TryGetTargetFrameId(payloadJson, "target", out parsed.TargetFrameId);
        TryGetTargetFrameId(payloadJson, "from", out parsed.FromTargetFrameId);
        TryGetTargetFrameId(payloadJson, "to", out parsed.ToTargetFrameId);
        int motionAt = IndexOfKey(payloadJson, "motion", 0);
        if (motionAt >= 0)
        {
            int valueAt = SkipValueStart(payloadJson, motionAt);
            if (valueAt >= 0 && valueAt < payloadJson.Length && payloadJson[valueAt] == '{')
            {
                string motionJson;
                int endAt;
                if (ExtractBraced(payloadJson, valueAt, out motionJson, out endAt))
                {
                    double period;
                    if (TryGetDouble(motionJson, "period_s", out period))
                    {
                        parsed.HasMotion = true;
                        parsed.MotionPeriodS = (float)period;
                    }
                    string kind;
                    bool kindFound;
                    if (TryGetStringOrNull(motionJson, "kind", out kind, out kindFound) && kindFound)
                        parsed.MotionKind = kind;
                    if (TryGetStringOrNull(motionJson, "axis", out kind, out kindFound) && kindFound)
                        parsed.MotionAxis = kind;
                    double angle;
                    if (TryGetDouble(motionJson, "angle_deg", out angle))
                        parsed.MotionAngleDeg = (float)angle;
                    double dist;
                    if (TryGetDouble(motionJson, "distance_m", out dist))
                        parsed.MotionDistanceM = (float)dist;
                }
            }
        }
        op = parsed;
        return true;
    }

    static void TryGetTargetFrameId(string payloadJson, string key, out string frameId)
    {
        frameId = null;
        int keyAt = IndexOfKey(payloadJson, key, 0);
        if (keyAt < 0)
            return;
        int braceAt = payloadJson.IndexOf('{', keyAt);
        string targetJson;
        int endAt;
        if (braceAt < 0 || !ExtractBraced(payloadJson, braceAt, out targetJson, out endAt))
            return;
        string parsed;
        bool found;
        if (TryGetStringOrNull(targetJson, "frame_id", out parsed, out found) && found)
            frameId = parsed;
    }

    /// <summary>One streamed speech chunk (turn_id, seq, audio object).</summary>
    public sealed class SpeakChunkMsg
    {
        public int TurnId;
        public int Seq;
        public string AudioDataB64;
    }

    /// <summary>End of streamed speech (turn_id, text, voice_gate).</summary>
    public sealed class SpeakFinalMsg
    {
        public int TurnId;
        public string Text;
        public string VoiceGate;
    }

    /// <summary>Parse a speak_chunk payload. False when turn/seq/audio missing.</summary>
    public static bool TryParseSpeakChunk(string payloadJson, out SpeakChunkMsg msg)
    {
        msg = null;
        if (string.IsNullOrEmpty(payloadJson))
            return false;
        long turnId;
        long seq;
        if (!TryGetLong(payloadJson, "turn_id", out turnId))
            return false;
        if (!TryGetLong(payloadJson, "seq", out seq))
            return false;
        string audioData = null;
        int audioAt = IndexOfKey(payloadJson, "audio", 0);
        if (audioAt >= 0)
        {
            int valueAt = SkipValueStart(payloadJson, audioAt);
            if (valueAt >= 0 && valueAt < payloadJson.Length && payloadJson[valueAt] == '{')
            {
                string audioJson;
                int endAt;
                bool found;
                if (ExtractBraced(payloadJson, valueAt, out audioJson, out endAt))
                    TryGetStringOrNull(audioJson, "data_b64", out audioData, out found);
            }
        }
        if (string.IsNullOrEmpty(audioData))
            return false;
        msg = new SpeakChunkMsg { TurnId = (int)turnId, Seq = (int)seq, AudioDataB64 = audioData };
        return true;
    }

    /// <summary>Parse a speak_final payload. False when turn_id missing.</summary>
    public static bool TryParseSpeakFinal(string payloadJson, out SpeakFinalMsg msg)
    {
        msg = null;
        if (string.IsNullOrEmpty(payloadJson))
            return false;
        long turnId;
        if (!TryGetLong(payloadJson, "turn_id", out turnId))
            return false;
        string text;
        bool found;
        TryGetStringOrNull(payloadJson, "text", out text, out found);
        string gate;
        TryGetStringOrNull(payloadJson, "voice_gate", out gate, out found);
        msg = new SpeakFinalMsg { TurnId = (int)turnId, Text = text, VoiceGate = gate };
        return true;
    }

    /// <summary>SAM2 tracking status: selecting/initializing/tracking/stopped/error.</summary>
    public sealed class TrackingStatusMsg
    {
        public string State;
        public string Text;
        public int Generation;
    }

    /// <summary>One SAM2 tracking result; MaskB64 null means the track is lost.</summary>
    public sealed class TrackingResultMsg
    {
        public string FrameId;
        public string SeedFrameId;
        public int Generation;
        public int Width;
        public int Height;
        public string MaskB64;
        public bool HasStageEpoch;
        public int StageEpoch;
    }

    /// <summary>Parse a tracking_status payload. False when state missing.</summary>
    public static bool TryParseTrackingStatus(string payloadJson, out TrackingStatusMsg msg)
    {
        msg = null;
        if (string.IsNullOrEmpty(payloadJson))
            return false;
        string state;
        bool found;
        if (!TryGetStringOrNull(payloadJson, "state", out state, out found) || !found)
            return false;
        string text;
        TryGetStringOrNull(payloadJson, "text", out text, out found);
        long generation;
        TryGetLong(payloadJson, "generation", out generation);
        msg = new TrackingStatusMsg { State = state, Text = text, Generation = (int)generation };
        return true;
    }

    /// <summary>Parse a tracking_result payload. MaskB64 null when no objects.</summary>
    public static bool TryParseTrackingResult(string payloadJson, out TrackingResultMsg msg)
    {
        msg = null;
        if (string.IsNullOrEmpty(payloadJson))
            return false;
        var parsed = new TrackingResultMsg();
        string value;
        bool found;
        if (TryGetStringOrNull(payloadJson, "frame_id", out value, out found) && found)
            parsed.FrameId = value;
        if (TryGetStringOrNull(payloadJson, "seed_frame_id", out value, out found) && found)
            parsed.SeedFrameId = value;
        long number;
        if (TryGetLong(payloadJson, "generation", out number))
            parsed.Generation = (int)number;
        if (TryGetLong(payloadJson, "width", out number))
            parsed.Width = (int)number;
        if (TryGetLong(payloadJson, "height", out number))
            parsed.Height = (int)number;
        int objectsAt = IndexOfKey(payloadJson, "objects", 0);
        if (objectsAt >= 0)
        {
            int valueAt = SkipValueStart(payloadJson, objectsAt);
            if (valueAt >= 0 && valueAt < payloadJson.Length && payloadJson[valueAt] == '[')
            {
                int braceAt = payloadJson.IndexOf('{', valueAt);
                string firstJson;
                int endAt;
                if (braceAt >= 0 && ExtractBraced(payloadJson, braceAt, out firstJson, out endAt))
                    if (TryGetStringOrNull(firstJson, "mask_b64", out value, out found) && found)
                        parsed.MaskB64 = value;
            }
        }
        int envelopeAt = IndexOfKey(payloadJson, "envelope", 0);
        if (envelopeAt >= 0)
        {
            int valueAt = SkipValueStart(payloadJson, envelopeAt);
            if (valueAt >= 0 && valueAt < payloadJson.Length && payloadJson[valueAt] == '{')
            {
                string envJson;
                int endAt;
                if (ExtractBraced(payloadJson, valueAt, out envJson, out endAt)
                    && TryGetLong(envJson, "stage_epoch", out number))
                {
                    parsed.HasStageEpoch = true;
                    parsed.StageEpoch = (int)number;
                }
            }
        }
        if (string.IsNullOrEmpty(parsed.FrameId))
            return false;
        msg = parsed;
        return true;
    }

    /// <summary>Parse speak payload (turn_id, text, optional audio object).</summary>
    public static bool TryParseSpeak(string payloadJson, out SpeakMsg msg)
    {
        msg = null;
        if (string.IsNullOrEmpty(payloadJson))
            return false;
        var parsed = new SpeakMsg();
        long turnId;
        if (TryGetLong(payloadJson, "turn_id", out turnId))
        {
            parsed.HasTurnId = true;
            parsed.TurnId = (int)turnId;
        }
        string text;
        bool found;
        if (TryGetStringOrNull(payloadJson, "text", out text, out found) && found)
            parsed.Text = text;
        int audioAt = IndexOfKey(payloadJson, "audio", 0);
        if (audioAt >= 0)
        {
            int valueAt = SkipValueStart(payloadJson, audioAt);
            if (valueAt >= 0 && valueAt < payloadJson.Length && payloadJson[valueAt] == '{')
            {
                string audioJson;
                int endAt;
                if (ExtractBraced(payloadJson, valueAt, out audioJson, out endAt))
                {
                    TryGetStringOrNull(audioJson, "encoding", out parsed.AudioEncoding, out found);
                    TryGetStringOrNull(audioJson, "data_b64", out parsed.AudioDataB64, out found);
                }
            }
        }
        msg = parsed;
        return true;
    }

    static int IndexOfKey(string json, string key, int startAt)
    {
        string quoted = "\"" + key + "\"";
        int at = json.IndexOf(quoted, startAt, StringComparison.Ordinal);
        while (at >= 0)
        {
            int i = at + quoted.Length;
            while (i < json.Length && char.IsWhiteSpace(json[i]))
                i++;
            if (i < json.Length && json[i] == ':')
                return i;
            at = json.IndexOf(quoted, at + 1, StringComparison.Ordinal);
        }
        return -1;
    }

    static int SkipValueStart(string json, int colonAt)
    {
        int i = colonAt + 1;
        while (i < json.Length && char.IsWhiteSpace(json[i]))
            i++;
        return i < json.Length ? i : -1;
    }

    /// <summary>String-or-null field read. found=false when the key is absent.</summary>
    static bool TryGetStringOrNull(string json, string key, out string value, out bool found)
    {
        value = null;
        found = false;
        if (string.IsNullOrEmpty(json))
            return false;
        int colonAt = IndexOfKey(json, key, 0);
        if (colonAt < 0)
            return true;
        found = true;
        int i = SkipValueStart(json, colonAt);
        if (i < 0)
            return false;
        if (json[i] == 'n')
            return true; // JSON null stays null.
        if (json[i] != '"')
            return false;
        string parsed;
        int endAt;
        if (!TryReadString(json, i, out parsed, out endAt))
            return false;
        value = parsed;
        return true;
    }

    /// <summary>Integer field read (hello_ok artifact_port, etc.).</summary>
    public static bool TryGetIntField(string json, string key, out int value)
    {
        long l;
        if (!TryGetLong(json, key, out l))
        {
            value = 0;
            return false;
        }
        value = (int)l;
        return true;
    }

    static bool TryGetLong(string json, string key, out long value)
    {
        value = 0;
        int colonAt = IndexOfKey(json, key, 0);
        if (colonAt < 0)
            return false;
        int i = SkipValueStart(json, colonAt);
        if (i < 0)
            return false;
        int start = i;
        if (i < json.Length && (json[i] == '-' || json[i] == '+'))
            i++;
        while (i < json.Length && char.IsDigit(json[i]))
            i++;
        int digits = i - start;
        if (digits == 0 || (digits == 1 && (json[start] == '-' || json[start] == '+')))
            return false;
        return long.TryParse(json.Substring(start, i - start),
            NumberStyles.Integer, CultureInfo.InvariantCulture, out value);
    }

    static bool TryGetDouble(string json, string key, out double value)
    {
        value = 0;
        int colonAt = IndexOfKey(json, key, 0);
        if (colonAt < 0)
            return false;
        int i = SkipValueStart(json, colonAt);
        if (i < 0)
            return false;
        int start = i;
        if (i < json.Length && (json[i] == '-' || json[i] == '+'))
            i++;
        bool any = false;
        while (i < json.Length && (char.IsDigit(json[i]) || json[i] == '.'
            || json[i] == 'e' || json[i] == 'E' || json[i] == '-' || json[i] == '+'))
        {
            i++;
            any = true;
        }
        if (!any)
            return false;
        return double.TryParse(json.Substring(start, i - start),
            NumberStyles.Float, CultureInfo.InvariantCulture, out value);
    }

    static bool TryReadString(string json, int quoteAt, out string value, out int endAt)
    {
        value = null;
        endAt = -1;
        var sb = new StringBuilder(32);
        int i = quoteAt + 1;
        while (i < json.Length)
        {
            char c = json[i];
            if (c == '"')
            {
                value = sb.ToString();
                endAt = i;
                return true;
            }
            if (c == '\\' && i + 1 < json.Length)
            {
                char e = json[i + 1];
                switch (e)
                {
                    case '"': sb.Append('\"'); break;
                    case '\\': sb.Append('\\'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    default: sb.Append(e); break;
                }
                i += 2;
                continue;
            }
            sb.Append(c);
            i++;
        }
        return false;
    }

    /// <summary>Parse a procedural elements array into element records.</summary>
    public static bool TryParseProceduralElements(string arrayJson, out System.Collections.Generic.List<ProceduralElement> elements)
    {
        elements = null;
        if (string.IsNullOrEmpty(arrayJson))
            return false;
        var list = new System.Collections.Generic.List<ProceduralElement>();
        int i = 0;
        while (i < arrayJson.Length)
        {
            int braceAt = arrayJson.IndexOf('{', i);
            if (braceAt < 0)
                break;
            string obj;
            int endAt;
            if (!ExtractBraced(arrayJson, braceAt, out obj, out endAt))
                return false;
            var el = new ProceduralElement();
            string v;
            bool f;
            if (TryGetStringOrNull(obj, "element", out v, out f) && f)
                el.Element = v;
            if (TryGetStringOrNull(obj, "color", out v, out f) && f)
                el.Color = v;
            if (TryGetStringOrNull(obj, "size", out v, out f) && f)
                el.Size = v;
            if (TryGetStringOrNull(obj, "material", out v, out f) && f)
                el.Material = v;
            if (TryGetStringOrNull(obj, "text", out v, out f) && f)
                el.Text = v;
            list.Add(el);
            i = endAt + 1;
        }
        elements = list;
        return true;
    }

    static bool ExtractBracketed(string json, int bracketAt, out string arr, out int endAt)
    {
        arr = null;
        endAt = -1;
        int depth = 0;
        bool inString = false;
        bool escape = false;
        for (int i = bracketAt; i < json.Length; i++)
        {
            char c = json[i];
            if (inString)
            {
                if (escape)
                    escape = false;
                else if (c == '\\')
                    escape = true;
                else if (c == '"')
                    inString = false;
                continue;
            }
            if (c == '"')
                inString = true;
            else if (c == '[')
                depth++;
            else if (c == ']')
            {
                depth--;
                if (depth == 0)
                {
                    arr = json.Substring(bracketAt, i - bracketAt + 1);
                    endAt = i;
                    return true;
                }
            }
        }
        return false;
    }

    static bool ExtractBraced(string json, int braceAt, out string obj, out int endAt)
    {
        obj = null;
        endAt = -1;
        int depth = 0;
        bool inString = false;
        bool escape = false;
        for (int i = braceAt; i < json.Length; i++)
        {
            char c = json[i];
            if (inString)
            {
                if (escape)
                    escape = false;
                else if (c == '\\')
                    escape = true;
                else if (c == '"')
                    inString = false;
                continue;
            }
            if (c == '"')
                inString = true;
            else if (c == '{')
                depth++;
            else if (c == '}')
            {
                depth--;
                if (depth == 0)
                {
                    obj = json.Substring(braceAt, i - braceAt + 1);
                    endAt = i;
                    return true;
                }
            }
        }
        return false;
    }
}
