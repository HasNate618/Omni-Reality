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

    [Test]
    public void FittedMeshIsCentredOnItsBoxNotTheWorldOrigin()
    {
        // The imported holder is created at the world origin and its pivot is
        // not its bounds centre, so a reparent that keeps the world pose would
        // leave the mesh rendering at the room origin.
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            new Vector3(1f, 0f, 2f), Vector3.up, "d-fit", new Vector3(0.55f, 0.40f, 0.72f));
        var holder = new GameObject("Generated_job");
        GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.transform.SetParent(holder.transform, false);
        cube.transform.localPosition = new Vector3(0.5f, 0.3f, -0.2f);
        cube.transform.localScale = Vector3.one * 0.6f;

        Assert.IsTrue(store.FitMeshIntoBox("d-fit", holder));
        Assert.AreEqual(placed.transform, holder.transform.parent);

        // Box is 0.55 x 0.72 x 0.40 at (1, 0.36, 2); the 0.6 cube is too big on
        // z, so one uniform factor of 0.4/0.6 shrinks it to 0.40 a side.
        Bounds bounds = cube.GetComponent<Renderer>().bounds;
        Assert.AreEqual(placed.transform.position.x, bounds.center.x, 1e-3f);
        Assert.AreEqual(placed.transform.position.y, bounds.center.y, 1e-3f);
        Assert.AreEqual(placed.transform.position.z, bounds.center.z, 1e-3f);
        Assert.LessOrEqual(bounds.size.x, 0.55f + 1e-3f);
        Assert.LessOrEqual(bounds.size.y, 0.72f + 1e-3f);
        Assert.LessOrEqual(bounds.size.z, 0.40f + 1e-3f);
        Object.DestroyImmediate(placed);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void HorizontalNormalLeavesTheBoxAtTheRootsYaw()
    {
        // A horizontal normal makes ListingBox.Create build a real yaw. The
        // box is not square (0.55 x 0.72 x 0.40), so a rotation applied twice
        // shows as a twisted footprint.
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, new Vector3(1f, 0f, 1f), "d-wall", new Vector3(0.55f, 0.40f, 0.72f));
        Transform box = placed.transform.GetChild(0);
        Assert.Greater(Quaternion.Angle(Quaternion.identity, placed.transform.rotation), 5f);
        Assert.AreEqual(0f, Quaternion.Angle(placed.transform.rotation, box.rotation), 1e-3f);
        Assert.AreEqual(0f, box.localRotation.x, 1e-4f);
        Assert.AreEqual(0f, box.localRotation.y, 1e-4f);
        Assert.AreEqual(0f, box.localRotation.z, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void EvictedGeneratedDrawingIsNoLongerRegistered()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        for (int i = 0; i <= DrawingStore.MaxDrawings; i++)
            store.PlaceGenerated(Vector3.zero, Vector3.up, "drawing-" + i, null);
        Assert.AreEqual(DrawingStore.MaxDrawings, store.Count);
        Assert.IsFalse(store.HasGenerated("drawing-0"));
        Assert.IsTrue(store.HasGenerated("drawing-" + DrawingStore.MaxDrawings));
        Object.DestroyImmediate(go);
    }
}
