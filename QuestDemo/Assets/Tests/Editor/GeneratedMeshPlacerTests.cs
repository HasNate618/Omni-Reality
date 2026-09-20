using NUnit.Framework;
using UnityEngine;

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

    [Test]
    public void FetchAttemptBudgetIsBounded()
    {
        Assert.AreEqual(6, GeneratedMeshPlacer.MaxFetchAttempts);
        Assert.Greater(GeneratedMeshPlacer.FetchRetrySeconds, 0f);
    }

    [Test]
    public void FailureLeavesTheListedBoxNotASmallCube()
    {
        // A 55 x 72 x 40 cm listing must never degrade to a 10 cm cube. This
        // is the real failure path: no mesh ever arrives, so the store's
        // listed box is what remains.
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "d-fail", new Vector3(0.55f, 0.40f, 0.72f));
        Transform box = placed.transform.GetChild(0);
        Assert.AreEqual(0.55f, box.localScale.x, 1e-4f);
        Assert.AreEqual(0.72f, box.localScale.y, 1e-4f);
        Assert.AreEqual(0.40f, box.localScale.z, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void StoreRegistersAGeneratedDrawing()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "drawing-1", new Vector3(0.55f, 0.40f, 0.72f));
        Assert.IsNotNull(placed);
        Assert.AreEqual(1, store.Count);
        Assert.IsTrue(store.HasGenerated("drawing-1"));
        Object.DestroyImmediate(go);
    }

    [Test]
    public void GeneratedDrawingCountsTowardTheClutterCap()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        for (int i = 0; i < DrawingStore.MaxDrawings; i++)
            store.PlaceGenerated(Vector3.zero, Vector3.up, "drawing-" + i, null);
        Assert.AreEqual(DrawingStore.MaxDrawings, store.Count);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void ZeroOffsetLeavesTheBoxOnTheHit()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            new Vector3(2f, 0f, 3f), Vector3.up, "d0", new Vector3(0.55f, 0.40f, 0.72f), 0f);
        Assert.AreEqual(2f, placed.transform.position.x, 1e-4f);
        Assert.AreEqual(3f, placed.transform.position.z, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void NonZeroOffsetShiftsAlongTheWidthAxisOnly()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "d0", new Vector3(0.55f, 0.40f, 0.72f), 1.15f);
        // Facing flattens to +Z, so the width axis is +X. Height is the box's
        // half-height, untouched by the offset.
        Assert.AreEqual(1.15f, Mathf.Abs(placed.transform.position.x), 1e-4f);
        Assert.AreEqual(0f, placed.transform.position.z, 1e-4f);
        Assert.AreEqual(0.36f, placed.transform.position.y, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void RootAndBoxCoincideSoAGrabCannotDoubleOffset()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "d0", new Vector3(0.55f, 0.40f, 0.72f));
        Transform box = placed.transform.GetChild(0);
        Assert.AreEqual(0f, box.localPosition.magnitude, 1e-5f);
        Assert.AreEqual(placed.transform.position.y, box.position.y, 1e-5f);
        Object.DestroyImmediate(go);
    }
}
