using System.Collections;
using System.Collections.Generic;
using Meta.XR;
using UnityEngine;

/// <summary>
/// Runtime AR director: camera permission, camera-texture quad, cube anchor.
/// Task 4 contract: DemoCube is scenery, never the capture pin. Capture pins
/// come only from SpatialRuntime world_hint (capture-time ray + depth hit);
/// a miss records null world_hint and no pin is planted anywhere here.
/// </summary>
public class ARRuntime : MonoBehaviour
{
    PassthroughCameraAccess _cameraAccess;
    Renderer _quadRenderer;
    bool _anchorSaveStarted;

    void Start()
    {
        _cameraAccess = GetComponent<PassthroughCameraAccess>();
        var quad = GameObject.Find("CameraQuad");
        if (quad != null)
            _quadRenderer = quad.GetComponent<Renderer>();

        if (!OVRPermissionsRequester.IsPermissionGranted(OVRPermissionsRequester.Permission.PassthroughCameraAccess))
        {
            Debug.Log("ARRuntime: requesting HEADSET_CAMERA permission");
            OVRPermissionsRequester.Request(new List<OVRPermissionsRequester.Permission>
            {
                OVRPermissionsRequester.Permission.PassthroughCameraAccess
            });
        }
        // Scene (spatial data) permission is requested independently: depth
        // and environment raycast stay NotReady without it, and captures
        // remain honest misses until it is granted. Never bundle the two
        // requests; each grant is tracked on its own.
        if (!OVRPermissionsRequester.IsPermissionGranted(OVRPermissionsRequester.Permission.Scene))
        {
            Debug.Log("ARRuntime: requesting Scene permission");
            OVRPermissionsRequester.Request(new List<OVRPermissionsRequester.Permission>
            {
                OVRPermissionsRequester.Permission.Scene
            });
        }
        StartCoroutine(SaveAnchorWhenReady());
    }

    void Update()
    {
        if (_cameraAccess != null && _quadRenderer != null)
        {
            var tex = _cameraAccess.GetTexture();
            if (tex != null && _quadRenderer.sharedMaterial.mainTexture != tex)
            {
                _quadRenderer.sharedMaterial.mainTexture = tex;
                Debug.Log("ARRuntime: camera texture live " + tex.width + "x" + tex.height);
            }
        }
    }

    IEnumerator SaveAnchorWhenReady()
    {
        yield return new WaitForSeconds(6f);
        if (_anchorSaveStarted)
            yield break;
        _anchorSaveStarted = true;
        var cube = GameObject.Find("DemoCube");
        if (cube == null)
        {
            Debug.LogWarning("ARRuntime: DemoCube missing, skipping anchor save");
            yield break;
        }
        var anchor = cube.GetComponent<OVRSpatialAnchor>();
        if (anchor == null)
        {
            Debug.LogWarning("ARRuntime: no OVRSpatialAnchor on DemoCube");
            yield break;
        }
        var task = anchor.SaveAnchorAsync();
        yield return new WaitUntil(() => task == null || task.IsCompleted);
        if (task != null && task.IsCompleted)
            Debug.Log("ARRuntime: anchor save result=" + task.GetResult());
    }
}
