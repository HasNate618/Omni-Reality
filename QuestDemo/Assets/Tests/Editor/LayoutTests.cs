using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;

/// <summary>
/// Layout mode EditMode tests: listing sizes, corner-relative slot maths,
/// footprint picking, and the hedged clearance copy.
///
/// These cover the parts that must be right before anyone puts the headset
/// on. What they cannot prove is on-device: whether the corner raycasts find
/// real walls, and whether a dragged box stays where it was dropped.
/// </summary>
public class LayoutTests
{
    static LayoutRoom.CornerFrame SquareCorner()
    {
        return new LayoutRoom.CornerFrame
        {
            Origin = Vector3.zero,
            AxisX = Vector3.right,
            AxisZ = Vector3.forward,
            FromWalls = true,
        };
    }

    static LayoutBox MakeBox(string label, Vector3 sizeM, Vector3 pos, float yawDeg)
    {
        LayoutBox box = LayoutBox.Create(label, sizeM, LayoutMode.DefaultTint);
        box.transform.SetPositionAndRotation(pos, Quaternion.Euler(0f, yawDeg, 0f));
        return box;
    }

    [Test]
    public void ImplausibleSizesAreRejected()
    {
        Assert.That(LayoutBox.IsUsableSize(new Vector3(1.8f, 0.83f, 0.9f)), Is.True);
        Assert.That(LayoutBox.IsUsableSize(new Vector3(0f, 0.83f, 0.9f)), Is.False);
        Assert.That(LayoutBox.IsUsableSize(new Vector3(9f, 0.83f, 0.9f)), Is.False);
        Assert.That(LayoutBox.IsUsableSize(new Vector3(float.NaN, 0.83f, 0.9f)), Is.False);
        Assert.That(LayoutBox.Create("bad", new Vector3(0f, 1f, 1f), Color.cyan), Is.Null);
    }

    [Test]
    public void BoxIsSeatedOnTheFloorNotCentredOnIt()
    {
        Vector3 pos;
        Quaternion rot;
        LayoutBox.SlotToWorld(SquareCorner(), 1.05f, 0.5f, 0f, 0.83f, out pos, out rot);
        // Centre sits at half height, so the base rests on y = 0.
        Assert.That(pos.y, Is.EqualTo(0.415f).Within(1e-4f));
        Assert.That(pos.x, Is.EqualTo(1.05f).Within(1e-4f));
        Assert.That(pos.z, Is.EqualTo(0.5f).Within(1e-4f));
    }

    [Test]
    public void SlotsAreRelativeToTheCornerNotTheWorld()
    {
        var corner = new LayoutRoom.CornerFrame
        {
            Origin = new Vector3(3f, 0f, -2f),
            AxisX = Vector3.forward,
            AxisZ = Vector3.left,
            FromWalls = true,
        };
        Vector3 pos;
        Quaternion rot;
        LayoutBox.SlotToWorld(corner, 1f, 2f, 0f, 1f, out pos, out rot);
        Assert.That(pos.x, Is.EqualTo(1f).Within(1e-4f));
        Assert.That(pos.z, Is.EqualTo(-1f).Within(1e-4f));
    }

    [Test]
    public void WallIntersectionFindsTheCorner()
    {
        Vector3 corner;
        bool ok = LayoutRoom.TryIntersectWalls(
            new Vector3(2f, 0f, 0f), Vector3.forward,
            new Vector3(0f, 0f, 2f), Vector3.right,
            out corner);
        Assert.That(ok, Is.True);
        Assert.That(corner.x, Is.EqualTo(0f).Within(1e-3f));
        Assert.That(corner.z, Is.EqualTo(0f).Within(1e-3f));
    }

    [Test]
    public void ParallelWallsAreNotACorner()
    {
        Vector3 corner;
        bool ok = LayoutRoom.TryIntersectWalls(
            new Vector3(0f, 0f, 0f), Vector3.forward,
            new Vector3(0f, 0f, 3f), Vector3.forward,
            out corner);
        Assert.That(ok, Is.False);
    }

    [Test]
    public void AimRayMeetsTheFloorOnlyWhenItPointsDown()
    {
        Vector3 hit;
        Assert.That(LayoutDrag.TryFloorPoint(
            new Ray(new Vector3(0f, 1.5f, 0f), Vector3.down), 0f, out hit), Is.True);
        Assert.That(hit.y, Is.EqualTo(0f).Within(1e-4f));

        Assert.That(LayoutDrag.TryFloorPoint(
            new Ray(new Vector3(0f, 1.5f, 0f), Vector3.up), 0f, out hit), Is.False);
        // Far beyond arm's reach: not a grab.
        Assert.That(LayoutDrag.TryFloorPoint(
            new Ray(new Vector3(0f, 50f, 0f), Vector3.down), 0f, out hit), Is.False);
    }

