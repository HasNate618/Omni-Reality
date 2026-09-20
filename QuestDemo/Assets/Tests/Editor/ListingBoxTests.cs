using NUnit.Framework;
using UnityEngine;

public class ListingBoxTests
{
    [Test]
    public void WireOrderBecomesUnityWidthHeightDepth()
    {
        // wire w, d, h = 0.55, 0.40, 0.72  ->  Unity (x, y, z) = (w, h, d)
        Vector3 size = ListingBox.ToBoxSize(new Vector3(0.55f, 0.40f, 0.72f));
        Assert.AreEqual(0.55f, size.x, 1e-5f);
        Assert.AreEqual(0.72f, size.y, 1e-5f);
        Assert.AreEqual(0.40f, size.z, 1e-5f);
    }

    [Test]
    public void FitShrinksTheOversizedAxisOnly()
    {
        // box (w,h,d) = 0.55, 0.72, 0.40 ; mesh twice as wide as the box
        Vector3 box = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 mesh = new Vector3(1.10f, 0.72f, 0.40f);
        float scale = ListingBox.FitScale(mesh, box);
        Assert.AreEqual(0.5f, scale, 1e-4f);
    }

    [Test]
    public void FitIsUniformSoProportionsSurvive()
    {
        Vector3 box = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 mesh = new Vector3(1.10f, 1.80f, 0.80f);
        float scale = ListingBox.FitScale(mesh, box);
        Vector3 scaled = mesh * scale;
        // the mesh's own ratio is preserved: it is the same mesh, one factor
        Assert.AreEqual(mesh.x / mesh.y, scaled.x / scaled.y, 1e-4f);
        Assert.LessOrEqual(scaled.x, box.x + 1e-4f);
        Assert.LessOrEqual(scaled.y, box.y + 1e-4f);
        Assert.LessOrEqual(scaled.z, box.z + 1e-4f);
    }

    [Test]
    public void NeverEnlargesAMeshThatAlreadyFits()
    {
        Vector3 box = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 mesh = new Vector3(0.10f, 0.10f, 0.10f);
        Assert.AreEqual(1f, ListingBox.FitScale(mesh, box), 1e-4f);
    }

    [Test]
    public void DegenerateSizesDoNotDivideByZero()
    {
        Assert.AreEqual(1f, ListingBox.FitScale(Vector3.zero, new Vector3(1f, 1f, 1f)), 1e-4f);
        Assert.AreEqual(1f, ListingBox.FitScale(new Vector3(1f, 1f, 1f), Vector3.zero), 1e-4f);
    }

    [Test]
    public void AspectThresholdTripsAtTwentyFivePercent()
    {
        Vector3 box = new Vector3(0.50f, 0.50f, 0.50f);
        Assert.IsFalse(ListingBox.IsApproximate(new Vector3(0.60f, 0.50f, 0.50f), box));
        Assert.IsTrue(ListingBox.IsApproximate(new Vector3(0.64f, 0.50f, 0.50f), box));
    }

    [Test]
    public void AspectThresholdUsesTheWorstAxis()
    {
        Vector3 box = new Vector3(0.50f, 0.50f, 0.50f);
        Vector3 skewed = new Vector3(0.51f, 0.51f, 0.10f);
        Assert.IsTrue(ListingBox.IsApproximate(skewed, box));
    }

    [Test]
    public void BoxIsCreatedWithTheRequestedSize()
    {
        GameObject box = ListingBox.Create(
            new Vector3(0.55f, 0.72f, 0.40f), Vector3.zero, Vector3.forward, "drawing-1");
        Assert.IsNotNull(box);
        Assert.AreEqual(0.55f, box.transform.localScale.x, 1e-4f);
        Assert.AreEqual(0.72f, box.transform.localScale.y, 1e-4f);
        Assert.AreEqual(0.40f, box.transform.localScale.z, 1e-4f);
        Object.DestroyImmediate(box);
    }
}
