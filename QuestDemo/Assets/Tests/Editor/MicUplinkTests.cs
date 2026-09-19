using NUnit.Framework;

public class MicUplinkTests
{
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
