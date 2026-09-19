#if UNITY_EDITOR
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

/// <summary>
/// Editor-only DemoCube cleanup shared by build scripts and EditMode tests.
/// </summary>
public static class BootstrapDemoRemoval
{
    public static void RemoveDemoCube(Scene scene)
    {
        GameObject cube = GameObject.Find("DemoCube");
        if (cube == null)
            return;
        Object.DestroyImmediate(cube);
        EditorSceneManager.MarkSceneDirty(scene);
    }
}
#endif
