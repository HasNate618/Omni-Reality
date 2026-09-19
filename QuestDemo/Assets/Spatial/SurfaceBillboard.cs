using UnityEngine;

/// <summary>Billboard label text toward the main camera each frame.</summary>
public class SurfaceBillboard : MonoBehaviour
{
    void LateUpdate()
    {
        Camera cam = Camera.main;
        if (cam == null)
            return;
        transform.rotation = Quaternion.LookRotation(transform.position - cam.transform.position);
    }
}
