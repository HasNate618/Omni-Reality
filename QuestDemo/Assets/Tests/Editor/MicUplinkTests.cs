using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class MicUplinkTests
{
    static void SetConnectRequested(CoordinatorClient client, bool requested)
    {
        typeof(CoordinatorClient)
            .GetField("_beginRequested", BindingFlags.NonPublic | BindingFlags.Instance)
            .SetValue(client, requested);
    }

    static bool CoordinatorIsOpen(CoordinatorClient client)
    {
        var prop = typeof(CoordinatorClient).GetProperty(
            "IsOpen",
            BindingFlags.NonPublic | BindingFlags.Instance);
        return prop != null && (bool)prop.GetValue(client);
    }

    static int OutboxCount(CoordinatorClient client)
    {
        object queue = typeof(CoordinatorClient)
            .GetField("_outbox", BindingFlags.NonPublic | BindingFlags.Instance)
            .GetValue(client);
        return (int)queue.GetType().GetProperty("Count").GetValue(queue);
    }

    static void RunAwake(MicUtterance mic)
    {
        typeof(MicUtterance).GetMethod(
                "Awake",
                BindingFlags.NonPublic | BindingFlags.Instance)
            .Invoke(mic, null);
    }

    static List<short> Chunk(short amplitude)
    {
        var chunk = new List<short>(VoiceActivityGate.ChunkSamples);
        for (int i = 0; i < VoiceActivityGate.ChunkSamples; i++)
            chunk.Add(amplitude);
        return chunk;
    }

    static VoiceActivityGate StartedGate()
    {
        var gate = new VoiceActivityGate();
        gate.Observe(Chunk(0));
        gate.Observe(Chunk(0));
        gate.Observe(Chunk(0));
        gate.Observe(Chunk(1000));
        return gate;
    }

    [Test]
    public void SpeechOnsetReturnsThreeChunkPrerollThenOnset()
    {
        var gate = new VoiceActivityGate();
        gate.Observe(Chunk(0));
        gate.Observe(Chunk(0));
        gate.Observe(Chunk(0));
        VoiceActivityDecision start = gate.Observe(Chunk(1000));
        Assert.AreEqual(VoiceActivityState.Started, start.State);
        Assert.AreEqual(4, start.Chunks.Count);
    }

    [Test]
    public void EightSilentChunksEndAnOpenUtterance()
    {
        var gate = StartedGate();
        for (int i = 0; i < 7; i++)
            Assert.AreEqual(VoiceActivityState.Streaming, gate.Observe(Chunk(0)).State);
        Assert.AreEqual(VoiceActivityState.Ended, gate.Observe(Chunk(0)).State);
    }

    [Test]
    public void EightyChunksEndAnOpenUtteranceEvenWhenSpeechContinues()
    {
        var gate = StartedGate();
        VoiceActivityDecision decision = default;
        for (int i = 0; i < 79; i++)
            decision = gate.Observe(Chunk(1000));
        Assert.AreEqual(VoiceActivityState.Ended, decision.State);
    }

    [Test]
    public void AudioChunkCarriesUtteranceAndPcmShape()
    {
        // 1600 s16le samples (100 ms @ 16 kHz) -> 3200 bytes -> 4268 b64 chars.
        string b64 = System.Convert.ToBase64String(new byte[3200]);
        Assert.AreEqual(4268, b64.Length);
        string json = ProtocolJson.BuildAudioChunk(null, "utt123", b64);
        Assert.IsTrue(json.Contains("\"type\":\"audio_chunk\""));
        Assert.IsTrue(json.Contains("\"utterance_id\":\"utt123\""));
        Assert.IsTrue(json.Contains("\"encoding\":\"pcm_s16le\""));
        Assert.IsTrue(json.Contains("\"sample_rate\":16000"));
        Assert.IsTrue(json.Contains(b64));
    }

    [Test]
    public void UtteranceEndClosesTheId()
    {
        string json = ProtocolJson.BuildUtteranceEnd(null, "utt123");
        Assert.IsTrue(json.Contains("\"type\":\"utterance_end\""));
        Assert.IsTrue(json.Contains("\"utterance_id\":\"utt123\""));
    }

    [Test]
    public void MicUplinkUsesVoiceActivityGateThresholdAndDurations()
    {
        Assert.AreEqual(0.01f, MicUtterance.SilenceRms);
        Assert.AreEqual(16000, MicUtterance.SampleRate);
        Assert.AreEqual(1600, MicUtterance.ChunkSamples);
        Assert.AreEqual(800, MicUtterance.SilenceEndMs);
        Assert.AreEqual(8000, MicUtterance.MaxUtteranceMs);
    }

    [Test]
    public void PlayerReportsPlaybackState()
    {
        Assert.IsNotNull(typeof(SpeakCloudPlayer).GetProperty("IsPlaying"));
    }

    [Test]
    public void AutoVadOpensOnlyWhenSocketConnectedAndPlaybackInactive()
    {
        var go = new GameObject("MicVadGate");
        var client = go.AddComponent<CoordinatorClient>();
        var player = go.AddComponent<SpeakCloudPlayer>();
        typeof(SpeakCloudPlayer).GetMethod(
                "Awake",
                BindingFlags.NonPublic | BindingFlags.Instance)
            .Invoke(player, null);
        typeof(CoordinatorClient)
            .GetField("SpeakPlayer", BindingFlags.NonPublic | BindingFlags.Instance)
            .SetValue(client, player);
        var mic = go.AddComponent<MicUtterance>();
        RunAwake(mic);
        SetConnectRequested(client, true);
        Assert.IsFalse(CoordinatorIsOpen(client));
        Assert.IsTrue((bool)typeof(CoordinatorClient)
            .GetField("_beginRequested", BindingFlags.NonPublic | BindingFlags.Instance)
            .GetValue(client));

        var canOpen = typeof(MicUtterance).GetMethod(
            "CanOpenUtterance",
            BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.IsNotNull(canOpen);
        Assert.IsFalse((bool)canOpen.Invoke(mic, null));

        Object.DestroyImmediate(go);
    }

    [Test]
    public void ToggleListeningFlipsDefaultOnAndStaysSafeOffline()
    {
        var go = new GameObject("MicToggleListen");
        var client = go.AddComponent<CoordinatorClient>();
        var mic = go.AddComponent<MicUtterance>();
        RunAwake(mic);
        var toggle = typeof(MicUtterance).GetMethod(
            "ToggleListening", BindingFlags.NonPublic | BindingFlags.Instance);
        var flag = typeof(MicUtterance).GetField(
            "_listening", BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.IsNotNull(toggle);
        Assert.IsNotNull(flag);
        Assert.IsFalse((bool)flag.GetValue(mic));
        toggle.Invoke(mic, null);
        Assert.IsTrue((bool)flag.GetValue(mic));
        Assert.IsNull(mic.UtteranceId);
        toggle.Invoke(mic, null);
        Assert.IsFalse((bool)flag.GetValue(mic));
        Assert.IsNull(mic.UtteranceId);
        Assert.AreEqual(0, OutboxCount(client));
        Object.DestroyImmediate(go);
    }

    [Test]
    public void AudioChunkAndUtteranceEndDroppedWhileOffline()
    {
        var go = new GameObject("CoordOffline");
        var client = go.AddComponent<CoordinatorClient>();
        SetConnectRequested(client, true);
        Assert.IsFalse(CoordinatorIsOpen(client));

        string b64 = System.Convert.ToBase64String(new byte[3200]);
        client.EnqueueAudioChunk("utt-offline", b64);
        Assert.AreEqual(0, OutboxCount(client));

        client.OpenUtteranceId = "utt-offline";
        client.EnqueueUtteranceEnd("utt-offline");
        Assert.AreEqual(0, OutboxCount(client));
        Assert.IsNull(client.OpenUtteranceId);

        Object.DestroyImmediate(go);
    }
}
