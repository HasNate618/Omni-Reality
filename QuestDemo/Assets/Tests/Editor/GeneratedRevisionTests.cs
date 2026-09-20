using NUnit.Framework;
using UnityEngine;

public class GeneratedRevisionTests
{
    /// <summary>A 0.55 x 0.40 x 0.72 m table: Unity box 0.55 x 0.72 x 0.40.</summary>
    static readonly Vector3 TableExtentM = new Vector3(0.55f, 0.40f, 0.72f);

    /// <summary>A 0.30 x 0.25 x 0.45 m lamp: Unity box 0.30 x 0.45 x 0.25.</summary>
    static readonly Vector3 LampExtentM = new Vector3(0.30f, 0.25f, 0.45f);

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
        return _store.PlaceGenerated(Vector3.zero, Vector3.up, id, TableExtentM);
    }

    GameObject PlaceAt(string id, Vector3 extentM, Vector3 point)
    {
        return _store.PlaceGenerated(point, Vector3.up, id, extentM);
    }

    /// <summary>The listed box child of a generated root: child 0, before any mesh.</summary>
    static Transform Box(GameObject placed)
    {
        return placed.transform.GetChild(0);
    }

    static void AssertBox(Transform box, float x, float y, float z)
    {
        Assert.AreEqual(x, box.localScale.x, 1e-4f);
        Assert.AreEqual(y, box.localScale.y, 1e-4f);
        Assert.AreEqual(z, box.localScale.z, 1e-4f);
    }

    static GameObject NewCubeHolder(float sizeM)
    {
        var holder = new GameObject("Generated_job");
        GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.transform.SetParent(holder.transform, false);
        cube.transform.localScale = Vector3.one * sizeM;
        return holder;
    }

    static Bounds FitBounds(GameObject holder)
    {
        Renderer[] renderers = holder.GetComponentsInChildren<Renderer>();
        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++)
            bounds.Encapsulate(renderers[i].bounds);
        return bounds;
    }

    [Test]
    public void NudgeMovesTheWholePlacement()
    {
        GameObject placed = Place("d1");
        float before = placed.transform.position.x;
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "nudge", "right", null, out error));
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
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "rotate_cw", null, null, out error));
    }

    [Test]
    public void EnlargeIsRefusedBecauseTheListingIsTruth()
    {
        Place("d1");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d1", "enlarge", null, null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void ShrinkIsRefusedBecauseTheListingIsTruth()
    {
        Place("d1");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d1", "shrink", null, null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void NudgeOnAnUnknownDrawingIsRefused()
    {
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("nope", "nudge", "left", null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void RemoveDeletesTheGeneratedDrawing()
    {
        Place("d1");
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "remove", null, null, out error));
        Assert.IsFalse(_store.HasGenerated("d1"));
        Assert.AreEqual(0, _store.Count);
    }

    [Test]
    public void RemovingAnUnknownDrawingIsRefused()
    {
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("nope", "remove", null, null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void SwapExchangesExtentsAndKeepsBothDrawings()
    {
        GameObject table = PlaceAt("d-table", TableExtentM, new Vector3(1f, 0f, 2f));
        GameObject lamp = PlaceAt("d-lamp", LampExtentM, new Vector3(3f, 0f, 4f));
        string error;
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-lamp", out error), error);
        Assert.IsNull(error);
        // Spec §7.3: each box now lists what the other one did.
        AssertBox(Box(table), 0.30f, 0.45f, 0.25f);
        AssertBox(Box(lamp), 0.55f, 0.72f, 0.40f);
        Assert.IsTrue(_store.HasGenerated("d-table"));
        Assert.IsTrue(_store.HasGenerated("d-lamp"));
        Assert.AreEqual(2, _store.Count);
        Object.DestroyImmediate(table);
        Object.DestroyImmediate(lamp);
    }

    [Test]
    public void SwapAgainstAnUnknownDrawingIsRefused()
    {
        GameObject table = Place("d-table");
        string error;
        Assert.IsFalse(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-unknown", out error));
        Assert.AreEqual("invalid", error);
        // A refusal leaves both drawings alone: this box still lists its own size.
        AssertBox(Box(table), 0.55f, 0.72f, 0.40f);
        Object.DestroyImmediate(table);
    }

    [Test]
    public void SwapWithoutAnotherDrawingIsRefused()
    {
        GameObject table = Place("d-table");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d-table", "swap", null, null, out error));
        Assert.AreEqual("invalid", error);
        AssertBox(Box(table), 0.55f, 0.72f, 0.40f);
        Object.DestroyImmediate(table);
    }

    [Test]
    public void SwappingADrawingWithItselfIsRefused()
    {
        // An exchange with itself changes nothing, so ACKing it as applied
        // would confirm a revision the wearer can never see.
        GameObject table = Place("d-table");
        string error;
        Assert.IsFalse(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-table", out error));
        Assert.AreEqual("invalid", error);
        Object.DestroyImmediate(table);
    }

    [Test]
    public void SwapKeepsTheFootprintAndTheSurfaceContact()
    {
        // "Does not move either drawing" means the same place in the room --
        // same footprint, same surface contact -- not the same centre height.
        // Holding the centre would leave the shorter item floating (spec §6.2
        // rests the box on the surface it was planted on).
        GameObject table = PlaceAt("d-table", TableExtentM, new Vector3(1f, 0f, 2f));
        GameObject lamp = PlaceAt("d-lamp", LampExtentM, new Vector3(3f, 0f, 4f));
        string error;
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-lamp", out error), error);

        // Table slot, 0.72 m tall box -> 0.25 m: the centre drops by the
        // difference in half-heights, so the base stays on the floor at y = 0.
        Assert.AreEqual(1f, table.transform.position.x, 1e-4f);
        Assert.AreEqual(2f, table.transform.position.z, 1e-4f);
        Assert.AreEqual(0.36f - 0.235f, table.transform.position.y, 1e-3f);
        // Lamp slot, 0.45 m -> 0.72 m: the centre rises by the same rule.
        Assert.AreEqual(3f, lamp.transform.position.x, 1e-4f);
        Assert.AreEqual(4f, lamp.transform.position.z, 1e-4f);
        Assert.AreEqual(0.225f + 0.135f, lamp.transform.position.y, 1e-3f);
        // The grab locks Y to the planted surface, which just moved.
        Assert.AreEqual(table.transform.position.y,
            table.GetComponent<FloorPlaneGrab>().LockedY, 1e-4f);
        Assert.AreEqual(lamp.transform.position.y,
            lamp.GetComponent<FloorPlaneGrab>().LockedY, 1e-4f);
        // Root and box stay coincident across the re-seat.
        Assert.AreEqual(0f, Box(table).localPosition.magnitude, 1e-5f);
        Assert.AreEqual(table.transform.position.y, Box(table).position.y, 1e-5f);
        Object.DestroyImmediate(table);
        Object.DestroyImmediate(lamp);
    }

    [Test]
    public void SwapCarriesTheFittedMeshWithoutRescalingItTwice()
    {
        // §7.3 exchanges meshes as well as extents. Re-fitting must start from
        // the mesh's import size: re-fitting an already-fitted mesh shrinks it
        // on every swap, under-filling the box the wearer is being sold.
        GameObject table = PlaceAt("d-table", TableExtentM, new Vector3(1f, 0f, 2f));
        GameObject lamp = PlaceAt("d-lamp", LampExtentM, new Vector3(3f, 0f, 4f));
        GameObject holder = NewCubeHolder(0.6f);
        bool approximate;
        Assert.IsTrue(_store.FitMeshIntoBox("d-table", holder, out approximate));
        // A 0.6 m cube in a 0.55 x 0.72 x 0.40 box: one factor of 0.40 / 0.60,
        // so it stays a cube, and it misses the 25% aspect rule on x and z.
        Assert.IsTrue(approximate);
        Assert.AreEqual(0.40f, FitBounds(holder).size.x, 1e-3f);
        Assert.AreEqual(0.40f, FitBounds(holder).size.y, 1e-3f);
        Assert.AreEqual(0.40f, FitBounds(holder).size.z, 1e-3f);

        string error;
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-lamp", out error), error);
        Assert.AreEqual(lamp.transform, holder.transform.parent);
        // Now in a 0.30 x 0.45 x 0.25 box: one factor of 0.25 / 0.60, still a
        // cube, and still centred on the box it was fitted into.
        Assert.AreEqual(0.25f, FitBounds(holder).size.x, 1e-3f);
        Assert.AreEqual(0.25f, FitBounds(holder).size.y, 1e-3f);
        Assert.AreEqual(0.25f, FitBounds(holder).size.z, 1e-3f);
        Assert.AreEqual(0f, Vector3.Distance(lamp.transform.position, FitBounds(holder).center), 1e-3f);

        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-lamp", "swap", null, "d-table", out error), error);
        Assert.AreEqual(table.transform, holder.transform.parent);
        // The round trip lands on the original fit; a ratchet would leave 0.25.
        Assert.AreEqual(0.40f, FitBounds(holder).size.x, 1e-3f);
        Assert.AreEqual(0f, Vector3.Distance(table.transform.position, FitBounds(holder).center), 1e-3f);
        Object.DestroyImmediate(holder);
        Object.DestroyImmediate(table);
        Object.DestroyImmediate(lamp);
    }

    [Test]
    public void ASwapOpCarriesTheOtherDrawingFromItsTarget()
    {
        // §7.3 addresses the other drawing through the op's target, the only
        // field the wire spends on it, and CoordinatorClient hands that to
        // ApplyGeneratedRevision. Without it the verb could never arrive.
        const string payload =
            "{\"op_id\":\"01k00000000000000000000001\",\"turn_id\":1,\"stage_epoch\":0,"
            + "\"kind\":\"revise_procedural\",\"drawing_id\":\"01k0000000000000000000000a\","
            + "\"action\":\"swap\","
            + "\"target\":{\"type\":\"drawing\",\"drawing_id\":\"01k0000000000000000000000b\"}}";
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(payload, out op));
        Assert.AreEqual("swap", op.Action);
        Assert.IsTrue(op.HasTargetDrawingId);
        Assert.AreEqual("01k0000000000000000000000b", op.TargetDrawingId);
    }

    [Test]
    public void ATargetThatIsNotADrawingCarriesNoOtherDrawing()
    {
        const string payload =
            "{\"op_id\":\"01k00000000000000000000001\",\"turn_id\":1,\"stage_epoch\":0,"
            + "\"kind\":\"revise_procedural\",\"drawing_id\":\"01k0000000000000000000000a\","
            + "\"action\":\"nudge\",\"direction\":\"left\","
            + "\"target\":{\"type\":\"image_point\",\"frame_id\":\"01k0000000000000000000000c\","
            + "\"u\":0.5,\"v\":0.5}}";
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(payload, out op));
        Assert.IsFalse(op.HasTargetDrawingId);
        Assert.IsNull(op.TargetDrawingId);
        // The frame_id parse is untouched by the new one.
        Assert.AreEqual("01k0000000000000000000000c", op.TargetFrameId);
    }
}
