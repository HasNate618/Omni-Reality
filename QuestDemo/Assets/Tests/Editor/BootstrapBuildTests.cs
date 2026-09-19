using System;
using System.Reflection;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public class BootstrapBuildTests
{
    static void InvokeRemoveBootstrapDemoObjects(Scene scene)
    {
        Type buildAndroid = Type.GetType("BuildAndroid, Assembly-CSharp-Editor");
        Assert.IsNotNull(buildAndroid, "BuildAndroid type in Assembly-CSharp-Editor");
        MethodInfo method = buildAndroid.GetMethod(
            "RemoveBootstrapDemoObjects",
            BindingFlags.Public | BindingFlags.Static);
        Assert.IsNotNull(method, "BuildAndroid.RemoveBootstrapDemoObjects");
        method.Invoke(null, new object[] { scene });
    }

    [Test]
    public void RemoveBootstrapDemoObjects_DestroysDemoCube()
    {
        Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.name = "DemoCube";

        InvokeRemoveBootstrapDemoObjects(scene);

        Assert.IsNull(GameObject.Find("DemoCube"));
    }

    [Test]
    public void AimLineDisabledWhenNoRightTouchController()
    {
        Assert.IsFalse(SpatialRuntime.ShouldEnableAimLineForController(false));
    }
}
