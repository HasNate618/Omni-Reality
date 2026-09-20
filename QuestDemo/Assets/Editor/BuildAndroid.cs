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
        BuildWithOptions(BuildOptions.None);
    }

    /// <summary>Device-test build: debuggable so prefs (laptop_ipv4) can be
    /// seeded via run-as. Test builds only, never the demo release.</summary>
    public static void BuildDevelopment()
    {
        BuildWithOptions(BuildOptions.Development | BuildOptions.AllowDebugging);
    }

    /// <summary>Strip bootstrap-only scene objects before the in-memory build scene is baked.</summary>
    public static void RemoveBootstrapDemoObjects(Scene scene)
    {
        BootstrapDemoRemoval.RemoveDemoCube(scene);
    }

    static void BuildWithOptions(BuildOptions options)
    {
        const string scenePath = "Assets/Scenes/SampleScene.unity";
        Scene scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
        RemoveBootstrapDemoObjects(scene);

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

        // Runtime-created materials (rings, ghosts, labels, procedural,
        // generated) use Unlit/Color and Unlit/Transparent via Shader.Find:
        // pin both so release stripping cannot drop either (else visuals
        // silently vanish on device).
        EnsureAlwaysIncludedShader("Unlit/Color");
        EnsureAlwaysIncludedShader("Unlit/Transparent");

        System.IO.Directory.CreateDirectory("Builds");
        BuildPlayerOptions opts = new BuildPlayerOptions
        {
            scenes = new[] { scenePath },
            locationPathName = "Builds/QuestDemo.apk",
            targetGroup = BuildTargetGroup.Android,
            target = BuildTarget.Android,
            options = options,
        };
        var report = BuildPipeline.BuildPlayer(opts);
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
        {
            Debug.LogError("BUILD FAILED: " + report.summary.result);
            EditorApplication.Exit(1);
        }
        Debug.Log("BUILD SUCCEEDED: Builds/QuestDemo.apk");
    }

    static void EnsureAlwaysIncludedShader(string shaderName)
    {
        Shader shader = Shader.Find(shaderName);
        if (shader == null)
        {
            Debug.LogWarning("BuildAndroid: shader not found: " + shaderName);
            return;
        }
        var gfx = UnityEditor.AssetDatabase.LoadAssetAtPath<UnityEngine.Rendering.GraphicsSettings>(
            "ProjectSettings/GraphicsSettings.asset");
        if (gfx == null)
            return;
        var so = new SerializedObject(gfx);
        var arr = so.FindProperty("m_AlwaysIncludedShaders");
        for (int i = 0; i < arr.arraySize; i++)
        {
            if (arr.GetArrayElementAtIndex(i).objectReferenceValue == shader)
                return;
        }
        arr.InsertArrayElementAtIndex(arr.arraySize);
        arr.GetArrayElementAtIndex(arr.arraySize - 1).objectReferenceValue = shader;
        so.ApplyModifiedProperties();
        UnityEditor.AssetDatabase.SaveAssets();
        Debug.Log("BuildAndroid: pinned always-included shader " + shaderName);
    }
}
