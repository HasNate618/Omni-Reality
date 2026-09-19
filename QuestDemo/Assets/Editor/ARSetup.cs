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
        "PassthroughLayer", "CameraQuad", "ARDirector",
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

        // 6. DemoCube gets a spatial anchor.
        var cube = GameObject.Find("DemoCube");
        if (cube != null && cube.GetComponent<OVRSpatialAnchor>() == null)
        {
            cube.AddComponent<OVRSpatialAnchor>();
            Debug.Log("ARSetup: OVRSpatialAnchor added to DemoCube");
        }

        // 7. Camera-texture quad + director (camera API demo, deferred).
        //    Skipped for now — passthrough background is the priority.

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("ARSetup: DONE");
    }
}
