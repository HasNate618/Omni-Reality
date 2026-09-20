using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using UnityEngine.XR.Interaction.Toolkit.Interactables;
using UnityEngine.XR.Interaction.Toolkit.Interactors;

/// <summary>
/// Floor-plane drag for generated furniture (spec §7.2). X and Z follow the
/// hand; Y stays locked to the surface the object was planted on, and scale
/// stays locked to the listed size. Rotation is voice-only in this slice, so
/// the engine's own tracking is switched off and this component moves the
/// object instead. The constraint math is static and pure for EditMode tests.
/// </summary>
/// <remarks>
/// Because the engine's tracking is off, XRI's smoothing and velocity
/// tracking do not apply on this path: the drag is more direct than a normal
/// XRI grab. That is the intended trade for an object whose height must never
/// move.
/// </remarks>
public class FloorPlaneGrab : MonoBehaviour
{
    public float LockedY;

    Vector3 _scaleAtGrabStart;
    Vector3 _grabOffset;
    IXRSelectInteractor _interactor;
    IXRSelectInteractable _interactable;
    bool _held;

    /// <summary>Desired position clamped to the locked height.</summary>
    public static Vector3 ConstrainPosition(Vector3 desired, float lockedY)
    {
        return new Vector3(desired.x, lockedY, desired.z);
    }

    /// <summary>Scale never changes during a grab.</summary>
    public static Vector3 ConstrainedScale(Vector3 current, Vector3 atGrabStart)
    {
        return atGrabStart;
    }

    /// <summary>Switch off engine-driven motion; this component owns it.</summary>
    public static void Configure(XRGrabInteractable grab)
    {
        if (grab == null)
            return;
        grab.trackPosition = false;
        grab.trackRotation = false;
        grab.trackScale = false;
        // The body is kinematic, so Detach would log a warning on every
        // release before early-returning.
        grab.throwOnDetach = false;
    }

    /// <summary>Attach a grabbable to a generated root. Returns the constraint.</summary>
    public static FloorPlaneGrab Attach(GameObject root, float lockedY)
    {
        if (root == null)
            return null;
        XRGrabInteractable grab = root.GetComponent<XRGrabInteractable>();
        if (grab == null)
            grab = root.AddComponent<XRGrabInteractable>();
        Configure(grab);
        // XRGrabInteractable requires a Rigidbody, so adding it adds one. The
        // grab drives the transform on a locked plane: physics must not fight
        // it, and gravity would drop the listed box out of the room.
        Rigidbody body = root.GetComponent<Rigidbody>();
        if (body != null)
        {
            body.isKinematic = true;
            body.useGravity = false;
        }
        FloorPlaneGrab constraint = root.GetComponent<FloorPlaneGrab>();
        if (constraint == null)
            constraint = root.AddComponent<FloorPlaneGrab>();
        constraint.LockedY = lockedY;
        return constraint;
    }

    void OnEnable()
    {
        XRGrabInteractable grab = GetComponent<XRGrabInteractable>();
        if (grab == null)
            return;
        grab.selectEntered.AddListener(OnGrabbed);
        grab.selectExited.AddListener(OnReleased);
    }

    void OnDisable()
    {
        // The listeners are gone, so a still-held root would keep being driven
        // from a hand with no active selection. Drop the grab state with them.
        _held = false;
        _interactor = null;
        _interactable = null;
        XRGrabInteractable grab = GetComponent<XRGrabInteractable>();
        if (grab == null)
            return;
        grab.selectEntered.RemoveListener(OnGrabbed);
        grab.selectExited.RemoveListener(OnReleased);
    }

    void OnGrabbed(SelectEnterEventArgs args)
    {
        _held = true;
        _interactor = args.interactorObject;
        _interactable = args.interactableObject;
        _scaleAtGrabStart = transform.localScale;
        Vector3 hand;
        // Where it was grabbed stays under the hand: grabbing a corner must
        // not snap the object's origin to the hand.
        _grabOffset = TryGetHandPosition(out hand) ? transform.position - hand : Vector3.zero;
    }

    void OnReleased(SelectExitEventArgs args)
    {
        _held = false;
        _interactor = null;
        _interactable = null;
        // The pose it was left in is the pose it keeps: world-locked, no snap.
        transform.position = ConstrainPosition(transform.position, LockedY);
        transform.localScale = ConstrainedScale(transform.localScale, _scaleAtGrabStart);
    }

    /// <summary>Where the hand is, or false when it cannot be asked.</summary>
    bool TryGetHandPosition(out Vector3 handPosition)
    {
        handPosition = Vector3.zero;
        // An interface reference carries no Unity destroyed-object check, so
        // look at the underlying objects before calling into them.
        Object interactor = _interactor as Object;
        Object interactable = _interactable as Object;
        if (interactor == null || interactable == null)
            return false;
        Transform attach = _interactor.GetAttachTransform(_interactable);
        if (attach == null)
            return false;
        handPosition = attach.position;
        return true;
    }

    void Update()
    {
        if (!_held)
            return;
        Vector3 hand;
        // A detaching or destroyed interactor must not teleport the object to
        // the origin: with no hand to follow, it simply stays put.
        if (!TryGetHandPosition(out hand))
            return;
        transform.position = ConstrainPosition(hand + _grabOffset, LockedY);
        transform.localScale = ConstrainedScale(transform.localScale, _scaleAtGrabStart);
    }
}
