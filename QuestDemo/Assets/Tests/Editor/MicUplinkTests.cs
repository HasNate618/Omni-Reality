using System.Collections.Generic;
using NUnit.Framework;

public class MicUplinkTests
{
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
}
