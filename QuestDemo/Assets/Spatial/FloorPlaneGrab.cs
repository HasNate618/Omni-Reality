using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using UnityEngine.XR.Interaction.Toolkit.Interactables;

/// <summary>
/// Floor-plane drag for generated furniture (spec §7.2). X and Z follow the
/// hand; Y stays locked to the surface the object was planted on, and scale
/// stays locked to the listed size. Rotation is voice-only in this slice, so
/// the engine's own tracking is switched off and this component moves the
/// object instead. The constraint math is static and pure for EditMode tests.
/// </summary>
public class FloorPlaneGrab : MonoBehaviour
{
    public float LockedY;

    Vector3 _scaleAtGrabStart;
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
        XRGrabInteractable grab = GetComponent<XRGrabInteractable>();
        if (grab == null)
            return;
        grab.selectEntered.RemoveListener(OnGrabbed);
        grab.selectExited.RemoveListener(OnReleased);
    }

    void OnGrabbed(SelectEnterEventArgs args)
    {
        _held = true;
        _scaleAtGrabStart = transform.localScale;
    }

    void OnReleased(SelectExitEventArgs args)
    {
        _held = false;
        // The pose it was left in is the pose it keeps: world-locked, no snap.
        transform.position = ConstrainPosition(transform.position, LockedY);
        transform.localScale = ConstrainedScale(transform.localScale, _scaleAtGrabStart);
    }

    void Update()
    {
        if (!_held)
            return;
        transform.position = ConstrainPosition(transform.position, LockedY);
        transform.localScale = ConstrainedScale(transform.localScale, _scaleAtGrabStart);
    }
}