    [Test]
    public void FootprintPickingRespectsRotation()
    {
        LayoutBox box = MakeBox("sofa", new Vector3(1.8f, 0.83f, 0.6f), Vector3.zero, 90f);
        try
        {
            // Rotated 90 degrees, the long axis now runs along z.
            Assert.That(LayoutDrag.ContainsOnFloor(box, new Vector2(0f, 0.8f)), Is.True);
            Assert.That(LayoutDrag.ContainsOnFloor(box, new Vector2(0.8f, 0f)), Is.False);
        }
        finally
        {
            Object.DestroyImmediate(box.gameObject);
        }
    }

    [Test]
    public void PickAtReturnsNullWhenNothingIsUnderTheRay()
    {
        var boxes = new List<LayoutBox>();
        LayoutBox box = MakeBox("table", new Vector3(0.5f, 0.45f, 0.5f), Vector3.zero, 0f);
        boxes.Add(box);
        try
        {
            Assert.That(LayoutDrag.PickAt(boxes, new Vector2(0f, 0f)), Is.SameAs(box));
            Assert.That(LayoutDrag.PickAt(boxes, new Vector2(4f, 4f)), Is.Null);
        }
        finally
        {
            Object.DestroyImmediate(box.gameObject);
        }
    }

    [Test]
    public void ClearanceIsNegativeWhenFootprintsOverlap()
    {
        LayoutBox a = MakeBox("sofa", new Vector3(1.8f, 0.83f, 0.9f), Vector3.zero, 0f);
        LayoutBox b = MakeBox("chair", new Vector3(0.8f, 1f, 0.8f), new Vector3(0.5f, 0f, 0f), 0f);
        try
        {
            Assert.That(LayoutMode.Clearance(a, b), Is.LessThan(0f));
        }
        finally
        {
            Object.DestroyImmediate(a.gameObject);
            Object.DestroyImmediate(b.gameObject);
        }
    }

    [Test]
    public void ClearanceMatchesTheGapBetweenEdges()
    {
        LayoutBox a = MakeBox("sofa", new Vector3(1f, 0.83f, 1f), Vector3.zero, 0f);
        LayoutBox b = MakeBox("chair", new Vector3(1f, 1f, 1f), new Vector3(2.5f, 0f, 0f), 0f);
        try
        {
            // Centres 2.5 m apart, half-extents 0.5 each: a 1.5 m gap.
            Assert.That(LayoutMode.Clearance(a, b), Is.EqualTo(1.5f).Within(1e-3f));
        }
        finally
        {
            Object.DestroyImmediate(a.gameObject);
            Object.DestroyImmediate(b.gameObject);
        }
    }

    [Test]
    public void FitVerdictIsAlwaysHedgedAndNeverClaimsMeasurement()
    {
        LayoutBox a = MakeBox("sofa", new Vector3(1f, 0.83f, 1f), Vector3.zero, 0f);
        LayoutBox b = MakeBox("chair", new Vector3(1f, 1f, 1f), new Vector3(2.5f, 0f, 0f), 0f);
        try
        {
            string verdict = LayoutMode.FitVerdict(new List<LayoutBox> { a, b });
            Assert.That(verdict, Does.StartWith("Roughly"));
            Assert.That(verdict, Does.Contain("sofa"));
            Assert.That(verdict, Does.Contain("chair"));
        }
        finally
        {
            Object.DestroyImmediate(a.gameObject);
            Object.DestroyImmediate(b.gameObject);
        }
    }

    [Test]
    public void OverlapIsReportedAsOverlapNotAsClearance()
    {
        LayoutBox a = MakeBox("sofa", new Vector3(1.8f, 0.83f, 0.9f), Vector3.zero, 0f);
        LayoutBox b = MakeBox("chair", new Vector3(0.8f, 1f, 0.8f), new Vector3(0.3f, 0f, 0f), 0f);
        try
        {
            Assert.That(LayoutMode.FitVerdict(new List<LayoutBox> { a, b }),
                        Does.Contain("overlap"));
        }
        finally
        {
            Object.DestroyImmediate(a.gameObject);
            Object.DestroyImmediate(b.gameObject);
        }
    }

