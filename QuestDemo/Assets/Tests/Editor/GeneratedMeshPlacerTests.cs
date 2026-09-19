using NUnit.Framework;

public class GeneratedMeshPlacerTests
{
    [Test]
    public void ArtifactUrl_UsesJobIdOnly()
    {
        string url = GeneratedMeshPlacer.ArtifactUrl("10.0.0.8", 8766, "01k00000000000000000000001");
        Assert.AreEqual("http://10.0.0.8:8766/artifacts/01k00000000000000000000001.glb", url);
    }

    [Test]
    public void ArtifactUrl_RejectsDotDot()
    {
        Assert.IsNull(GeneratedMeshPlacer.ArtifactUrl("10.0.0.8", 8766, "../x"));
    }
}
