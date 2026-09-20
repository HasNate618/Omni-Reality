using System;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class QuestStreamInputTests
{
    [Serializable] class Audio { public string encoding; public int sample_rate; public int channels; public string data_b64; }
    [Serializable] class Payload { public Audio audio; public string frame_id; }
    [Serializable] class Message { public string utterance_id; public Payload payload; }

    [Test]
    public void AudioAndEndCarrySameUtteranceAndExactFrameReference()
    {
        var pcm = new byte[] { 0, 1, 2, 3, 4, 5 };
        var chunk = JsonUtility.FromJson<Message>(ProtocolJson.BuildAudioChunk("session", "speech", pcm, 2, 4));
        var end = JsonUtility.FromJson<Message>(ProtocolJson.BuildUtteranceEnd("session", "speech", "snapshot"));
        Assert.AreEqual("speech", chunk.utterance_id);
        Assert.AreEqual(chunk.utterance_id, end.utterance_id);
        Assert.AreEqual("snapshot", end.payload.frame_id);
        Assert.AreEqual(16000, chunk.payload.audio.sample_rate);
        Assert.AreEqual(1, chunk.payload.audio.channels);
        CollectionAssert.AreEqual(new byte[] { 2, 3, 4, 5 }, Convert.FromBase64String(chunk.payload.audio.data_b64));
    }

    [Test]
    public void PcmIsSignedLittleEndianAndDownmixesStereo()
    {
        var pcm = QuestStreamInput.ToMonoPcm16(new float[] { -1, -1, 0, 0, 1, 1 }, 2, 16000);
        CollectionAssert.AreEqual(new byte[] { 0, 128, 0, 0, 255, 127 }, pcm);
    }

    [Test]
    public void DeviceRateIsConvertedToSixteenKilohertz()
    {
        var samples = new float[48000 * 2];
        for (int i = 0; i < samples.Length; i++) samples[i] = .5f;
        var pcm = QuestStreamInput.ToMonoPcm16(samples, 2, 48000);
        Assert.AreEqual(32000, pcm.Length);
        Assert.AreEqual(16384, (short)(pcm[0] | pcm[1] << 8));
    }

    [Test]
    public void PinnedSnapshotSurvivesReplacementAndAudioEndStaysAfterAudio()
    {
        var type = typeof(CoordinatorClient).GetNestedType("SendQueue", BindingFlags.NonPublic);
        var queue = Activator.CreateInstance(type, true);
        var video = type.GetMethod("VideoFrame");
        var enqueue = type.GetMethod("Enqueue");
        var dequeue = type.GetMethod("TryDequeue");
        video.Invoke(queue, new object[] { "old-video", false });
        video.Invoke(queue, new object[] { "selected-snapshot", true });
        enqueue.Invoke(queue, new object[] { 8, "audio" });
        enqueue.Invoke(queue, new object[] { 8, "utterance-end" });
        video.Invoke(queue, new object[] { "new-video", false });
        video.Invoke(queue, new object[] { "newest-video", false });
        enqueue.Invoke(queue, new object[] { 0, "cancel" });
        foreach (var expected in new[] { "cancel", "selected-snapshot", "audio", "utterance-end", "newest-video" })
        {
            var args = new object[] { null };
            Assert.IsTrue((bool)dequeue.Invoke(queue, args));
            Assert.AreEqual(expected, args[0]);
        }
        Assert.IsFalse((bool)dequeue.Invoke(queue, new object[] { null }));
    }
}
