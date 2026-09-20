using NUnit.Framework;

public class SpeakCloudPlayerTests
{
    [Test]
    public void TryDecodePcmBase64_AcceptsMinimalSample()
    {
        byte[] raw = { 0x00, 0x00, 0xFF, 0x7F };
        string b64 = System.Convert.ToBase64String(raw);
        byte[] pcm;
        Assert.IsTrue(SpeakCloudPlayer.TryDecodePcmBase64(b64, out pcm));
        Assert.AreEqual(4, pcm.Length);
    }

    [Test]
    public void TryParseSpeak_ReadsAudioBlock()
    {
        string payload =
            "{\"turn_id\":2,\"text\":\"Hi\",\"audio\":{\"encoding\":\"pcm_s16le\",\"sample_rate\":16000,\"channels\":1,\"data_b64\":\"AAASAA==\"}}";
        ProtocolJson.SpeakMsg msg;
        Assert.IsTrue(ProtocolJson.TryParseSpeak(payload, out msg));
        Assert.AreEqual(2, msg.TurnId);
        Assert.AreEqual("Hi", msg.Text);
        Assert.AreEqual("pcm_s16le", msg.AudioEncoding);
        Assert.IsFalse(string.IsNullOrEmpty(msg.AudioDataB64));
    }
}
