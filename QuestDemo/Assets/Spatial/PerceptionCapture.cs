using System;
using Meta.XR;
using UnityEngine;
using UnityEngine.Rendering;

/// <summary>
/// One requested PCA frame, never a video loop. LateUpdate snapshots the SDK
/// metadata and AsyncGPUReadback follows its native texture update in render
/// order. A blocking Blit/ReadPixels would read the previous image (SDK warning).
/// </summary>
[DefaultExecutionOrder(10000)]
public class PerceptionCapture : MonoBehaviour
{
    internal PassthroughCameraAccess CameraSource;
    internal SpatialRuntime Runtime;
    readonly PerceptionCaptureGate _gate = new PerceptionCaptureGate();
    Action<CaptureEnvelope, byte[], string> _complete;
    int _ticket;

    public void Capture(Action<CaptureEnvelope, byte[], string> complete)
    {
        int ticket = _gate.Begin(Time.realtimeSinceStartup);
        if (ticket == 0)
        {
            complete(null, null, "capture_busy");
            return;
        }
        _ticket = ticket;
        _complete = complete;
    }

    public void Cancel()
    {
        _gate.Cancel();
        _complete = null;
    }

    void OnDisable() { Cancel(); }

    void LateUpdate()
    {
        if (_gate.Expire(Time.realtimeSinceStartup))
        {
            Finish(null, null, "capture_timeout");
            return;
        }
        if (!_gate.Active || _gate.ReadbackPending) return;
        if (CameraSource == null || !CameraSource.IsPlaying || Runtime == null)
        {
            Finish(null, null, "camera_down");
            return;
        }
        if (!CameraSource.IsUpdatedThisFrame) return;
        if (!PerceptionImage.IsFresh(CameraSource.Timestamp, DateTime.UtcNow))
        {
            Finish(null, null, "camera_stale");
            return;
        }
        if (!SystemInfo.supportsAsyncGPUReadback)
        {
            Finish(null, null, "readback_unsupported");
            return;
        }
        try
        {
            if (!Runtime.TryCapture(out var env, out _, out _, requireDepth: false, includePointing: false))
            {
                Finish(null, null, "geometry_unavailable");
                return;
            }
            var source = CameraSource.GetTexture();
            if (source == null || source.width != env.ImageW || source.height != env.ImageH)
            {
                Finish(null, null, "texture_unavailable");
                return;
            }
            int ticket = _ticket;
            _gate.StartReadback(ticket);
            AsyncGPUReadback.Request(source, 0, TextureFormat.RGBA32,
                request => OnReadback(ticket, env, request));
        }
        catch (Exception e)
        {
            _gate.AcceptResult(_ticket, Time.realtimeSinceStartup);
            VoiceBootstrapLog.Log("PerceptionCapture", "capture_failed", ("exception_type", e.GetType().Name));
            Finish(null, null, "readback_failed");
        }
    }

    void OnReadback(int ticket, CaptureEnvelope env, AsyncGPUReadbackRequest request)
    {
        if (!_gate.AcceptResult(ticket, Time.realtimeSinceStartup)) return;
        if (this == null || !isActiveAndEnabled) return;
        try
        {
            if (request.hasError)
            {
                Finish(null, null, "readback_failed");
                return;
            }
            if (Runtime == null || env.StageEpoch != Runtime.StageEpoch)
            {
                Finish(null, null, "stage_changed");
                return;
            }
            var pixels = request.GetData<Color32>().ToArray();
            if (PerceptionImage.IsDark(pixels))
            {
                Finish(null, null, "dark_image");
                return;
            }
            byte[] jpeg = PerceptionImage.Encode(pixels, env.ImageW, env.ImageH, out var size);
            if (jpeg == null)
            {
                Finish(null, null, "encode_over_cap");
                return;
            }
            PerceptionImage.SetSentSize(env, size);
            Runtime.UpdateSentGeometry(env);
            VoiceBootstrapLog.Log("PerceptionCapture", "frame_captured",
                ("jpeg_bytes", jpeg.Length), ("width", size.x), ("height", size.y));
            Finish(env, jpeg, null);
        }
        catch (Exception e)
        {
            VoiceBootstrapLog.Log("PerceptionCapture", "capture_failed", ("exception_type", e.GetType().Name));
            Finish(null, null, "encode_failed");
        }
    }

    void Finish(CaptureEnvelope env, byte[] jpeg, string reason)
    {
        _gate.Cancel();
        var complete = _complete;
        _complete = null;
        if (reason != null)
            VoiceBootstrapLog.Log("PerceptionCapture", "frame_dropped", ("reason", reason));
        complete?.Invoke(env, jpeg, reason);
    }
}
