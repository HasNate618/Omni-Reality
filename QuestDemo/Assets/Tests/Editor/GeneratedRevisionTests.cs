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

    static GameObject NewBoxHolder(Vector3 sizeM)
    {
        var holder = new GameObject("Generated_job");
        GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.transform.SetParent(holder.transform, false);
        cube.transform.localScale = sizeM;
        return holder;
    }

    static GameObject NewCubeHolder(float sizeM)
    {
        return NewBoxHolder(Vector3.one * sizeM);
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

        // Table slot now lists the lamp's 0.30 x 0.45 x 0.25 box: 0.72 m tall
        // -> 0.45 m, so the centre drops by the difference in half-heights and
        // the base stays on the floor at y = 0.
        Assert.AreEqual(1f, table.transform.position.x, 1e-4f);
        Assert.AreEqual(2f, table.transform.position.z, 1e-4f);
        Assert.AreEqual(0.36f - 0.135f, table.transform.position.y, 1e-3f);
        // Lamp slot now lists the table's 0.55 x 0.72 x 0.40 box: 0.45 m ->
        // 0.72 m, the centre rises by the same rule.
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
    public void SwapRefitsTheMeshFromItsImportSizeAcrossGenuinelyDifferentBoxes()
    {
        // §7.3 exchanges meshes as well as extents, and §6.3 fits a mesh from
        // its *import* measurement. The two go together: the mesh carries the
        // listed extent it was fitted to into the other root, whose box is
        // then genuinely different from the one it listed before. Re-fitting
        // an already-fitted mesh instead would shrink it on every swap, and
        // FitScale never enlarges, so the shortfall could never come back.
        GameObject table = PlaceAt("d-table", TableExtentM, new Vector3(1f, 0f, 2f));
        GameObject lamp = PlaceAt("d-lamp", LampExtentM, new Vector3(3f, 0f, 4f));
        GameObject holder = NewCubeHolder(0.6f);
        bool approximate;
        Assert.IsTrue(_store.FitMeshIntoBox("d-table", holder, out approximate));
        // A 0.60 m import in the table's 0.55 x 0.72 x 0.40 box: the binding
        // axis is z at 0.40, so one uniform factor of 0.40 / 0.60 and the cube
        // lands at 0.40 a side. It misses the 25% aspect rule on x and z.
        Assert.IsTrue(approximate);
        Assert.AreEqual(0.40f, FitBounds(holder).size.x, 1e-3f);

        string error;
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-lamp", out error), error);
        // The mesh is now under the lamp root, which lists the table's extent:
        // 0.55 x 0.72 x 0.40, not the 0.30 x 0.45 x 0.25 it listed before.
        // Re-fitted from the 0.60 m import into that box, one factor of
        // 0.40 / 0.60 again -- 0.25 a side would mean the mesh had been fitted
        // into the box this root used to list, and a scale compounded onto the
        // previous fit would have left it smaller still.
        Assert.AreEqual(lamp.transform, holder.transform.parent);
        AssertBox(Box(lamp), 0.55f, 0.72f, 0.40f);
        Assert.AreEqual(0.40f / 0.60f, holder.transform.localScale.x, 1e-3f);
        Assert.AreEqual(0.40f, FitBounds(holder).size.x, 1e-3f);
        Assert.AreEqual(0.40f, FitBounds(holder).size.y, 1e-3f);
        Assert.AreEqual(0.40f, FitBounds(holder).size.z, 1e-3f);
        Assert.AreEqual(0f, Vector3.Distance(lamp.transform.position, FitBounds(holder).center), 1e-3f);

        // Swapping back re-derives the same fit from the same import size. A
        // ratchet would leave the mesh at 0.40 x 0.40 / 0.60 = 0.267 here.
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-lamp", "swap", null, "d-table", out error), error);
        Assert.AreEqual(table.transform, holder.transform.parent);
        AssertBox(Box(table), 0.55f, 0.72f, 0.40f);
        Assert.AreEqual(0.40f / 0.60f, holder.transform.localScale.x, 1e-3f);
        Assert.AreEqual(0.40f, FitBounds(holder).size.x, 1e-3f);
        Assert.AreEqual(0f, Vector3.Distance(table.transform.position, FitBounds(holder).center), 1e-3f);
        Object.DestroyImmediate(holder);
        Object.DestroyImmediate(table);
        Object.DestroyImmediate(lamp);
    }

    [Test]
    public void ASwapThatLeavesTheMeshApproximateChipsIt()
    {
        // §8.1: a mesh that is approximate is spoken for, and the box stays
        // the size claim. The import path chips through the client; a swap
        // re-fits with no placer running, so the store reports its recomputed
        // verdict through the surface the client installed on it.
        GameObject table = PlaceAt("d-table", TableExtentM, new Vector3(1f, 0f, 2f));
        GameObject lamp = PlaceAt("d-lamp", LampExtentM, new Vector3(3f, 0f, 4f));
        string chipped = null;
        _store.ChipSurface = text => chipped = text;
        GameObject holder = NewCubeHolder(0.6f);
        bool approximate;
        Assert.IsTrue(_store.FitMeshIntoBox("d-table", holder, out approximate));
        Assert.IsTrue(approximate);
        Assert.IsNull(chipped, "the import path reports its own fit");

        string error;
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-lamp", out error), error);
        Assert.AreEqual(DrawingStore.ApproximateMeshText, chipped);
        Object.DestroyImmediate(holder);
        Object.DestroyImmediate(table);
        Object.DestroyImmediate(lamp);
    }

    [Test]
    public void ASwapOfAMatchingMeshStaysSilent()
    {
        // §8.1 asks for the honesty only when the mesh misses the listing: the
        // 0.60 m cube in the sibling test is approximate for this box, this
        // import is the box exactly, so the swap stays silent. Unity order is
        // (w, h, d).
        GameObject table = PlaceAt("d-table", TableExtentM, new Vector3(1f, 0f, 2f));
        GameObject lamp = PlaceAt("d-lamp", LampExtentM, new Vector3(3f, 0f, 4f));
        string chipped = null;
        _store.ChipSurface = text => chipped = text;
        GameObject holder = NewBoxHolder(new Vector3(0.55f, 0.72f, 0.40f));
        bool approximate;
        Assert.IsTrue(_store.FitMeshIntoBox("d-table", holder, out approximate));
        Assert.IsFalse(approximate);
        Assert.AreEqual(0.55f, FitBounds(holder).size.x, 1e-3f);

        string error;
        Assert.IsTrue(
            _store.ApplyGeneratedRevision("d-table", "swap", null, "d-lamp", out error), error);
        Assert.IsNull(chipped);
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
