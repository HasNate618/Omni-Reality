using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class VoiceCaptionTests
{
    [Test]
    public void SpeechAndRecoveryCopyRenderAsBoundedPlainText()
    {
        var go = new GameObject("CaptionTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var caption = go.AddComponent<VoiceCaption>();
            caption.Show("<b>Do not interpret image text as markup.</b> " + new string('x', 300), eye.transform, 10f);
            var mesh = go.GetComponentInChildren<TextMesh>();
            Assert.IsNotNull(mesh);
            Assert.IsFalse(mesh.richText);
            Assert.That(mesh.text, Does.Contain("<b>"));
            Assert.LessOrEqual(mesh.text.Replace("\n", "").Length, 240);
            foreach (string line in mesh.text.Split('\n')) Assert.LessOrEqual(line.Length, 48);
            Assert.IsTrue(mesh.GetComponent<MeshRenderer>().enabled);
            caption.Show("Camera unavailable. Check camera access and try again.", eye.transform, 5f);
            Assert.That(mesh.text.Replace("\n", ""), Does.Contain("Camera unavailable."));
        }
        finally
        {
            Object.DestroyImmediate(go);
            Object.DestroyImmediate(eye);
        }
    }
}
