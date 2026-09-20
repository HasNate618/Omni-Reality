using NUnit.Framework;
using UnityEngine;

public class FloorPlaneGrabTests
{
    [Test]
    public void ConstrainKeepsTheLockedHeightAndFollowsXZ()
    {
        Vector3 desired = new Vector3(1.2f, 3.5f, -0.4f);
        Vector3 constrained = FloorPlaneGrab.ConstrainPosition(desired, 0f);
        Assert.AreEqual(1.2f, constrained.x, 1e-5f);
        Assert.AreEqual(0f, constrained.y, 1e-5f);
        Assert.AreEqual(-0.4f, constrained.z, 1e-5f);
    }

    [Test]
    public void ConstrainHonoursANonZeroSurfaceHeight()
    {
        Vector3 constrained = FloorPlaneGrab.ConstrainPosition(new Vector3(0f, -9f, 0f), 0.75f);
        Assert.AreEqual(0.75f, constrained.y, 1e-5f);
    }

    [Test]
    public void ScaleIsLockedToTheValueAtGrabStart()
    {
        Vector3 atGrab = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 current = new Vector3(0.90f, 1.20f, 0.10f);
        Vector3 result = FloorPlaneGrab.ConstrainedScale(current, atGrab);
        Assert.AreEqual(atGrab.x, result.x, 1e-5f);
        Assert.AreEqual(atGrab.y, result.y, 1e-5f);
        Assert.AreEqual(atGrab.z, result.z, 1e-5f);
    }

    [Test]
    public void GrabOffsetRidesTheHandAndTheHeightStaysPinned()
    {
        // Grabbed at a corner, so the object's origin sits off the hand.
        Vector3 offset = new Vector3(0.20f, 0.90f, -0.30f);
        Vector3 handAtGrab = new Vector3(-1.00f, 1.20f, 0.40f);
        Vector3 handLater = handAtGrab + new Vector3(0.50f, 0.90f, 0.25f);
        Vector3 atGrab = FloorPlaneGrab.ConstrainPosition(handAtGrab + offset, 0.36f);
        Vector3 later = FloorPlaneGrab.ConstrainPosition(handLater + offset, 0.36f);
        // The object travels exactly as far as the hand in X and Z...
        Assert.AreEqual(handLater.x - handAtGrab.x, later.x - atGrab.x, 1e-5f);
        Assert.AreEqual(handLater.z - handAtGrab.z, later.z - atGrab.z, 1e-5f);
        // ...and never in Y, however far the hand rises.
        Assert.AreEqual(0.36f, atGrab.y, 1e-5f);
        Assert.AreEqual(0.36f, later.y, 1e-5f);
    }

    [Test]
    public void GrabDisablesEngineDrivenPositionAndScale()
    {
        var go = new GameObject("grabbable", typeof(RectTransform));
        var grab = go.AddComponent<UnityEngine.XR.Interaction.Toolkit.Interactables.XRGrabInteractable>();
        FloorPlaneGrab.Configure(grab);
        Assert.IsFalse(grab.trackPosition, "we move it ourselves on the floor");
        Assert.IsFalse(grab.trackRotation, "yaw is voice-only in this slice");
        Assert.IsFalse(grab.trackScale, "listed size must survive a grab");
        Object.DestroyImmediate(go);
    }

    [Test]
    public void AttachAddsAGrabInteractableAndTheConstraint()
    {
        var go = new GameObject("drawing");
        FloorPlaneGrab attached = FloorPlaneGrab.Attach(go, 0.25f);
        Assert.IsNotNull(attached);
        Assert.IsNotNull(go.GetComponent<UnityEngine.XR.Interaction.Toolkit.Interactables.XRGrabInteractable>());
        Assert.AreEqual(0.25f, attached.LockedY, 1e-5f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void AttachLeavesTheBodyKinematicSoGravityCannotDropIt()
    {
        // XRGrabInteractable's [RequireComponent(typeof(Rigidbody))] adds a
        // simulating body; a dropped listing box would contradict the plant.
        var go = new GameObject("drawing");
        FloorPlaneGrab.Attach(go, 0f);
        Rigidbody body = go.GetComponent<Rigidbody>();
        Assert.IsNotNull(body, "the required Rigidbody must be there");
        Assert.IsTrue(body.isKinematic, "the grab drives the transform, not physics");
        Assert.IsFalse(body.useGravity, "gravity would drop the listed box out of the room");
        Object.DestroyImmediate(go);
    }
}
