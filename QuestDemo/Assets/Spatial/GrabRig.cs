using System;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using UnityEngine.XR.Interaction.Toolkit.Inputs.Readers;
using UnityEngine.XR.Interaction.Toolkit.Interactors;

/// <summary>
/// Completes the interaction plumbing generated furniture needs. Task 12 put
/// an <c>XRGrabInteractable</c> and a <c>FloorPlaneGrab</c> on every generated
/// root, but the project had no <see cref="XRInteractionManager"/> and no
/// interactor anywhere, so nothing could ever select a box and the floor-plane
/// grab could not run at all on device. <see cref="Ensure"/> wires that up in
/// code, the way the rest of this rig is built at runtime; the scene file is
/// left alone because its YAML is GUID-based and hand-editing it is how scenes
/// get corrupted.
/// </summary>
/// <remarks>
/// One <see cref="XRDirectInteractor"/> per controller, on the camera rig's own
/// hand anchors, with a trigger <see cref="SphereCollider"/> for contact. A
/// direct interactor is the one this grab wants: its attach transform is the
/// hand, which is what <c>FloorPlaneGrab</c> moves the object by, while a ray
/// interactor's attach transform rides the ray. Select is the grip, read live
/// from <see cref="OVRInput"/>: the index triggers already belong to capture in
/// <c>SpatialRuntime</c>, and reading at the moment XRI polls leaves no
/// dependence on Update order between this rig and the interaction manager.
/// </remarks>
public static class GrabRig
{
    /// <summary>Radius of the hand sphere that must touch a grabbable.</summary>
    public const float HandSphereRadiusM = 0.1f;

    /// <summary>Grip, so the index triggers stay the capture bindings.</summary>
    const OVRInput.Button Grip = OVRInput.Button.PrimaryHandTrigger;

    const string LeftHandAnchorName = "LeftHandAnchor";
    const string RightHandAnchorName = "RightHandAnchor";

    /// <summary>
    /// Makes the interaction manager and both controller interactors exist.
    /// Idempotent: an existing manager is reused and each hand keeps exactly
    /// one interactor, so calling this again adds nothing.
    /// </summary>
    public static XRInteractionManager Ensure()
    {
        return Ensure(FindAnchor(LeftHandAnchorName), FindAnchor(RightHandAnchorName));
    }

    /// <summary>Test/injection seam for <see cref="Ensure"/>.</summary>
    public static XRInteractionManager Ensure(Transform leftHand, Transform rightHand)
    {
        XRInteractionManager manager = EnsureInteractionManager();
        EnsureHand(leftHand, manager, OVRInput.Controller.LTouch);
        EnsureHand(rightHand, manager, OVRInput.Controller.RTouch);
        return manager;
    }

    static XRInteractionManager EnsureInteractionManager()
    {
        XRInteractionManager manager = UnityEngine.Object.FindAnyObjectByType<XRInteractionManager>();
        if (manager == null)
            manager = new GameObject("XRInteractionManager").AddComponent<XRInteractionManager>();
        return manager;
    }

    static void EnsureHand(Transform hand, XRInteractionManager manager, OVRInput.Controller controller)
    {
        if (hand == null)
        {
            Debug.LogWarning("GrabRig: no " + controller + " anchor in the scene, that hand cannot grab");
            return;
        }
        // Collider first: XRDirectInteractor inspects its colliders in Awake.
        EnsureTriggerSphere(hand.gameObject);
        XRDirectInteractor interactor = hand.GetComponent<XRDirectInteractor>();
        if (interactor == null)
            interactor = hand.gameObject.AddComponent<XRDirectInteractor>();
        interactor.interactionManager = manager;
        // One input source, replaced rather than stacked, so a second call
        // changes nothing.
        interactor.selectInput.bypass = new OvrGripButtonReader(controller);
    }

    static void EnsureTriggerSphere(GameObject hand)
    {
        SphereCollider sphere = hand.GetComponent<SphereCollider>();
        if (sphere == null)
            sphere = hand.AddComponent<SphereCollider>();
        // A trigger volume is what XRDirectInteractor reads for contact. The
        // hand carries no Rigidbody: the grabbable's own kinematic body is
        // what lets Unity raise trigger events against this collider.
        sphere.isTrigger = true;
        sphere.radius = HandSphereRadiusM;
    }

    static Transform FindAnchor(string name)
    {
        GameObject anchor = GameObject.Find(name);
        return anchor != null ? anchor.transform : null;
    }

    /// <summary>
    /// Select as a grip, read from <see cref="OVRInput"/> at the moment the
    /// interactor asks. OVRInput is the same input source <c>SpatialRuntime</c>
    /// uses for capture, so grabbing needs no input actions asset and cannot
    /// disagree with the rest of the app about where a button is.
    /// </summary>
    sealed class OvrGripButtonReader : IXRInputButtonReader
    {
        readonly OVRInput.Controller _controller;

        internal OvrGripButtonReader(OVRInput.Controller controller)
        {
            _controller = controller;
        }

        public bool ReadIsPerformed()
        {
            try { return OVRInput.Get(Grip, _controller); }
            catch (Exception) { return false; }
        }

        public bool ReadWasPerformedThisFrame()
        {
            try { return OVRInput.GetDown(Grip, _controller); }
            catch (Exception) { return false; }
        }

        public bool ReadWasCompletedThisFrame()
        {
            try { return OVRInput.GetUp(Grip, _controller); }
            catch (Exception) { return false; }
        }

        public float ReadValue()
        {
            try { return OVRInput.Get(OVRInput.Axis1D.PrimaryHandTrigger, _controller); }
            catch (Exception) { return 0f; }
        }

        public bool TryReadValue(out float value)
        {
            value = ReadValue();
            return true;
        }
    }
}
