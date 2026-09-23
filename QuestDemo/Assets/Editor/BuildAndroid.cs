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

        // The old smoke-test cube is gone: passthrough plus the tracking
        // overlay are the scene now. Delete it from scenes that still have it.
        RemoveBootstrapDemoObjects(scene);
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

        // UnityWebRequest refuses plain HTTP by default ("Not allowed"), and the
        // generated-mesh fetch is http:// on the LAN. This is the request-level
        // gate; ARCleartextFix handles the platform-level one (a network security
        // config, when present, wins over android:usesCleartextTraffic).
        PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;

        EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);

        // Runtime-created materials (rings, ghosts, labels, procedural,
        // generated) use Unlit/Color and Unlit/Transparent via Shader.Find:
        // pin both so release stripping cannot drop either (else visuals
        // silently vanish on device).
        EnsureAlwaysIncludedShader("Unlit/Color");
        EnsureAlwaysIncludedShader("Unlit/Transparent");

        WriteLaptopIpAsset();
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
        // Runs BEFORE the result check on purpose: a failed build can still have
        // written the token, and that is exactly when nobody is looking.
        BlankDebuggerAccessToken();
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
        {
            Debug.LogError("BUILD FAILED: " + report.summary.result);
            EditorApplication.Exit(1);
        }
        Debug.Log("BUILD SUCCEEDED: Builds/QuestDemo.apk");
    }

    /// <summary>
    /// Re-blank the Meta Immersive Debugger access token that a build writes into
    /// Assets/Resources/DevAgentSettings.asset.
    ///
    /// That asset is COMMITTED, so a token left behind sits in the working tree and
    /// gets swept up by the next `git add -A` -- the secret-scanner trip that
    /// docs/questdemo-build.md warns about ("the SDK-bundled default accessToken is
    /// blanked"). Verified: a build with no other change turns `accessToken: ` into
    /// `accessToken: <32 hex>`, so this has to run after every build, not once.
    ///
    /// Idempotent, and a no-op when the file or the field is absent, so it is safe
    /// on any machine. Only the token is cleared; serverAddress is environment
    /// configuration that HEAD already carries.
    /// </summary>
    static void BlankDebuggerAccessToken()
    {
        const string path = "Assets/Resources/DevAgentSettings.asset";
        if (!System.IO.File.Exists(path))
            return;
        string text = System.IO.File.ReadAllText(path);
        string blanked = System.Text.RegularExpressions.Regex.Replace(
            text,
            @"^(\s*accessToken:\s*).*$",
            "$1",
            System.Text.RegularExpressions.RegexOptions.Multiline);
        if (blanked == text)
            return;
        System.IO.File.WriteAllText(path, blanked);
        AssetDatabase.ImportAsset(path);
        Debug.Log("BuildAndroid: blanked the Immersive Debugger accessToken in " + path);
    }

    /// <summary>
    /// Bake OMNI_LAPTOP_IP into StreamingAssets so a non-debuggable build can
    /// still be pointed at the demo laptop. Without this the address can only
    /// come from the laptop_ipv4 pref, which needs `adb shell run-as` and so is
    /// writeable only on a debuggable build. SpatialRuntime prefers the pref
    /// when it is set, so this never overrides a device-side choice.
    /// </summary>
    static void WriteLaptopIpAsset()
    {
        const string dir = "Assets/StreamingAssets";
        const string path = dir + "/laptop_ip.txt";
        string ip = (System.Environment.GetEnvironmentVariable("OMNI_LAPTOP_IP") ?? "").Trim();
        if (ip.Length == 0)
        {
            // Never ship a stale address from an earlier build.
            if (System.IO.File.Exists(path))
            {
                System.IO.File.Delete(path);
                AssetDatabase.Refresh();
                Debug.Log("BuildAndroid: OMNI_LAPTOP_IP unset; removed stale " + path);
            }
            else
            {
                Debug.Log("BuildAndroid: OMNI_LAPTOP_IP unset; no laptop IP baked");
            }
            return;
        }
        System.IO.Directory.CreateDirectory(dir);
        System.IO.File.WriteAllText(path, ip);
        // The file is written mid-run, so import it before the build reads
        // StreamingAssets; otherwise the bake may miss the player.
        AssetDatabase.Refresh();
        Debug.Log("BuildAndroid: baked laptop IP " + ip + " into " + path);
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
