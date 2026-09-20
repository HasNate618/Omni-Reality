using System;
using NUnit.Framework;
using UnityEngine;

public class PerceptionCaptureTests
{
    [TestCase(1280, 960, 640, 480)]
    [TestCase(960, 1280, 480, 640)]
    [TestCase(320, 240, 320, 240)]
    public void ResizePreservesAspectWithoutUpscaling(int w, int h, int sw, int sh)
    {
        Assert.AreEqual(new Vector2Int(sw, sh), PerceptionImage.FitSize(w, h));
    }

    [Test]
    public void SentPixelCentresMapBackToOriginalCameraPixels()
    {
        var env = new CaptureEnvelope { ImageW = 1280, ImageH = 960, Fx = 657.5f, PosePy = 1.6f, TUnixNs = 123 };
        PerceptionImage.SetSentSize(env, new Vector2Int(640, 480));
        Assert.AreEqual(640, env.SentW);
        Assert.AreEqual(480, env.SentH);
        Assert.AreEqual(0.5f, env.CropSx * 0f + env.CropTx);
        Assert.AreEqual(1278.5f, env.CropSx * 639f + env.CropTx);
        Assert.AreEqual(958.5f, env.CropSy * 479f + env.CropTy);
        Assert.AreEqual(657.5f, env.Fx, "intrinsics remain in original pixels");
        Assert.AreEqual(123, env.TUnixNs, "encoding must not replace capture time");
        Assert.AreEqual(1.6f, env.PosePy);
    }

    [Test]
    public void EncodeKeepsImageCornerOrientationAndSize()
    {
        // Unity pixels are bottom-left first: red bottom, blue top.
        var pixels = new Color32[64 * 48];
        for (int y = 0; y < 48; y++)
            for (int x = 0; x < 64; x++)
                pixels[y * 64 + x] = y < 24 ? new Color32(255, 0, 0, 255) : new Color32(0, 0, 255, 255);
        var jpeg = PerceptionImage.Encode(pixels, 64, 48, out var size);
        Assert.IsNotNull(jpeg);
        var decoded = new Texture2D(2, 2);
        try
        {
            Assert.IsTrue(decoded.LoadImage(jpeg));
            Assert.AreEqual(new Vector2Int(64, 48), size);
            Assert.AreEqual(64, decoded.width);
            Assert.Greater(decoded.GetPixel(8, 8).r, 0.8f);
            Assert.Greater(decoded.GetPixel(8, 40).b, 0.8f);
        }
        finally { UnityEngine.Object.DestroyImmediate(decoded); }
    }

    [Test]
    public void NoisyImageNeverExceedsUploadCap()
    {
        var random = new System.Random(76);
        var pixels = new Color32[1280 * 960];
        for (int i = 0; i < pixels.Length; i++)
            pixels[i] = new Color32((byte)random.Next(256), (byte)random.Next(256), (byte)random.Next(256), 255);
        var jpeg = PerceptionImage.Encode(pixels, 1280, 960, out var size);
        Assert.AreEqual(new Vector2Int(640, 480), size);
        Assert.IsTrue(jpeg == null || jpeg.Length <= 65536);
    }

    [Test]
    public void CameraFreshnessAndDarkFrameHaveExplicitGates()
    {
        var now = DateTime.UtcNow;
        Assert.IsTrue(PerceptionImage.IsFresh(now.AddMilliseconds(-100), now));
        Assert.IsFalse(PerceptionImage.IsFresh(now.AddSeconds(-1), now));
        Assert.IsFalse(PerceptionImage.IsFresh(DateTime.MinValue, now));
        Assert.IsTrue(PerceptionImage.IsDark(new Color32[20]));
        Assert.IsFalse(PerceptionImage.IsDark(new[] { new Color32(255, 255, 255, 255) }));
    }

    [Test]
    public void TimeoutDropsLateReadbackAndPreventsOverlappingNativeRequests()
    {
        var gate = new PerceptionCaptureGate();
        int ticket = gate.Begin(1f);
        Assert.Greater(ticket, 0);
        Assert.IsTrue(gate.StartReadback(ticket));
        Assert.AreEqual(0, gate.Begin(1.1f));
        Assert.IsTrue(gate.Expire(1.76f));
        Assert.AreEqual(0, gate.Begin(2f), "native request still owns its texture readback");
        Assert.IsFalse(gate.AcceptResult(ticket, 2f));
        Assert.Greater(gate.Begin(2.1f), ticket);
    }

    [Test]
    public void MissingCameraCompletesOnceAndCancelNeverPublishes()
    {
        var go = new GameObject("CaptureLifecycle");
        try
        {
            var capture = go.AddComponent<PerceptionCapture>();
            var tick = typeof(PerceptionCapture).GetMethod("LateUpdate", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            int calls = 0;
            capture.Capture((env, jpeg, reason) =>
            {
                calls++;
                Assert.IsNull(env);
                Assert.IsNull(jpeg);
                Assert.AreEqual("camera_down", reason);
            });
            tick.Invoke(capture, null);
            tick.Invoke(capture, null);
            Assert.AreEqual(1, calls);
            capture.Capture((env, jpeg, reason) => calls++);
            capture.Cancel();
            tick.Invoke(capture, null);
            Assert.AreEqual(1, calls);
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void CancelAndDuplicateCallbacksCannotPublishAnOldFrame()
    {
        var gate = new PerceptionCaptureGate();
        int old = gate.Begin(1f);
        Assert.IsTrue(gate.StartReadback(old));
        gate.Cancel();
        Assert.IsFalse(gate.AcceptResult(old, 1.1f));
        int current = gate.Begin(1.2f);
        Assert.IsTrue(gate.StartReadback(current));
        Assert.IsFalse(gate.AcceptResult(old, 1.3f));
        Assert.IsTrue(gate.AcceptResult(current, 1.4f));
        Assert.IsFalse(gate.AcceptResult(current, 1.5f));
    }
}
