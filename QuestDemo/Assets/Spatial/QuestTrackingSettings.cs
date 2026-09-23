using UnityEngine;

/// <summary>Non-secret settings shared by scripted builds and the Inspector.</summary>
[CreateAssetMenu(menuName = "Omni/Quest Tracking Settings")]
public class QuestTrackingSettings : ScriptableObject
{
    public bool enableTracking = true;
    [Tooltip("Computer LAN IPv4, or 127.0.0.1 with adb reverse tcp:8765 tcp:8765.")]
    public string laptopIpv4 = "127.0.0.1";
    [Range(1, 10)] public int streamFps = 3;
    [Range(320, 1024)] public int maxImageSide = 640;
    [Range(30, 90)] public int jpegQuality = 70;
    [Tooltip("Only enable if the received JPEG is vertically inverted on this camera/backend.")]
    public bool flipImageVertically;

    public static QuestTrackingSettings Load()
    {
        return Resources.Load<QuestTrackingSettings>("QuestTrackingSettings");
    }
}