    [Test]
    public void ASingleBoxHasNothingToReport()
    {
        LayoutBox a = MakeBox("sofa", new Vector3(1f, 0.83f, 1f), Vector3.zero, 0f);
        try
        {
            Assert.That(LayoutMode.FitVerdict(new List<LayoutBox> { a }), Is.Empty);
            Assert.That(LayoutMode.FitVerdict(null), Is.Empty);
        }
        finally
        {
            Object.DestroyImmediate(a.gameObject);
        }
    }

    [Test]
    public void SizeCaptionReportsTheListingInCentimetres()
    {
        LayoutBox a = MakeBox("sofa", new Vector3(1.8f, 0.83f, 0.9f), Vector3.zero, 0f);
        try
        {
            string caption = a.SizeCaption();
            Assert.That(caption, Does.Contain("sofa"));
            Assert.That(caption, Does.Contain("180"));
            Assert.That(caption, Does.Contain("90"));
            Assert.That(caption, Does.Contain("83"));
        }
        finally
        {
            Object.DestroyImmediate(a.gameObject);
        }
    }

    [Test]
    public void TintFallsBackWhenTheColourIsUnparseable()
    {
        Assert.That(LayoutMode.ParseTint(null), Is.EqualTo(LayoutMode.DefaultTint));
        Assert.That(LayoutMode.ParseTint("not-a-colour"), Is.EqualTo(LayoutMode.DefaultTint));
        Color parsed = LayoutMode.ParseTint("#3DDCFF");
        Assert.That(parsed.a, Is.EqualTo(LayoutMode.BoxAlpha).Within(1e-4f));
        Assert.That(parsed.b, Is.GreaterThan(0.9f));
    }

    [Test]
    public void HonestyCopyNamesBothSourcesOfError()
    {
        Assert.That(LayoutMode.HonestyCopy.ToLower(), Does.Contain("approximate"));
        Assert.That(LayoutMode.HonestyCopy.ToLower(), Does.Contain("listing"));
    }

    [Test]
    public void PlaceBoxOpParsesSizeAndSlot()
    {
        const string payload =
            "{\"op_id\":\"01k5j8g0038q3m7b2d6h9n4r5v\",\"turn_id\":1,\"stage_epoch\":1," +
            "\"kind\":\"place_box\",\"drawing_id\":null," +
            "\"target\":{\"type\":\"layout_slot\",\"dx\":1.05,\"dz\":0.5,\"yaw_deg\":-90}," +
            "\"size_m\":{\"w\":1.8,\"d\":0.9,\"h\":0.83}," +
            "\"style\":{\"color\":\"#3DDCFF\",\"label\":\"2-seat sofa\"}}";
        ProtocolJson.SceneOpMsg op;
        Assert.That(ProtocolJson.TryParseSceneOp(payload, out op), Is.True);
        Assert.That(op.Kind, Is.EqualTo("place_box"));
        Assert.That(op.HasSize, Is.True);
        Assert.That(op.SizeW, Is.EqualTo(1.8f).Within(1e-4f));
        Assert.That(op.SizeD, Is.EqualTo(0.9f).Within(1e-4f));
        Assert.That(op.SizeH, Is.EqualTo(0.83f).Within(1e-4f));
        Assert.That(op.HasSlot, Is.True);
        Assert.That(op.SlotDx, Is.EqualTo(1.05f).Within(1e-4f));
        Assert.That(op.SlotYawDeg, Is.EqualTo(-90f).Within(1e-4f));
        Assert.That(op.StyleLabel, Is.EqualTo("2-seat sofa"));
        Assert.That(op.StyleColor, Is.EqualTo("#3DDCFF"));
    }
}

/// <summary>
/// Furniture, the selector, and resizing. The honesty rule under test here is
/// that a resized piece stops claiming the listing's dimensions.
/// </summary>
public class FurnitureTests
{
    static GameObject _eye;

    [SetUp]
    public void MakeEye()
    {
        _eye = new GameObject("CenterEyeAnchor");
        _eye.transform.position = new Vector3(0f, 1.6f, 0f);
        _eye.transform.rotation = Quaternion.identity;
    }

    [TearDown]
    public void DropEye()
    {
        if (_eye != null)
            Object.DestroyImmediate(_eye);
    }

    [Test]
    public void CatalogEntriesAreAllBuildableSizes()
    {
        Assert.That(FurnitureCatalog.Count, Is.EqualTo(6));
        foreach (FurnitureCatalog.Entry e in FurnitureCatalog.All)
        {
            Assert.That(LayoutBox.IsUsableSize(e.SizeM), Is.True, e.Kind);
            Assert.That(FurnitureCatalog.IsKnown(e.Kind), Is.True, e.Kind);
            Assert.That(e.Label, Is.Not.Null.And.Not.Empty);
        }
    }

