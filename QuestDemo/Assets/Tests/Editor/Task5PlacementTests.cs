using NUnit.Framework;
using UnityEngine;

/// <summary>
/// Task 5 EditMode tests: pulsing ring defaults, placement resolver honesty,
/// honesty-chip pose/timing, and drawing-store cap/world-lock.
/// These ran red before implementation (missing PulsingRing,
/// PlacementResolver, HonestyChip, DrawingStore types) and must pass after.
/// Headset pass/fail (90-degree walk, forced miss) still requires a device.
/// </summary>
public class Task5PlacementTests
{
    // Brief step 1: failing test for the pulse default.
    [Test]
    public void PulseDefaultPeriodIs1Point2Seconds()
    {
        Assert.That(PulsingRing.DefaultPeriodS, Is.EqualTo(1.2f));
        var go = new GameObject("TestRingDefault");
        try
        {
            var ring = go.AddComponent<PulsingRing>();
            Assert.That(ring.periodS, Is.EqualTo(1.2f));
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void RingDiameterDefaultsAndClamps()
    {
        Assert.That(PulsingRing.DefaultDiameterM, Is.EqualTo(0.06f));
        Assert.That(PulsingRing.ClampDiameter(0.06f), Is.EqualTo(0.06f));
        Assert.That(PulsingRing.ClampDiameter(0.001f), Is.EqualTo(0.03f));
        Assert.That(PulsingRing.ClampDiameter(10f), Is.EqualTo(0.25f));
    }

    [Test]
    public void PulseLoopsEveryPeriod()
    {
        Assert.That(PulsingRing.PulseScaleFactor(0f),
            Is.EqualTo(PulsingRing.PulseScaleFactor(1f)).Within(1e-5f));
        Assert.That(PulsingRing.PulseAlpha(0f),
            Is.EqualTo(PulsingRing.PulseAlpha(1f)).Within(1e-5f));
        foreach (float phase in new float[] { 0f, 0.25f, 0.5f, 0.75f })
        {
            Assert.That(PulsingRing.PulseScaleFactor(phase),
                Is.InRange(0.85f, 1.15f), "phase " + phase);
            Assert.That(PulsingRing.PulseAlpha(phase),
                Is.InRange(0.55f - 1e-6f, 1.0f + 1e-6f), "phase " + phase);
        }
    }

    [Test]
    public void RingColorIsUnlitCyan()
    {
        Color expected = new Color(0x3D / 255f, 0xDC / 255f, 1f, 1f);
        Assert.That(PulsingRing.RingColor.r, Is.EqualTo(expected.r).Within(1e-4f));
        Assert.That(PulsingRing.RingColor.g, Is.EqualTo(expected.g).Within(1e-4f));
        Assert.That(PulsingRing.RingColor.b, Is.EqualTo(expected.b).Within(1e-4f));
    }

    [Test]
    public void RingMeshIsWorldLockedAnnulus()
    {
        GameObject ring = PulsingRing.CreateRing(0.06f);
        try
        {
            Assert.That(ring.transform.parent, Is.Null, "ring must never attach to a camera");
            Assert.That(ring.GetComponentInParent<Camera>(), Is.Null);
            Assert.That(ring.GetComponent<PulsingRing>(), Is.Not.Null);
            var filter = ring.GetComponent<MeshFilter>();
            Assert.That(filter, Is.Not.Null);
            Assert.That(filter.sharedMesh, Is.Not.Null);
            Assert.That(filter.sharedMesh.vertexCount, Is.GreaterThan(0));
        }
        finally
        {
            Object.DestroyImmediate(ring);
        }
    }

    static CaptureGeometryCache.Entry HitEntry(Vector3 point, Vector3 normal)
    {
        return new CaptureGeometryCache.Entry
        {
            HasHit = true,
            HitPoint = point,
            HasNormal = true,
            HitNormal = normal,
            RayOrigin = new Vector3(0f, 1.6f, 0f),
            RayDirection = Vector3.forward,
        };
    }

    static CaptureGeometryCache.Entry MissEntry()
    {
        return new CaptureGeometryCache.Entry
        {
            HasHit = false,
            RayOrigin = new Vector3(0f, 1.6f, 0f),
            RayDirection = Vector3.forward,
        };
    }

    [Test]
    public void TooCloseProducesExactCopyAndNoPin()
    {
        var result = PlacementResolver.TryPlaceFromCapture(
            HitEntry(new Vector3(0f, 1f, 1f), Vector3.up),
            "too_close", new Vector3(0f, 1f, 1f), true);
        Assert.That(result.Outcome, Is.EqualTo(PlacementOutcome.TooClose));
        Assert.That(result.ChipText, Is.EqualTo("Too close for depth."));
        Assert.That(result.ShouldPin, Is.False);
    }

    [Test]
    public void NoSurfaceProducesExactCopyAndNoPin()
    {
        var result = PlacementResolver.TryPlaceFromCapture(
            MissEntry(), "no_surface", null, true);
        Assert.That(result.Outcome, Is.EqualTo(PlacementOutcome.NoSurface));
        Assert.That(result.ChipText, Is.EqualTo("I can't plant that on a surface."));
        Assert.That(result.ShouldPin, Is.False);
    }

    [Test]
    public void StaleProducesExactCopyAndNoPin()
    {
        var result = PlacementResolver.TryPlaceFromCapture(
            HitEntry(Vector3.zero, Vector3.up),
            "placed", new Vector3(0f, 0f, 1f), true);
        Assert.That(result.Outcome, Is.EqualTo(PlacementOutcome.Stale));
        Assert.That(result.ChipText, Is.EqualTo("That moved, look again."));
        Assert.That(result.ShouldPin, Is.False);
    }

    [Test]
    public void AgreeingDelayedHitPinsAtCapturePoint()
    {
        Vector3 cached = new Vector3(0f, 1f, 2f);
        var result = PlacementResolver.TryPlaceFromCapture(
            HitEntry(cached, Vector3.up), "placed", cached + new Vector3(0f, 0f, 0.05f), true);
        Assert.That(result.Outcome, Is.EqualTo(PlacementOutcome.Placed));
        Assert.That(result.ShouldPin, Is.True);
        Assert.That(result.Point, Is.EqualTo(cached), "pin uses the capture-time point");
        Assert.That(result.ChipText, Is.Null);
    }

    [Test]
    public void ForcedMissProducesChipAndNoPin()
    {
        // Delayed null (depth briefly disabled / empty sky): no_surface, no pin,
        // even with a cached hit and even on a different ray.
        var result = PlacementResolver.TryPlaceFromCapture(
            HitEntry(new Vector3(0f, 1f, 2f), Vector3.up), "placed", null, false);
        Assert.That(result.Outcome, Is.EqualTo(PlacementOutcome.NoSurface));
        Assert.That(result.ChipText, Is.EqualTo("I can't plant that on a surface."));
        Assert.That(result.ShouldPin, Is.False);
    }

    [Test]
    public void MissingCacheEntryNeverPins()
    {
        var result = PlacementResolver.TryPlaceFromCapture(
            null, "placed", new Vector3(0f, 1f, 2f), false);
        Assert.That(result.ShouldPin, Is.False);
        Assert.That(result.ChipText, Is.EqualTo("I can't plant that on a surface."));
    }

    [Test]
    public void ChipSits12cmInFrontOfEye()
    {
        Vector3 eye = new Vector3(0f, 1.6f, 0f);
        Vector3 pos = HonestyChip.ComputeChipPosition(eye, Vector3.forward);
        Assert.That(Vector3.Distance(pos, eye + Vector3.forward * 0.12f), Is.LessThan(1e-5f));
        Assert.That(HonestyChip.ForwardDistanceM, Is.EqualTo(0.12f));
    }

    [Test]
    public void ChipHidesAfter3Seconds()
    {
        Assert.That(HonestyChip.VisibleSeconds, Is.EqualTo(3f));
        Assert.That(HonestyChip.ShouldHide(3.01f, 3f), Is.True);
        Assert.That(HonestyChip.ShouldHide(2.9f, 3f), Is.False);
        Assert.That(HonestyChip.ShouldHide(10f, -1f), Is.False);
    }

    [Test]
    public void StoreCapsAtEightDrawings()
    {
        Assert.That(DrawingStore.MaxDrawings, Is.EqualTo(8));
        Assert.That(DrawingStore.NeedsEviction(7), Is.False);
        Assert.That(DrawingStore.NeedsEviction(8), Is.True);
    }

    [Test]
    public void StorePlaceMarkIsWorldLockedAndCapped()
    {
        var storeGO = new GameObject("TestDrawingStore");
        try
        {
            var store = storeGO.AddComponent<DrawingStore>();
            Vector3 point = new Vector3(0f, 1f, 2f);
            GameObject mark = store.PlaceMark(point, Vector3.up, "test-draw-1");
            Assert.That(mark.transform.parent, Is.Null, "mark must never attach to a camera");
            Assert.That(Vector3.Distance(mark.transform.position, point), Is.LessThan(1e-5f));
            Assert.That(store.Count, Is.EqualTo(1));
            for (int i = 2; i <= 9; i++)
                store.PlaceMark(point, Vector3.up, "test-draw-" + i);
            Assert.That(store.Count, Is.EqualTo(8), "store caps at eight drawings");
            store.Clear();
            Assert.That(store.Count, Is.EqualTo(0));
        }
        finally
        {
            Object.DestroyImmediate(storeGO);
        }
    }
}
