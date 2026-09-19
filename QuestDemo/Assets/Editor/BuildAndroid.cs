using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class BuildAndroid
{
    public static void SetupOnly()
    {
        const string scenePath = "Assets/Scenes/SampleScene.unity";
        EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
        XRSetup.EnsureXR();
        ARSetup.EnsureAR();
        Debug.Log("SETUP ONLY DONE");
    }

    public static void Build()
    {
        const string scenePath = "Assets/Scenes/SampleScene.unity";
        Scene scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);

        if (GameObject.Find("DemoCube") == null)
        {
            GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.name = "DemoCube";
            cube.transform.position = new Vector3(0f, 1f, 2f);
            EditorSceneManager.MarkSceneDirty(scene);
        }
        EditorSceneManager.SaveScene(scene);

        XRSetup.EnsureXR();

        // OVR project config: declare passthrough support so the manifest
        // preprocessor injects com.oculus.feature.PASSTHROUGH (without it the
        // runtime never initializes Insight passthrough: black background).
        var ovrCfg = OVRProjectConfig.CachedProjectConfig;
        if (ovrCfg != null && ovrCfg.insightPassthroughSupport == OVRProjectConfig.FeatureSupport.None)
        {
            ovrCfg.insightPassthroughSupport = OVRProjectConfig.FeatureSupport.Supported;
            UnityEditor.EditorUtility.SetDirty(ovrCfg);
            UnityEditor.AssetDatabase.SaveAssets();
            Debug.Log("BuildAndroid: OVRProjectConfig.insightPassthroughSupport = Supported");
        }

        // Ensure Meta Quest + Meta XR bridge features are enabled.
        // MetaQuestFeature activates Quest extensions; MetaXRFeature is the
        // OVRPlugin bridge (tracking/passthrough/anchors through OpenXR).
        // The SDK's auto-enable dialog never fires in batchmode, so do it here.
        var oxrSettings = UnityEngine.XR.OpenXR.OpenXRSettings.GetSettingsForBuildTargetGroup(BuildTargetGroup.Android);
        if (oxrSettings != null)
        {
            foreach (var f in oxrSettings.GetFeatures())
            {
                if (f == null || f.enabled)
                    continue;
                var t = f.GetType().Name;
                if (t == "MetaQuestFeature" || t == "MetaXRFeature")
                {
                    f.enabled = true;
                    Debug.Log("BuildAndroid: enabled " + t + " for Android");
                }
            }
        }

        ARSetup.EnsureAR();

        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(scenePath, true) };

        PlayerSettings.companyName = "omni";
        PlayerSettings.productName = "QuestDemo";
        PlayerSettings.SetApplicationIdentifier(BuildTargetGroup.Android, "com.omni.questdemo");
        PlayerSettings.Android.minSdkVersion = AndroidSdkVersions.AndroidApiLevel32;
        PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
        PlayerSettings.runInBackground = true;

        EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);

        System.IO.Directory.CreateDirectory("Builds");
        BuildPlayerOptions opts = new BuildPlayerOptions
        {
            scenes = new[] { scenePath },
            locationPathName = "Builds/QuestDemo.apk",
            targetGroup = BuildTargetGroup.Android,
            target = BuildTarget.Android,
            options = BuildOptions.None,
        };
        var report = BuildPipeline.BuildPlayer(opts);
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
        {
            Debug.LogError("BUILD FAILED: " + report.summary.result);
            EditorApplication.Exit(1);
        }
        Debug.Log("BUILD SUCCEEDED: Builds/QuestDemo.apk");
    }
}