    [Test]
    public void UnknownKindFallsBackInsteadOfThrowing()
    {
        Assert.That(FurnitureCatalog.IsKnown("hovercraft"), Is.False);
        Assert.That(FurnitureCatalog.IsKnown(null), Is.False);
        Assert.That(FurnitureCatalog.Find("hovercraft").Kind, Is.EqualTo(FurnitureCatalog.Sofa));
    }

    [Test]
    public void EveryKindBuildsGeometryInsideItsListingSize()
    {
        foreach (FurnitureCatalog.Entry e in FurnitureCatalog.All)
        {
            LayoutBox box = LayoutBox.Create(e.Label, e.SizeM, e.Tint, e.Kind);
            try
            {
                Assert.That(box, Is.Not.Null, e.Kind);
                var renderers = box.GetComponentsInChildren<MeshRenderer>();
                Assert.That(renderers.Length, Is.GreaterThan(1), e.Kind + " built no parts");

                Bounds b = renderers[0].bounds;
                for (int i = 1; i < renderers.Length; i++)
                    b.Encapsulate(renderers[i].bounds);
                // A little slack for the label, which sits above the piece.
                Assert.That(b.size.x, Is.LessThanOrEqualTo(e.SizeM.x + 0.02f), e.Kind + " too wide");
                Assert.That(b.size.z, Is.LessThanOrEqualTo(e.SizeM.z + 0.02f), e.Kind + " too deep");
            }
            finally
            {
                if (box != null)
                    Object.DestroyImmediate(box.gameObject);
            }
        }
    }

    [Test]
    public void FurnitureStandsOnTheFloorNotThroughIt()
    {
        FurnitureCatalog.Entry e = FurnitureCatalog.Find(FurnitureCatalog.Sofa);
        LayoutBox box = LayoutBox.Create(e.Label, e.SizeM, e.Tint, e.Kind);
        box.transform.position = new Vector3(0f, e.SizeM.y * 0.5f, 0f);
        try
        {
            var renderers = box.GetComponentsInChildren<MeshRenderer>();
            float lowest = float.MaxValue;
            foreach (MeshRenderer r in renderers)
                lowest = Mathf.Min(lowest, r.bounds.min.y);
            Assert.That(lowest, Is.GreaterThan(-0.02f), "furniture sinks below the floor");
        }
        finally
        {
            Object.DestroyImmediate(box.gameObject);
        }
    }

    [Test]
    public void ResizeScaleFollowsHandTravel()
    {
        // Moving out by one doubling distance doubles the piece.
        Assert.That(LayoutResize.ScaleFor(1f, 1f + LayoutResize.MetresPerDoubling, 1f),
                    Is.EqualTo(2f).Within(0.01f));
        Assert.That(LayoutResize.ScaleFor(1f, 1f - LayoutResize.MetresPerDoubling, 1f),
                    Is.EqualTo(0.5f).Within(0.01f));
        Assert.That(LayoutResize.ScaleFor(1f, 1f, 1f), Is.EqualTo(1f).Within(0.001f));
    }

    [Test]
    public void ResizeIsClampedToSaneFurniture()
    {
        Assert.That(LayoutResize.ScaleFor(1f, 40f, 1f), Is.EqualTo(LayoutBox.MaxScale).Within(1e-3f));
        Assert.That(LayoutResize.ScaleFor(40f, 1f, 1f), Is.EqualTo(LayoutBox.MinScale).Within(1e-3f));
    }

    [Test]
    public void ApplyScaleKeepsThePieceOnTheFloor()
    {
        FurnitureCatalog.Entry e = FurnitureCatalog.Find(FurnitureCatalog.Armchair);
        LayoutBox box = LayoutBox.Create(e.Label, e.SizeM, e.Tint, e.Kind);
        box.transform.position = new Vector3(0f, e.SizeM.y * 0.5f, 0f);
        try
        {
            box.ApplyScale(1.5f);
            float floorY = box.transform.position.y - (box.SizeM.y * 0.5f);
            Assert.That(floorY, Is.EqualTo(0f).Within(1e-3f));
            Assert.That(box.SizeM.y, Is.GreaterThan(e.SizeM.y));
        }
        finally
        {
            Object.DestroyImmediate(box.gameObject);
        }
    }

