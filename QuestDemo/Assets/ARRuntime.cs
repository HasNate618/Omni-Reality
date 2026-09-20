using System.Collections.Generic;
using Meta.XR;
using UnityEngine;

/// <summary>
/// Runtime AR director: camera and scene permissions, plus the camera-texture
/// quad. Capture pins come only from SpatialRuntime world_hint (capture-time
/// ray + depth hit); a miss records null world_hint and nothing is planted here.
/// </summary>
public class ARRuntime : MonoBehaviour
{
    PassthroughCameraAccess _cameraAccess;
    Renderer _quadRenderer;

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
}
