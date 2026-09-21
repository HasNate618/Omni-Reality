using System.IO;
using UnityEditor.Android;
using UnityEngine;

/// <summary>
/// Rewrites the generated network security config to permit cleartext.
///
/// The headset fetches generated meshes from the demo laptop over plain HTTP
/// (GeneratedMeshPlacer builds http://{laptop_ipv4}:8766/artifacts/{job}.glb)
/// and dials ws:// for the coordinator. Meta's gradle generator installs
/// <base-config cleartextTrafficPermitted="false">, and a network security
/// config takes PRECEDENCE over android:usesCleartextTraffic, so without this
/// rewrite the mesh fetch is blocked on device and no generated object can
/// ever appear.
///
/// Scope: the laptop address is only known at runtime (PlayerPrefs) and NSC
/// domain-config has no CIDR support, so a permissive base-config is the
/// practical scope for a LAN-only demo. The headset never reaches the public
/// internet - the model gateway is called from Python on the laptop.
///
/// callbackOrder must exceed OVRGradleGeneration's 99999: that generator
/// copies its own network_sec_config.xml onto the same path, so a callback at
/// or below it (ARGradleFix uses 1000) would be overwritten again.
/// </summary>
public class ARCleartextFix : IPostGenerateGradleAndroidProject
{
    public int callbackOrder => 100000;

    const string kConfig =
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" +
        "<network-security-config>\n" +
        "    <base-config cleartextTrafficPermitted=\"true\"></base-config>\n" +
        "</network-security-config>\n";

    public void OnPostGenerateGradleAndroidProject(string path)
    {
        var xmlDirectory = Path.Combine(path, "src/main/res/xml");
        Directory.CreateDirectory(xmlDirectory);
        var targetPath = Path.Combine(xmlDirectory, "network_sec_config.xml");

        // A previous build leaves this read-only, which would fail the write.
        if (File.Exists(targetPath))
        {
            File.SetAttributes(targetPath, File.GetAttributes(targetPath) & ~FileAttributes.ReadOnly);
        }
        File.WriteAllText(targetPath, kConfig);
        Debug.Log("ARCleartextFix: cleartext permitted in " + targetPath);
    }
}