    [Test]
    public void AResizedPieceStopsClaimingTheListingSize()
    {
        FurnitureCatalog.Entry e = FurnitureCatalog.Find(FurnitureCatalog.Sofa);
        LayoutBox box = LayoutBox.Create(e.Label, e.SizeM, e.Tint, e.Kind);
        try
        {
            Assert.That(box.IsResized, Is.False);
            Assert.That(box.SizeCaption(), Does.Not.Contain("resized"));

            box.ApplyScale(1.4f);

            Assert.That(box.IsResized, Is.True);
            Assert.That(box.SizeCaption(), Does.Contain("resized, not the listing"));
            Assert.That(box.ListingSizeM, Is.EqualTo(e.SizeM), "listing size must be remembered");
        }
        finally
        {
            Object.DestroyImmediate(box.gameObject);
        }
    }

    [Test]
    public void ScalingIsAnchoredToTheListingSoRepeatedGrabsDoNotDrift()
    {
        FurnitureCatalog.Entry e = FurnitureCatalog.Find(FurnitureCatalog.SideTable);
        LayoutBox box = LayoutBox.Create(e.Label, e.SizeM, e.Tint, e.Kind);
        try
        {
            box.ApplyScale(2f);
            box.ApplyScale(0.5f);
            Assert.That(box.ScaleFactor, Is.EqualTo(1f).Within(0.01f));
            Assert.That(box.SizeM.x, Is.EqualTo(e.SizeM.x).Within(1e-3f));
        }
        finally
        {
            Object.DestroyImmediate(box.gameObject);
        }
    }

    [Test]
    public void SelectorPicksTheCardUnderTheRay()
    {
        var go = new GameObject("Selector");
        var selector = go.AddComponent<FurnitureSelector>();
        try
        {
            selector.Open();
            Assert.That(selector.IsOpen, Is.True);

            // Straight through the middle card row from the eye.
            Vector3 centre = selector.transform.position;
            var ray = new Ray(centre - (selector.transform.forward * 1f), selector.transform.forward);
            Vector3 hit;
            int index = selector.IndexUnderRay(ray, out hit);
            Assert.That(index, Is.InRange(0, FurnitureCatalog.Count - 1));

            // Well above the menu selects nothing.
            var high = new Ray(centre - (selector.transform.forward * 1f) + (Vector3.up * 2f),
                               selector.transform.forward);
            Assert.That(selector.IndexUnderRay(high, out hit), Is.EqualTo(-1));
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void ClosedSelectorNeverPicks()
    {
        var go = new GameObject("Selector");
        var selector = go.AddComponent<FurnitureSelector>();
        try
        {
            Vector3 hit;
            Assert.That(selector.IsOpen, Is.False);
            Assert.That(selector.IndexUnderRay(new Ray(Vector3.zero, Vector3.forward), out hit),
                        Is.EqualTo(-1));
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void PreviewKeepsProportionsAndFitsOnACard()
    {
        Vector3 preview = FurnitureSelector.PreviewSize(new Vector3(1.8f, 0.83f, 0.9f));
        Assert.That(Mathf.Max(preview.x, Mathf.Max(preview.y, preview.z)),
                    Is.EqualTo(0.14f).Within(1e-3f));
        Assert.That(preview.x / preview.z, Is.EqualTo(2f).Within(0.01f));
    }

    [Test]
    public void LabelTextIsReadableAgainstPassthrough()
    {
        Color bright = LayoutLabel.Readable(new Color(0f, 0.2f, 0.3f, 1f));
        Assert.That(bright.r, Is.GreaterThan(0.5f));
        Assert.That(bright.a, Is.EqualTo(1f));
        Assert.That(LayoutLabel.CharacterSize, Is.GreaterThan(0.005f),
                    "layout labels must be far larger than VoiceCaption's 0.003");
    }

    [Test]
    public void PlaceBoxOpCarriesTheFurnitureKind()
    {
        const string payload =
            "{\"op_id\":\"01k5j8g0038q3m7b2d6h9n4r5v\",\"turn_id\":1,\"stage_epoch\":1," +
            "\"kind\":\"place_box\",\"drawing_id\":null,\"furniture\":\"bookshelf\"," +
            "\"target\":{\"type\":\"layout_slot\",\"dx\":1.0,\"dz\":0.5,\"yaw_deg\":0}," +
            "\"size_m\":{\"w\":0.8,\"d\":0.3,\"h\":1.06}," +
            "\"style\":{\"color\":\"#F06292\",\"label\":\"bookshelf\"}}";
        ProtocolJson.SceneOpMsg op;
        Assert.That(ProtocolJson.TryParseSceneOp(payload, out op), Is.True);
        Assert.That(op.Furniture, Is.EqualTo("bookshelf"));
    }
}
