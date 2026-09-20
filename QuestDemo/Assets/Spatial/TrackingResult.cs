using System;

/// <summary>Existing SAM 2 masks, correlated to the Quest capture frame.
/// Subscribe to CoordinatorClient.TrackingResultReceived from the renderer.
/// Decode mask_b64 as a grayscale PNG; foreground is 255. No rendering here.</summary>
[Serializable]
public class TrackingResult
{
    public string frame_id;
    public string seed_frame_id;
    public int generation;
    public int stage_epoch;
    public int width;
    public int height;
    public TrackingObject[] objects;
    [NonSerialized] public string RawPayloadJson; // includes original capture envelope
}

[Serializable]
public class TrackingObject
{
    public int obj_id;
    public string mask_b64;
}

/// <summary>Assistant reply for the headset to speak (payload of `speak`).</summary>
[Serializable]
public class SpeakPayload
{
    public int turn_id;
    public string text;
}

[Serializable]
public class TrackingStatus
{
    public string state;
    public string text;
    public int generation;
}
