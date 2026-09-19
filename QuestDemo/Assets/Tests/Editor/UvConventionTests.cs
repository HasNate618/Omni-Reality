using System;
using System.IO;
using NUnit.Framework;
using UnityEngine;

public class UvConventionTests
{
    [Serializable]
    private class UvCase
    {
        public float u;
        public float v;
        public float sent_w;
        public float sent_h;
        public float[] pca_viewport;
        public float[] pixel_center;
    }

    [Serializable]
    private class UvFixture
    {
        public UvCase[] cases;
    }

    [Test]
    public void SharedFixtureMatchesBothConversions()
    {
        var path = Path.GetFullPath(Path.Combine(
            Application.dataPath, "..", "..", "provider", "protocol", "fixtures", "uv_cases.json"));
        Assert.That(File.Exists(path), Is.True, $"Shared UV fixture not found: {path}");
        // JsonUtility needs an object root; the shared file remains a JSON array.
        var fixture = JsonUtility.FromJson<UvFixture>("{\"cases\":" + File.ReadAllText(path) + "}");
        Assert.That(fixture.cases, Is.Not.Null.And.Not.Empty);
        foreach (var item in fixture.cases)
        {
            var context = $"u={item.u}, v={item.v}, sent={item.sent_w}x{item.sent_h}";
            Assert.That(item.pca_viewport, Has.Length.EqualTo(2), context);
            Assert.That(item.pixel_center, Has.Length.EqualTo(2), context);

            var viewport = UvConvention.SpecUvToPcaViewport(item.u, item.v);
            Assert.That(viewport.x, Is.EqualTo(item.pca_viewport[0]).Within(1e-5f), context);
            Assert.That(viewport.y, Is.EqualTo(item.pca_viewport[1]).Within(1e-5f), context);

            var pixel = UvConvention.SpecUvToPixelCenter(item.u, item.v, item.sent_w, item.sent_h);
            Assert.That(pixel.x, Is.EqualTo(item.pixel_center[0]).Within(1e-5f), context);
            Assert.That(pixel.y, Is.EqualTo(item.pixel_center[1]).Within(1e-5f), context);
        }
    }
}
