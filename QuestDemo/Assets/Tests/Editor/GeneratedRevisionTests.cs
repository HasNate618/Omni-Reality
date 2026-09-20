using NUnit.Framework;
using UnityEngine;

public class GeneratedRevisionTests
{
    DrawingStore _store;

    [SetUp]
    public void SetUp()
    {
        _store = new GameObject("store").AddComponent<DrawingStore>();
    }

    [TearDown]
    public void TearDown()
    {
        Object.DestroyImmediate(_store.gameObject);
    }

    GameObject Place(string id)
    {
        return _store.PlaceGenerated(
            Vector3.zero, Vector3.up, id, new Vector3(0.55f, 0.40f, 0.72f));
    }

    [Test]
    public void NudgeMovesTheWholePlacement()
    {
        GameObject placed = Place("d1");
        float before = placed.transform.position.x;
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "nudge", "right", out error));
        Assert.IsNull(error);
        // The root moved. The store's transform cannot be searched for it:
        // PlaceGenerated plants the root at the scene root, world-locked.
        Assert.Greater(placed.transform.position.x, before);
        // Root and box stay coincident, the Task 10 invariant a grab rides on.
        Transform box = placed.transform.GetChild(0);
        Assert.AreEqual(0f, box.localPosition.magnitude, 1e-5f);
        Assert.AreEqual(placed.transform.position.x, box.position.x, 1e-5f);
    }

    [Test]
    public void RotateIsAllowed()
    {
        Place("d1");
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "rotate_cw", null, out error));
    }

    [Test]
    public void EnlargeIsRefusedBecauseTheListingIsTruth()
    {
        Place("d1");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d1", "enlarge", null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void ShrinkIsRefusedBecauseTheListingIsTruth()
    {
        Place("d1");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d1", "shrink", null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void NudgeOnAnUnknownDrawingIsRefused()
    {
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("nope", "nudge", "left", out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void RemoveDeletesTheGeneratedDrawing()
    {
        Place("d1");
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "remove", null, out error));
        Assert.IsFalse(_store.HasGenerated("d1"));
        Assert.AreEqual(0, _store.Count);
    }

    [Test]
    public void RemovingAnUnknownDrawingIsRefused()
    {
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("nope", "remove", null, out error));
        Assert.AreEqual("invalid", error);
    }
}
