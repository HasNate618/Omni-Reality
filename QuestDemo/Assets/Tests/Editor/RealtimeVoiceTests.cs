using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class RealtimeVoiceTests
{
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    static List<short> LoudChunk()
    {
        var chunk = new List<short>(1600);
        for (int i = 0; i < 1600; i++) chunk.Add(3000);
        return chunk;
    }

    static List<short> QuietChunk()
    {
        var chunk = new List<short>(1600);
        for (int i = 0; i < 1600; i++) chunk.Add(100);
        return chunk;
    }

    [Test]
    public void SpeakChunkAndFinalParse()
    {
        string chunk = "{\"turn_id\":7,\"seq\":3,\"audio\":{\"encoding\":\"pcm_s16le\",\"sample_rate\":16000,\"channels\":1,\"data_b64\":\"AQI=\"}}";
        ProtocolJson.SpeakChunkMsg chunkMsg;
        Assert.IsTrue(ProtocolJson.TryParseSpeakChunk(chunk, out chunkMsg));
        Assert.AreEqual(7, chunkMsg.TurnId);
        Assert.AreEqual(3, chunkMsg.Seq);
        Assert.AreEqual("AQI=", chunkMsg.AudioDataB64);

        string final = "{\"turn_id\":7,\"text\":\"A red square.\",\"voice_gate\":\"passed\"}";
        ProtocolJson.SpeakFinalMsg finalMsg;
        Assert.IsTrue(ProtocolJson.TryParseSpeakFinal(final, out finalMsg));
        Assert.AreEqual(7, finalMsg.TurnId);
        Assert.AreEqual("A red square.", finalMsg.Text);
        Assert.AreEqual("passed", finalMsg.VoiceGate);

        ProtocolJson.SpeakChunkMsg bad;
        Assert.IsFalse(ProtocolJson.TryParseSpeakChunk("{\"turn_id\":7}", out bad));
    }

    [Test]
    public void PcmConversionPreservesSignAndOrder()
    {
        // 0x1111 positive, 0x8000 most-negative, little-endian.
        var pcm = new byte[] { 0x11, 0x11, 0x00, 0x80 };
        float[] floats;
        Assert.IsTrue(SpeakCloudPlayer.TryConvertPcm(pcm, out floats));
        Assert.AreEqual(2, floats.Length);
        Assert.AreEqual(0x1111 / 32768f, floats[0], 1e-6f);
        Assert.AreEqual(-1f, floats[1], 1e-6f);
        float[] rejected;
        Assert.IsFalse(SpeakCloudPlayer.TryConvertPcm(null, out rejected));
        Assert.IsFalse(SpeakCloudPlayer.TryConvertPcm(new byte[0], out rejected));
        Assert.IsFalse(SpeakCloudPlayer.TryConvertPcm(new byte[3], out rejected));
    }

    [Test]
    public void AppendChunkRejectsBadInputWithoutTouchingStream()
    {
        var go = new GameObject("StreamPlayer2");
        try
        {
            var player = go.AddComponent<SpeakCloudPlayer>();
            Assert.IsFalse(player.AppendChunk(4, null));
            Assert.IsFalse(player.AppendChunk(4, new byte[0]));
            Assert.IsFalse(player.AppendChunk(4, new byte[3]));
            Assert.AreEqual(0, player.StreamingTurnId);
            Assert.AreEqual(0, player.StreamedSamples);
            // Clip growth past conversion needs a real audio device;
            // covered by the hardware acceptance pass, not headless.
            player.StopPlayback();
            player.StopPlayback();
            Assert.AreEqual(0, player.StreamedSamples);
        }
        finally { Object.DestroyImmediate(go); }
    }

    static byte[] PcmBytes(short value, int samples)
    {
        var pcm = new byte[samples * 2];
        for (int i = 0; i < samples; i++)
        {
            pcm[i * 2] = (byte)(value & 0xFF);
            pcm[i * 2 + 1] = (byte)((value >> 8) & 0xFF);
        }
        return pcm;
    }

    [Test]
    public void SecondTurnChunksAcceptedAfterFinal()
    {
        var go = new GameObject("StreamPlayer3");
        try
        {
            var player = go.AddComponent<SpeakCloudPlayer>();
            typeof(SpeakCloudPlayer).GetMethod("Awake", Private).Invoke(player, null);
            Assert.IsTrue(player.AppendChunk(4, PcmBytes(1000, 1600)));
            Assert.AreEqual(4, player.StreamingTurnId);
            // Foreign turn while owned: rejected, stream untouched.
            Assert.IsFalse(player.AppendChunk(5, PcmBytes(1000, 1600)));
            Assert.AreEqual(4, player.StreamingTurnId);
            // Final releases the turn; the audible tail keeps playing.
            player.FinishTurn(4);
            Assert.AreEqual(0, player.StreamingTurnId);
            // Next turn writes from zero on the same reused clip.
            Assert.IsTrue(player.AppendChunk(5, PcmBytes(2000, 1600)));
            Assert.AreEqual(5, player.StreamingTurnId);
            Assert.AreEqual(1600, player.StreamedSamples);
            // Stale final for the old turn changes nothing.
            player.FinishTurn(4);
            Assert.AreEqual(5, player.StreamingTurnId);
            player.StopPlayback();
            Assert.AreEqual(0, player.StreamingTurnId);
            Assert.AreEqual(0, player.StreamedSamples);
        }
        finally { Object.DestroyImmediate(go); }
    }

    [Test]
    public void BargeInRequiresLoudOnsetDuringPlayback()
    {
        var loud = new List<List<short>> { LoudChunk() };
        var quiet = new List<List<short>> { QuietChunk() };
        Assert.IsTrue(MicUtterance.IsBargeInLoud(loud));
        Assert.IsFalse(MicUtterance.IsBargeInLoud(quiet));
        Assert.IsFalse(MicUtterance.IsBargeInLoud(null));
        Assert.AreEqual(0.05f, MicUtterance.BargeInRms);
    }
}
