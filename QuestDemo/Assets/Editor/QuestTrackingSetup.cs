using UnityEditor;
using UnityEngine;

/// <summary>Discoverable setup that survives ARSetup's deterministic scene rebuild.</summary>
public static class QuestTrackingSetup
{
    [MenuItem("Omni/Configure Quest Tracking")]
    public static void Configure()
    {
        const string path = "Assets/Resources/QuestTrackingSettings.asset";
        if (!AssetDatabase.IsValidFolder("Assets/Resources"))
            AssetDatabase.CreateFolder("Assets", "Resources");
        var settings = AssetDatabase.LoadAssetAtPath<QuestTrackingSettings>(path);
        if (settings == null)
        {
            settings = ScriptableObject.CreateInstance<QuestTrackingSettings>();
            AssetDatabase.CreateAsset(settings, path);
        }
        settings.enableTracking = true;
        EditorUtility.SetDirty(settings);
        AssetDatabase.SaveAssets();
        Selection.activeObject = settings;
        EditorGUIUtility.PingObject(settings);
        Debug.Log("Set Laptop IPv4 in the Inspector, then Omni > Build Quest APK. "
                  + "Use 127.0.0.1 with adb reverse, or your computer's LAN address.");
    }

    [MenuItem("Omni/Build Quest APK")]
    public static void Build()
    {
        BuildAndroid.Build();
    }

    [MenuItem("Omni/Setup Quest Scene")]
    public static void Setup()
    {
        BuildAndroid.SetupOnly();
    }
}
