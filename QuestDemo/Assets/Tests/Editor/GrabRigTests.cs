using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;
using UnityEngine.XR.Interaction.Toolkit.Interactors;

/// <summary>
/// Setting the grab rig up must be safe to repeat: SpatialRuntime calls
/// <c>Ensure</c> during Awake, and a second interaction manager would leave the
/// runtime's interactors registered to a manager the interactables are not on,
/// so no selection could ever resolve.
/// </summary>
public class GrabRigTests
{
    GameObject _left;
    GameObject _right;
    readonly List<XRInteractionManager> _preExisting = new List<XRInteractionManager>();

    [SetUp]
    public void SetUp()
    {
        _left = new GameObject("GrabRigTestsLeftHand");
        _right = new GameObject("GrabRigTestsRightHand");
        _preExisting.Clear();
        _preExisting.AddRange(Managers());
    }

    [TearDown]
    public void TearDown()
    {
        Object.DestroyImmediate(_left);
        Object.DestroyImmediate(_right);
        // Leave a manager that was already in the scene alone; remove only what
        // this test brought in.
        foreach (XRInteractionManager manager in Managers())
        {
            if (!_preExisting.Contains(manager))
                Object.DestroyImmediate(manager.gameObject);
        }
    }

    [Test]
    public void EnsureCalledTwiceLeavesExactlyOneInteractionManager()
    {
        GrabRig.Ensure(_left.transform, _right.transform);
        GrabRig.Ensure(_left.transform, _right.transform);

        Assert.AreEqual(1, Managers().Length,
            "a second manager would split the interactors from the interactables");
    }

    [Test]
    public void EnsureCalledTwiceLeavesOneInteractorPerHand()
    {
        GrabRig.Ensure(_left.transform, _right.transform);
        GrabRig.Ensure(_left.transform, _right.transform);

        Assert.AreEqual(1, _left.GetComponents<XRDirectInteractor>().Length);
        Assert.AreEqual(1, _right.GetComponents<XRDirectInteractor>().Length);
    }

    [Test]
    public void EnsureGivesTheHandTheTriggerVolumeTheInteractorReads()
    {
        GrabRig.Ensure(_left.transform, _right.transform);

        SphereCollider sphere = _right.GetComponent<SphereCollider>();
        Assert.IsNotNull(sphere, "XRDirectInteractor needs a collision volume");
        Assert.IsTrue(sphere.isTrigger, "only a trigger volume is read for contact");
        Assert.AreEqual(GrabRig.HandSphereRadiusM, sphere.radius, 1e-5f);
    }

    static XRInteractionManager[] Managers()
    {
        Object[] found = Object.FindObjectsByType(
            typeof(XRInteractionManager), FindObjectsInactive.Exclude);
        var managers = new XRInteractionManager[found.Length];
        for (int i = 0; i < found.Length; i++)
            managers[i] = (XRInteractionManager)found[i];
        return managers;
    }
}
