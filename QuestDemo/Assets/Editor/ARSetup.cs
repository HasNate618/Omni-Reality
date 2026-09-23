using Meta.XR;
using Meta.XR.EnvironmentDepth;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

/// <summary>
/// Idempotent AR setup: OVR rig + passthrough, anchored cube, camera quad.
/// Wipes AR remnants first, rebuilds deterministically, and FAILS LOUDLY
/// if the rig root is not present afterwards (silent scene corruption
/// shipped a broken build before).
/// Runs headless from BuildAndroid before the build.
/// </summary>
public static class ARSetup
{
    const string k_RigPrefab = "Packages/com.meta.xr.sdk.core/Prefabs/OVRCameraRig.prefab";

    static readonly string[] k_Remnants =
    {
        "OVRCameraRig", "TrackingSpace", "CenterEyeAnchor",
        "LeftEyeAnchor", "RightEyeAnchor",
        "LeftHandAnchor", "RightHandAnchor",
        "LeftHandOnControllerAnchor", "RightHandOnControllerAnchor",
        "LeftControllerInHandAnchor", "RightControllerInHandAnchor",
        "LeftHandAnchorDetached", "RightHandAnchorDetached",
        "PassthroughLayer", "CameraQuad", "ARDirector", "EnvironmentDepth", "EnvRaycast",
    };

