using System;
using System.Collections.Generic;

public enum VoiceActivityState
{
    Idle,
    Started,
    Streaming,
    Ended
}

public struct VoiceActivityDecision
{
    public VoiceActivityState State;
    public List<List<short>> Chunks;
}

/// <summary>
/// Pure RMS voice-activity gate for fixed-size PCM chunks (100 ms @ 16 kHz).
/// </summary>
public sealed class VoiceActivityGate
{
    public const int ChunkSamples = 1600;
    public const float SilenceRms = 0.01f;
    public const int PreRollChunks = 3;
    public const int SilenceChunksToEnd = 8;
    public const int MaxUtteranceChunks = 80;

    readonly Queue<List<short>> _preRoll = new Queue<List<short>>(PreRollChunks);
    bool _utteranceOpen;
    int _silenceStreak;
    int _utteranceChunks;

    public VoiceActivityDecision Observe(List<short> chunk)
    {
        if (_utteranceOpen)
            return ObserveOpen(chunk);

        if (ComputeRms(chunk) >= SilenceRms)
            return ObserveOnset(chunk);

        EnqueuePreRoll(chunk);
        return IdleDecision();
    }

    VoiceActivityDecision ObserveOnset(List<short> chunk)
    {
        var retained = new List<List<short>>(PreRollChunks + 1);
        foreach (List<short> pre in _preRoll)
            retained.Add(pre);
        retained.Add(CopyChunk(chunk));

        _preRoll.Clear();
        _utteranceOpen = true;
        _silenceStreak = 0;
        _utteranceChunks = 1;

        return new VoiceActivityDecision
        {
            State = VoiceActivityState.Started,
            Chunks = retained
        };
    }

    VoiceActivityDecision ObserveOpen(List<short> chunk)
    {
        _utteranceChunks++;
        if (_utteranceChunks >= MaxUtteranceChunks)
            return EndUtterance(chunk);

        if (ComputeRms(chunk) < SilenceRms)
        {
            _silenceStreak++;
            if (_silenceStreak >= SilenceChunksToEnd)
                return EndUtterance(chunk);
        }
        else
        {
            _silenceStreak = 0;
        }

        return new VoiceActivityDecision
        {
            State = VoiceActivityState.Streaming,
            Chunks = SingleChunkList(chunk)
        };
    }

    VoiceActivityDecision EndUtterance(List<short> chunk)
    {
        _utteranceOpen = false;
        _silenceStreak = 0;
        _utteranceChunks = 0;
        _preRoll.Clear();

        return new VoiceActivityDecision
        {
            State = VoiceActivityState.Ended,
            Chunks = SingleChunkList(chunk)
        };
    }

    void EnqueuePreRoll(List<short> chunk)
    {
        if (_preRoll.Count >= PreRollChunks)
            _preRoll.Dequeue();
        _preRoll.Enqueue(CopyChunk(chunk));
    }

    static VoiceActivityDecision IdleDecision()
    {
        return new VoiceActivityDecision
        {
            State = VoiceActivityState.Idle,
            Chunks = new List<List<short>>()
        };
    }

    static List<List<short>> SingleChunkList(List<short> chunk)
    {
        return new List<List<short>> { CopyChunk(chunk) };
    }

    static List<short> CopyChunk(List<short> chunk)
    {
        return new List<short>(chunk);
    }

    static float ComputeRms(List<short> chunk)
    {
        if (chunk == null || chunk.Count == 0)
            return 0f;

        double sumSquares = 0d;
        for (int i = 0; i < chunk.Count; i++)
        {
            double sample = chunk[i] / 32768d;
            sumSquares += sample * sample;
        }

        return (float)Math.Sqrt(sumSquares / chunk.Count);
    }
}
