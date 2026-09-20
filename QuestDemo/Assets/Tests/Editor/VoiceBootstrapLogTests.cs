using NUnit.Framework;

public class VoiceBootstrapLogTests
{
    [Test]
    public void FormatLine_RejectsCaptionTextField()
    {
        Assert.Throws<System.ArgumentException>(() =>
            VoiceBootstrapLog.FormatLine(
                VoiceBootstrapLog.ComponentSpeak,
                "playback_accepted",
                ("caption", "Hello world")));
    }

    [Test]
    public void FormatLine_RejectsBase64PayloadField()
    {
        string b64 = new string('A', 48);
        Assert.Throws<System.ArgumentException>(() =>
            VoiceBootstrapLog.FormatLine(
                VoiceBootstrapLog.ComponentCoordinator,
                "audio_drop_offline",
                ("data_b64", b64)));
    }

    [Test]
    public void FormatLine_AllowsCountFieldsOnly()
    {
        string line = VoiceBootstrapLog.FormatLine(
            VoiceBootstrapLog.ComponentMic,
            "vad_ended",
            ("sent_chunks", 12),
            ("pcm_bytes", 38400));
        Assert.IsTrue(line.StartsWith("VoiceBootstrap "));
        Assert.IsTrue(line.Contains("event=vad_ended"));
        Assert.IsTrue(line.Contains("pcm_bytes=38400"));
        Assert.IsFalse(line.Contains("AAAA"));
    }

    [Test]
    public void PcmBytesFromBase64_MatchesDecodedLengthWithoutLoggingPayload()
    {
        byte[] raw = new byte[3200];
        string b64 = System.Convert.ToBase64String(raw);
        Assert.AreEqual(3200, VoiceBootstrapLog.PcmBytesFromBase64(b64));
        string line = VoiceBootstrapLog.FormatLine(
            VoiceBootstrapLog.ComponentCoordinator,
            "audio_drop_offline",
            ("pcm_bytes", VoiceBootstrapLog.PcmBytesFromBase64(b64)));
        Assert.IsFalse(line.Contains(b64));
    }

    [Test]
    public void IsAllowedField_RejectsTranscriptLikeStrings()
    {
        Assert.IsFalse(VoiceBootstrapLog.IsAllowedField("text", "mark the laptop"));
        Assert.IsFalse(VoiceBootstrapLog.IsAllowedField("reason", "this reason has spaces"));
    }
}
