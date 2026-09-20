using System;
using System.Collections;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class GuidePresentationTests
{
    const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.NonPublic;
    GameObject _go;
    CoordinatorClient _client;

    [SetUp]
    public void SetUp()
    {
        _go = new GameObject("GuideClientTest");
        _client = _go.AddComponent<CoordinatorClient>();
        Set("_sessionId", "session");
        Set("_activeUtterance", "seed");
    }

    [TearDown]
    public void TearDown() { UnityEngine.Object.DestroyImmediate(_go); }

    void Set(string name, object value) { typeof(CoordinatorClient).GetField(name, Hidden).SetValue(_client, value); }
    void Receive(string json) { typeof(CoordinatorClient).GetMethod("HandleMessage", Hidden).Invoke(_client, new object[] { json }); }
    static void Call(object target, string method, object value)
    {
        target.GetType().GetMethod(method, Hidden).Invoke(target, new[] { value });
    }
    static GuideStep Step(int index = 0, int[] ids = null)
    {
        return new GuideStep { guide_id = "desk", generation = 1, step_index = index,
            total_steps = 3, active_obj_ids = ids ?? new[] { 2, 1 }, instruction = "Move the mug." };
    }
    void ReceiveStep(GuideStep step, string utterance = "seed", string session = "session")
    {
        Receive("{\"type\":\"guide_step\",\"session_id\":\"" + session
            + "\",\"utterance_id\":\"" + utterance + "\",\"turn_id\":1,\"payload\":"
            + JsonUtility.ToJson(step) + "}");
    }
    void ReceiveMask(int generation = 1)
    {
        Receive("{\"type\":\"tracking_result\",\"session_id\":\"session\",\"utterance_id\":\"seed\","
            + "\"turn_id\":1,\"payload\":{\"generation\":" + generation
            + ",\"stage_epoch\":1,\"frame_id\":\"frame\",\"objects\":[]}}");
    }

    [Test]
    public void CommandsPreserveOriginalTrackingIdentityAndRepeatIsDelivered()
    {
        int steps = 0, masks = 0;
        _client.GuideStepReceived += _ => steps++;
        _client.TrackingResultReceived += _ => masks++;
        ReceiveStep(Step());
        Set("_activeUtterance", "next-command");
        ReceiveStep(Step(1, new[] { 3, 1 }), "next-command");
        ReceiveStep(Step(1, new[] { 3, 1 }), "repeat-command");
        ReceiveMask();
        Assert.AreEqual(3, steps);
        Assert.AreEqual(1, masks);
        Assert.AreEqual(1, _client.ActiveGuideStep.step_index);
    }

    [Test]
    public void RejectsOtherSessionMalformedAndStaleGuideSteps()
    {
        ReceiveStep(Step(), session: "old-session");
        Assert.IsNull(_client.ActiveGuideStep);
        ReceiveStep(Step(ids: new[] { 2, 2 }));
        Assert.IsNull(_client.ActiveGuideStep);
        var step = Step();
        ReceiveStep(step);
        step.generation = 0;
        ReceiveStep(step);
        Assert.AreEqual(1, _client.ActiveGuideStep.generation);
    }

    [Test]
    public void FinishedGuideFencesLateMasksAndCannotBeResurrected()
    {
        int masks = 0, finished = 0;
        _client.TrackingResultReceived += _ => masks++;
        _client.GuideFinishedReceived += _ => finished++;
        ReceiveStep(Step());
        Receive("{\"type\":\"guide_finished\",\"session_id\":\"session\",\"payload\":{"
            + "\"guide_id\":\"desk\",\"generation\":1,\"reason\":\"completed\"}}");
        ReceiveMask();
        ReceiveStep(Step());
        Assert.IsNull(_client.ActiveGuideStep);
        Assert.AreEqual(0, masks);
        Assert.AreEqual(1, finished);
    }

    [Test]
    public void LocalStopAndDisconnectClearGuide()
    {
        ReceiveStep(Step());
        _client.StopTracking();
        Assert.IsNull(_client.ActiveGuideStep);
        typeof(CoordinatorClient).GetMethod("CleanupSocket", Hidden).Invoke(_client, null);
        Set("_sessionId", "session");
        ReceiveStep(Step());
        Assert.IsNotNull(_client.ActiveGuideStep, "reconnect permits restarted generations");
        typeof(CoordinatorClient).GetMethod("CleanupSocket", Hidden).Invoke(_client, null);
        Assert.IsNull(_client.ActiveGuideStep);
    }

    [Test]
    public void ExistingLayersSwitchImmediatelyWithoutNewTrackingFrame()
    {
        var overlayGo = new GameObject("GuideOverlayTest");
        var overlay = overlayGo.AddComponent<TrackingMaskOverlay>();
        overlay.ShowLabels = false;
        try
        {
            Call(overlay, "OnGuideStep", Step());
            var layerFor = typeof(TrackingMaskOverlay).GetMethod("LayerFor", Hidden);
            var seen = (System.Collections.Generic.HashSet<int>)typeof(TrackingMaskOverlay)
                .GetField("_seen", Hidden).GetValue(overlay);
            for (int id = 1; id <= 3; id++) { layerFor.Invoke(overlay, new object[] { id }); seen.Add(id); }
            Call(overlay, "OnGuideStep", Step());
            var layers = (IDictionary)typeof(TrackingMaskOverlay).GetField("_layers", Hidden).GetValue(overlay);
            AssertLayer(layers[2], true, 1f);
            AssertLayer(layers[1], true, 0.35f);
            AssertLayer(layers[3], false, 0f);
            Call(overlay, "OnGuideStep", Step(1, new[] { 3, 1 }));
            AssertLayer(layers[2], false, 0f);
            AssertLayer(layers[3], true, 1f);
            Call(overlay, "OnGuideFinished", new GuideFinished { guide_id = "desk", generation = 1 });
            foreach (object layer in layers.Values) Assert.IsFalse(Quad(layer).activeSelf);
            // Manually dispose test resources synchronously; runtime uses deferred Destroy.
            foreach (object layer in layers.Values)
            {
                var quad = Quad(layer);
                UnityEngine.Object.DestroyImmediate(quad.GetComponent<MeshFilter>().sharedMesh);
                UnityEngine.Object.DestroyImmediate(quad.GetComponent<MeshRenderer>().sharedMaterial);
                UnityEngine.Object.DestroyImmediate(quad);
            }
            layers.Clear();
        }
        finally { UnityEngine.Object.DestroyImmediate(overlayGo); }
    }

    static GameObject Quad(object layer) { return (GameObject)layer.GetType().GetField("Quad").GetValue(layer); }
    static void AssertLayer(object layer, bool visible, float alpha)
    {
        Assert.AreEqual(visible, Quad(layer).activeSelf);
        Assert.AreEqual(alpha, Quad(layer).GetComponent<MeshRenderer>().sharedMaterial.color.a, 0.01f);
    }
}
