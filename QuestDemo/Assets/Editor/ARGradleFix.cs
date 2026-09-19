using System.IO;
using UnityEditor;
using UnityEditor.Android;
using UnityEngine;

/// <summary>
/// Appends the openxr_loader pickFirst to the GENERATED launcher + library
/// build.gradle files. Runs every Android build; no template flags needed.
/// </summary>
public class ARGradleFix : IPostGenerateGradleAndroidProject
{
    public int callbackOrder => 1000;

    const string kBlock = @"
// QuestDemo: Unity OpenXR and Meta OVRPlugin both ship libopenxr_loader.so.
android {
    packagingOptions {
        pickFirst 'lib/arm64-v8a/libopenxr_loader.so'
        pickFirst 'lib/armeabi-v7a/libopenxr_loader.so'
    }
}
";

    public void OnPostGenerateGradleAndroidProject(string path)
    {
        Patch(Path.Combine(path, "build.gradle"));
        var launcher = Path.Combine(Path.GetDirectoryName(path), "launcher", "build.gradle");
        Patch(launcher);
    }

    static void Patch(string gradleFile)
    {
        if (!File.Exists(gradleFile))
        {
            Debug.LogWarning("ARGradleFix: missing " + gradleFile);
            return;
        }
        var text = File.ReadAllText(gradleFile);
        if (!text.Contains("libopenxr_loader.so"))
        {
            File.AppendAllText(gradleFile, kBlock);
            Debug.Log("ARGradleFix: pickFirst appended to " + gradleFile);
            return;
        }
        if (!text.Contains("armeabi-v7a/libopenxr_loader"))
        {
            text = text.Replace(
                "pickFirst 'lib/arm64-v8a/libopenxr_loader.so'",
                "pickFirst 'lib/arm64-v8a/libopenxr_loader.so'\n        pickFirst 'lib/armeabi-v7a/libopenxr_loader.so'");
            File.WriteAllText(gradleFile, text);
            Debug.Log("ARGradleFix: armeabi-v7a pickFirst added to " + gradleFile);
            return;
        }
        Debug.Log("ARGradleFix: pickFirst already present in " + gradleFile);
    }
}
