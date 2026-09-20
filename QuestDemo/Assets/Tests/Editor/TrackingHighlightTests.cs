using System;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class TrackingHighlightTests
{
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    static void Invoke(CoordinatorClient client, string name, params object[] args) =>
        typeof(CoordinatorClient).GetMethod(name, Private).Invoke(client, args);

    static void WireOverlay(CoordinatorClient client, TrackingMaskOverlay overlay) =>
        typeof(CoordinatorClient).GetField("TrackingOverlay", Private).SetValue(client, overlay);

    static bool Streaming(CoordinatorClient client) =>
        (bool)typeof(CoordinatorClient).GetProperty("TrackStreaming", Private | BindingFlags.Public | BindingFlags.Instance).GetValue(client);

    static byte[] WhitePng(int size = 8)
    {
        var tex = new Texture2D(size, size, TextureFormat.RGBA32, false);
        var pixels = new Color32[size * size];
        for (int i = 0; i < pixels.Length; i++)
            pixels[i] = new Color32(255, 255, 255, 255);
        tex.SetPixels32(pixels);
        tex.Apply();
        byte[] png = tex.EncodeToPNG();
        UnityEngine.Object.DestroyImmediate(tex);
        return png;
    }

    static string StatusJson(string state, int generation, string text)
    {
        return "{\"v\":1,\"type\":\"tracking_status\",\"session_id\":\"s1\",\"turn_id\":7,"
            + "\"utterance_id\":\"u7\",\"payload\":{\"state\":\"" + state + "\",\"text\":\""
            + text + "\",\"generation\":" + generation + "}}";
    }

    static string ResultJson(int generation, string maskB64, int envelopeStage = -1)
    {
        string objects = maskB64 == null ? "[]" :
            "[{\"obj_id\":1,\"mask_b64\":\"" + maskB64 + "\"}]";
        string envelope = envelopeStage < 0 ? "{}" : "{\"stage_epoch\":" + envelopeStage + "}";
        return "{\"v\":1,\"type\":\"tracking_result\",\"session_id\":\"s1\",\"turn_id\":7,"
            + "\"utterance_id\":\"u7\",\"payload\":{\"frame_id\":\"f1\",\"seed_frame_id\":\"f0\","
            + "\"generation\":" + generation + ",\"stage_epoch\":3,\"width\":8,\"height\":8,"
            + "\"objects\":" + objects + ",\"envelope\":" + envelope + "}}";
    }

    [Test]
    public void TrackingStatusParsesAndRequiresState()
    {
        ProtocolJson.TrackingStatusMsg status;
        Assert.IsTrue(ProtocolJson.TryParseTrackingStatus(
            "{\"state\":\"tracking\",\"text\":\"SAM 2 is running.\",\"generation\":2}", out status));
        Assert.AreEqual("tracking", status.State);
        Assert.AreEqual(2, status.Generation);
        Assert.IsFalse(ProtocolJson.TryParseTrackingStatus("{\"text\":\"x\"}", out status));
        Assert.IsFalse(ProtocolJson.TryParseTrackingStatus(null, out status));
    }

    [Test]
    public void TrackingResultParsesFirstMaskAndToleratesLoss()
    {
        string mask = Convert.ToBase64String(WhitePng());
        ProtocolJson.TrackingResultMsg result;
        Assert.IsTrue(ProtocolJson.TryParseTrackingResult(
            "{\"frame_id\":\"f1\",\"width\":8,\"height\":8,\"objects\":[{\"obj_id\":1,"
            + "\"mask_b64\":\"" + mask + "\"}]}", out result));
        Assert.AreEqual("f1", result.FrameId);
        Assert.AreEqual(mask, result.MaskB64);
        Assert.IsTrue(ProtocolJson.TryParseTrackingResult(
            "{\"frame_id\":\"f1\",\"objects\":[]}", out result));
        Assert.IsNull(result.MaskB64);
        Assert.IsFalse(ProtocolJson.TryParseTrackingResult("{\"objects\":[]}", out result));
    }

    [Test]
    public void TrackingFrameCarriesFlagWithoutUtterance()
    {
        var env = new CaptureEnvelope { FrameId = "01k5j8g0008q3m7b2d6h9n4r5v", ImageW = 640, ImageH = 480, SentW = 640, SentH = 480 };
        string json = ProtocolJson.BuildTrackingFrame("s1", env, "/9j/2Q==");
        Assert.That(json, Does.Contain("\"type\":\"frame\""));
        Assert.That(json, Does.Contain("\"tracking\":true"));
        Assert.That(json, Does.Contain("\"utterance_id\":null"));
        Assert.That(json, Does.Contain("\"jpeg_b64\":\"/9j/2Q==\""));
    }

    [Test]
    public void OverlayHoldsTenSecondsThenFadesAndHides()
    {
        var go = new GameObject("OverlayHold");
        try
        {
            var overlay = go.AddComponent<TrackingMaskOverlay>();
            overlay.Show(WhitePng(), 8, 8);
            Assert.IsTrue(overlay.IsVisible);
            Assert.AreEqual(1f, overlay.CurrentAlpha);
            overlay.Tick(9.9f);
            Assert.IsTrue(overlay.IsVisible);
            Assert.AreEqual(1f, overlay.CurrentAlpha);
            overlay.Tick(0.2f);
            Assert.IsTrue(overlay.IsVisible);
            Assert.Less(overlay.CurrentAlpha, 1f);
            overlay.Tick(1f);
            Assert.IsFalse(overlay.IsVisible);
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void OverlayReplaceRestartsClockAndBadMasksHide()
    {
        var go = new GameObject("OverlayReplace");
        try
        {
            var overlay = go.AddComponent<TrackingMaskOverlay>();
            overlay.Show(WhitePng(), 8, 8);
            overlay.Tick(9f);
            overlay.Show(WhitePng(), 8, 8);
            Assert.AreEqual(1f, overlay.CurrentAlpha);
            overlay.Tick(9.5f);
            Assert.IsTrue(overlay.IsVisible);
            overlay.Show(null, 8, 8);
            Assert.IsFalse(overlay.IsVisible);
            overlay.Show(new byte[] { 1, 2, 3 }, 8, 8);
            Assert.IsFalse(overlay.IsVisible);
            overlay.Show(WhitePng(), 8, 8);
            overlay.Hide();
            Assert.IsFalse(overlay.IsVisible);
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void StatusFlipsStreamingAndResultPaintsOverlay()
    {
        var go = new GameObject("TrackClient");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var overlay = go.AddComponent<TrackingMaskOverlay>();
            WireOverlay(client, overlay);
            Assert.IsFalse(Streaming(client));
            Invoke(client, "HandleMessage", StatusJson("selecting", 0, "Selecting."));
            Assert.IsTrue(Streaming(client));
            Invoke(client, "HandleMessage", StatusJson("tracking", 0, "Running."));
            Assert.IsTrue(Streaming(client));
            Invoke(client, "HandleMessage", ResultJson(0, Convert.ToBase64String(WhitePng())));
            Assert.IsTrue(overlay.IsVisible);
            Invoke(client, "HandleMessage", ResultJson(0, null));
            Assert.IsFalse(overlay.IsVisible);
            Invoke(client, "HandleMessage", StatusJson("stopped", 0, "Done."));
            Assert.IsFalse(Streaming(client));
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void DisconnectClearsStreamingAndHidesOverlay()
    {
        var go = new GameObject("TrackCleanup");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var overlay = go.AddComponent<TrackingMaskOverlay>();
            WireOverlay(client, overlay);
            Invoke(client, "HandleMessage", StatusJson("tracking", 0, "Running."));
            Invoke(client, "HandleMessage", ResultJson(0, Convert.ToBase64String(WhitePng())));
            Assert.IsTrue(overlay.IsVisible);
            Invoke(client, "CleanupSocket");
            Assert.IsFalse(Streaming(client));
            Assert.IsFalse(overlay.IsVisible);
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void OpenUtterancePausesTrackingStream()
    {
        var go = new GameObject("TrackPause");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var capture = go.AddComponent<PerceptionCapture>();
            typeof(CoordinatorClient).GetField("TrackCapture", Private).SetValue(client, capture);
            var field = typeof(CoordinatorClient).GetField("_lastTrackFrameAt", Private);
            client.OpenUtteranceId = "u-open";
            Invoke(client, "HandleMessage", StatusJson("tracking", 0, "Running."));
            Invoke(client, "PumpTrackingStream", 1000f);
            Assert.AreEqual(-1000f, (float)field.GetValue(client));
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void StageMismatchHidesInsteadOfPainting()
    {
        var go = new GameObject("TrackStage");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var overlay = go.AddComponent<TrackingMaskOverlay>();
            WireOverlay(client, overlay);
            Invoke(client, "HandleMessage", StatusJson("tracking", 2, "Running."));
            Invoke(client, "HandleMessage", ResultJson(2, Convert.ToBase64String(WhitePng()), 99));
            Assert.IsFalse(overlay.IsVisible);
            Assert.IsFalse(Streaming(client));
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void StaleGenerationsNeverPaint()
    {
        var go = new GameObject("TrackStale");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var overlay = go.AddComponent<TrackingMaskOverlay>();
            WireOverlay(client, overlay);
            Invoke(client, "HandleMessage", StatusJson("tracking", 3, "Running."));
            Invoke(client, "HandleMessage", ResultJson(1, Convert.ToBase64String(WhitePng())));
            Assert.IsFalse(overlay.IsVisible);
            Invoke(client, "HandleMessage", ResultJson(3, Convert.ToBase64String(WhitePng())));
            Assert.IsTrue(overlay.IsVisible);
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }
}
