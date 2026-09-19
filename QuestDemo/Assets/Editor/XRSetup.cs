using UnityEditor;
using UnityEditor.XR.Management;
using UnityEngine;
using UnityEngine.SpatialTracking;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;

/// <summary>Idempotent XR setup for the Android (Quest) build target.</summary>
public static class XRSetup
{
    const string k_SettingsDir = "Assets/XR/Settings";
    const string k_PerTargetAsset = "Assets/XR/Settings/XRGeneralSettingsPerBuildTarget.asset";

    public static void EnsureXR()
    {
        // 1. Per-build-target settings asset (get or create).
        var perTarget = FindPerTargetSettings();
        if (perTarget == null)
        {
            perTarget = ScriptableObject.CreateInstance<XRGeneralSettingsPerBuildTarget>();
            if (!AssetDatabase.IsValidFolder(k_SettingsDir))
                AssetDatabase.CreateFolder("Assets/XR", "Settings");
            AssetDatabase.CreateAsset(perTarget, k_PerTargetAsset);
            EditorBuildSettings.AddConfigObject(XRGeneralSettings.settingsKey, perTarget, true);
            Debug.Log("XRSetup: created XRGeneralSettingsPerBuildTarget.asset");
        }

        // 2. Android general + manager settings.
        if (perTarget.SettingsForBuildTarget(BuildTargetGroup.Android) == null)
            perTarget.CreateDefaultSettingsForBuildTarget(BuildTargetGroup.Android);
        if (perTarget.ManagerSettingsForBuildTarget(BuildTargetGroup.Android) == null)
            perTarget.CreateDefaultManagerSettingsForBuildTarget(BuildTargetGroup.Android);

        var general = perTarget.SettingsForBuildTarget(BuildTargetGroup.Android);
        general.InitManagerOnStart = true;

        var manager = perTarget.ManagerSettingsForBuildTarget(BuildTargetGroup.Android);
        manager.automaticLoading = true;
        manager.automaticRunning = true;

        // 3. OpenXR loader assigned.
        bool hasOpenXR = false;
        foreach (var l in manager.activeLoaders)
        {
            if (l != null && l.GetType().FullName == typeof(OpenXRLoader).FullName)
            {
                hasOpenXR = true;
                break;
            }
        }
        if (!hasOpenXR)
        {
            var loader = ScriptableObject.CreateInstance<OpenXRLoader>();
            loader.name = "Open XR Loader";
            AssetDatabase.AddObjectToAsset(loader, k_PerTargetAsset);
            if (!manager.TryAddLoader(loader, 0))
                Debug.LogError("XRSetup: TryAddLoader(OpenXRLoader) failed");
            else
                Debug.Log("XRSetup: OpenXRLoader assigned for Android");
        }
        else
        {
            Debug.Log("XRSetup: OpenXRLoader already assigned for Android");
        }

        EditorUtility.SetDirty(perTarget);
        EditorUtility.SetDirty(general);
        EditorUtility.SetDirty(manager);
        AssetDatabase.SaveAssets();

        // 4. HMD-tracked main camera.
        var cam = Camera.main;
        if (cam == null)
        {
            Debug.LogError("XRSetup: no MainCamera in scene");
            return;
        }
        if (cam.GetComponent<TrackedPoseDriver>() == null)
        {
            var tpd = cam.gameObject.AddComponent<TrackedPoseDriver>();
            tpd.trackingType = TrackedPoseDriver.TrackingType.RotationAndPosition;
            Debug.Log("XRSetup: TrackedPoseDriver added to Main Camera");
        }
        cam.stereoTargetEye = StereoTargetEyeMask.Both;
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene());
        UnityEditor.SceneManagement.EditorSceneManager.SaveScene(
            UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene());
        Debug.Log("XRSetup: DONE");
    }

    static XRGeneralSettingsPerBuildTarget FindPerTargetSettings()
    {
        EditorBuildSettings.TryGetConfigObject(XRGeneralSettings.settingsKey, out XRGeneralSettingsPerBuildTarget s);
        if (s != null)
            return s;
        var guids = AssetDatabase.FindAssets("t:XRGeneralSettingsPerBuildTarget");
        if (guids.Length > 0)
        {
            s = AssetDatabase.LoadAssetAtPath<XRGeneralSettingsPerBuildTarget>(AssetDatabase.GUIDToAssetPath(guids[0]));
            if (s != null)
                EditorBuildSettings.AddConfigObject(XRGeneralSettings.settingsKey, s, true);
            return s;
        }
        return null;
    }
}