    public static void EnsureAR()
    {
        // Custom mainTemplate.gradle carries the openxr_loader pickFirst.
        // (No public API in 6000.6; flip the serialized PlayerSettings flag.)
        foreach (var ps in Resources.FindObjectsOfTypeAll<PlayerSettings>())
        {
            var so = new SerializedObject(ps);
            var prop = so.FindProperty("useCustomMainGradleTemplate");
            if (prop != null && !prop.boolValue)
            {
                prop.boolValue = true;
                so.ApplyModifiedProperties();
                Debug.Log("ARSetup: useCustomMainGradleTemplate enabled");
            }
        }

        var scene = EditorSceneManager.GetActiveScene();

        // 0a. Scene (spatial data) support: lets Meta's manifest preprocessor
        //     emit the Scene permission entry so a fresh install can grant
        //     depth/environment raycast. No manifest or package file is
        //     touched; this is the sanctioned project-config lever.
        var projectConfig = OVRProjectConfig.CachedProjectConfig;
        if (projectConfig == null)
        {
            Debug.LogWarning("ARSetup: OVRProjectConfig unavailable, Scene manifest entry not declared");
        }
        else if (projectConfig.sceneSupport == OVRProjectConfig.FeatureSupport.None)
        {
            projectConfig.sceneSupport = OVRProjectConfig.FeatureSupport.Supported;
            OVRProjectConfig.CommitProjectConfig(projectConfig);
            Debug.Log("ARSetup: OVRProjectConfig.sceneSupport=Supported");
        }

        // 0. Wipe AR remnants so a half-present rig can never survive.
        foreach (var name in k_Remnants)
        {
            var go = GameObject.Find(name);
            if (go != null)
            {
                Object.DestroyImmediate(go);
                Debug.Log("ARSetup: removed remnant " + name);
            }
        }

        // 1. Fresh OVR camera rig, unpacked to plain objects.
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(k_RigPrefab);
        if (prefab == null)
        {
            Debug.LogError("ARSetup: OVRCameraRig prefab missing at " + k_RigPrefab);
            EditorApplication.Exit(1);
            return;
        }
        var rig = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
        rig.name = "OVRCameraRig";
        PrefabUtility.UnpackPrefabInstance(rig, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
        Debug.Log("ARSetup: OVRCameraRig instantiated + unpacked");

        var ovrManager = rig.GetComponent<OVRManager>();
        if (ovrManager == null)
            ovrManager = rig.AddComponent<OVRManager>();
        ovrManager.isInsightPassthroughEnabled = true;

        // Floor-stage contract: every emitted pose/ray/hint is labelled
        // openxr_floor_stage, so the rig must be floor-based. The prefab
        // default is eye-level; configure FloorLevel and fail loudly if an
        // eye-level rig would survive — it must never pass setup.
        ovrManager.trackingOriginType = OVRManager.TrackingOrigin.FloorLevel;
        if (ovrManager.trackingOriginType == OVRManager.TrackingOrigin.EyeLevel)
        {
            Debug.LogError("ARSetup: eye-level tracking origin cannot satisfy openxr_floor_stage. Aborting, scene NOT saved.");
            EditorApplication.Exit(1);
            return;
        }
        Debug.Log("ARSetup: tracking origin floor-based (" + ovrManager.trackingOriginType + ")");

        // 2. Passthrough underlay layer.
        var layerGO = new GameObject("PassthroughLayer");
        var ptLayer = layerGO.AddComponent<OVRPassthroughLayer>();
        ptLayer.overlayType = OVROverlay.OverlayType.Underlay;
        Debug.Log("ARSetup: OVRPassthroughLayer added (Underlay)");

        // 3. Transparent eye cameras — REQUIRED for underlay passthrough.
        //    Meta's own sample scenes use SolidColor + alpha-0 background so
        //    the compositor's passthrough video shows through. (Skybox here
        //    paints the Unity scene opaque OVER passthrough.)
        int camCount = 0;
        foreach (var cam in rig.GetComponentsInChildren<Camera>(true))
        {
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0f, 0f, 0f, 0f);
            camCount++;
        }
        Debug.Log("ARSetup: " + camCount + " rig cameras set transparent");

        // 4. Validate the rig before anything else touches the scene.
        var check = GameObject.Find("OVRCameraRig");
        var centerEye = GameObject.Find("CenterEyeAnchor");
        if (check == null || centerEye == null || centerEye.GetComponent<Camera>() == null || camCount == 0)
        {
            Debug.LogError("ARSetup: rig validation FAILED (root/center-eye/camera missing). Aborting, scene NOT saved.");
            EditorApplication.Exit(1);
            return;
        }
        Debug.Log("ARSetup: rig validated");

        // 5. Retire the old flat camera (keep it, disabled, as fallback).
        var oldCam = GameObject.Find("Main Camera");
        if (oldCam != null && oldCam.activeSelf)
        {
            oldCam.SetActive(false);
            Debug.Log("ARSetup: legacy Main Camera disabled");
        }

        // 7. AR director: left PCA camera, depth + raycast, capture runtime.
        //    Slice 1 capture envelope source of truth; see SpatialRuntime.
        var director = GameObject.Find("ARDirector");
        if (director == null)
            director = new GameObject("ARDirector");
        var pca = director.GetComponent<PassthroughCameraAccess>();
        if (pca == null)
            pca = director.AddComponent<PassthroughCameraAccess>();
        pca.CameraPosition = PassthroughCameraAccess.CameraPositionType.Left;
        pca.RequestedResolution = new Vector2Int(1280, 960);
        Debug.Log("ARSetup: PassthroughCameraAccess left 1280x960");

        var depthGO = GameObject.Find("EnvironmentDepth");
        if (depthGO == null)
        {
            depthGO = new GameObject("EnvironmentDepth");
            depthGO.transform.SetParent(director.transform, false);
        }
        if (depthGO.GetComponent<EnvironmentDepthManager>() == null)
            depthGO.AddComponent<EnvironmentDepthManager>();
        Debug.Log("ARSetup: EnvironmentDepthManager added");

        var rayGO = GameObject.Find("EnvRaycast");
        if (rayGO == null)
        {
            rayGO = new GameObject("EnvRaycast");
            rayGO.transform.SetParent(director.transform, false);
        }
        if (rayGO.GetComponent<EnvironmentRaycastManager>() == null)
            rayGO.AddComponent<EnvironmentRaycastManager>();
        Debug.Log("ARSetup: EnvironmentRaycastManager added");

        if (director.GetComponent<ARRuntime>() == null)
            director.AddComponent<ARRuntime>();
        if (director.GetComponent<SpatialRuntime>() == null)
            director.AddComponent<SpatialRuntime>();
        if (director.GetComponent<QuestStreamInput>() == null)
            director.AddComponent<QuestStreamInput>();
        Debug.Log("ARSetup: ARRuntime + SpatialRuntime on ARDirector");

        // 8. Camera-texture quad + director (camera API demo, deferred).
        //    Skipped for now — passthrough background is the priority.

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("ARSetup: DONE");
    }
}
