using System;
using System.Net.WebSockets;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class PerceptionTransportTests
{
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    static object Queue(CoordinatorClient client) => typeof(CoordinatorClient).GetField("_outbox", Private).GetValue(client);
    static void Enqueue(object queue, int priority, string text) => queue.GetType().GetMethod("Enqueue").Invoke(queue, new object[] { priority, text });
    static string Next(object queue)
    {
        var args = new object[] { null };
        bool ok = (bool)queue.GetType().GetMethod("TryDequeue").Invoke(queue, args);
        return ok ? (string)args[0] : null;
    }
    static void Invoke(CoordinatorClient client, string name, params object[] args) => typeof(CoordinatorClient).GetMethod(name, Private).Invoke(client, args);

    [Test]
    public void EndCannotOvertakeAudioOrImageButAckAndCancelCan()
    {
        var go = new GameObject("OrderedMedia");
        try
        {
            var queue = Queue(go.AddComponent<CoordinatorClient>());
            Enqueue(queue, CoordinatorClient.PriorityAudioChunk, "audio1");
            Enqueue(queue, CoordinatorClient.PriorityAudioChunk, "audio2");
            Enqueue(queue, CoordinatorClient.PriorityFrame, "image");
            Enqueue(queue, CoordinatorClient.PriorityUtteranceEnd, "end");
            Enqueue(queue, CoordinatorClient.PriorityAck, "ack");
            Enqueue(queue, CoordinatorClient.PriorityCancel, "cancel");
            CollectionAssert.AreEqual(new[] { "cancel", "ack", "audio1", "audio2", "image", "end" },
                new[] { Next(queue), Next(queue), Next(queue), Next(queue), Next(queue), Next(queue) });
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void DisconnectDiscardsQueuedMediaAndOpenUtterance()
    {
        var go = new GameObject("DropMedia");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var queue = Queue(client);
            Enqueue(queue, CoordinatorClient.PriorityAudioChunk, "private audio");
            Enqueue(queue, CoordinatorClient.PriorityFrame, "private image");
            client.OpenUtteranceId = "old-turn";
            Invoke(client, "CleanupSocket");
            Assert.IsNull(Next(queue));
            Assert.IsNull(client.OpenUtteranceId);
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void FastCloseBeforeFirstTickDoesNotBlockReconnect()
    {
        var go = new GameObject("FastClose");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            typeof(CoordinatorClient).GetField("_socket", Private).SetValue(client, new ClientWebSocket());
            typeof(CoordinatorClient).GetField("_socketDown", Private).SetValue(client, true);
            Invoke(client, "Begin", "127.0.0.1");
            Invoke(client, "Tick");
            Assert.IsNull(typeof(CoordinatorClient).GetField("_socket", Private).GetValue(client));
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void ImageCaptureRequiresExplicitHelloCapability()
    {
        var go = new GameObject("Capability");
        try
        {
            var client = go.AddComponent<CoordinatorClient>();
            var property = typeof(CoordinatorClient).GetProperty("PerceptionEnabled");
            Assert.IsNotNull(property, "capture must be negotiated, not enabled in voice-only");
            Assert.IsFalse((bool)property.GetValue(client));
            Invoke(client, "HandleMessage", "{\"type\":\"hello_ok\",\"payload\":{\"session_id\":\"s1\",\"perception_qa\":true}}");
            Assert.IsTrue((bool)property.GetValue(client));
            Invoke(client, "CleanupSocket");
            Assert.IsFalse((bool)property.GetValue(client));
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    [Test]
    public void JpegFrameCarriesItsExplicitUtteranceAndGeometry()
    {
        var build = typeof(ProtocolJson).GetMethod("BuildFrame", new[] { typeof(string), typeof(CaptureEnvelope), typeof(string), typeof(string) });
        Assert.IsNotNull(build);
        var env = new CaptureEnvelope { FrameId = "01k5j8g0008q3m7b2d6h9n4r5v", ImageW = 1280, ImageH = 960, SentW = 640, SentH = 480 };
        string json = (string)build.Invoke(null, new object[] { "s1", env, "closing-turn", "/9j/2Q==" });
        Assert.That(json, Does.Contain("\"utterance_id\":\"closing-turn\""));
        Assert.That(json, Does.Contain("\"jpeg_b64\":\"/9j/2Q==\""));
        Assert.That(json, Does.Contain("\"sent_w\":640"));
        Assert.That(json, Does.Contain("\"image_w\":1280"));
    }
}
