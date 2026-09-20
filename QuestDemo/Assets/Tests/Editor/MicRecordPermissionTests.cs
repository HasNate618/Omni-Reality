using NUnit.Framework;
using UnityEngine;

public class MicRecordPermissionTests
{
    [Test]
    public void EditModeUsesUnityApplicationBackend()
    {
        Assert.AreEqual(
            MicRecordPermission.PermissionBackend.UnityApplication,
            MicRecordPermission.BackendForRuntime());
    }

    [Test]
    public void AndroidBackendUsesMicrophonePermissionConstant()
    {
        Assert.AreEqual(
            "android.permission.RECORD_AUDIO",
            UnityEngine.Android.Permission.Microphone);
    }

    [Test]
    public void RequestOnceIssuesSingleRequestForUnityBackend()
    {
        bool requested = false;
        MicRecordPermission.RequestOnce(
            ref requested,
            MicRecordPermission.PermissionBackend.UnityApplication);
        Assert.IsTrue(requested);
        bool stillRequested = requested;
        MicRecordPermission.RequestOnce(
            ref requested,
            MicRecordPermission.PermissionBackend.UnityApplication);
        Assert.AreEqual(stillRequested, requested);
    }

    [Test]
    public void PermissionLogEmitsOnlyAfterRequestAndOnGrantChange()
    {
        Assert.IsFalse(MicRecordPermission.ShouldLogGrantTransition(false, null, false));
        Assert.IsFalse(MicRecordPermission.ShouldLogGrantTransition(false, null, true));
        Assert.IsTrue(MicRecordPermission.ShouldLogGrantTransition(true, null, false));
        Assert.IsTrue(MicRecordPermission.ShouldLogGrantTransition(true, null, true));
        Assert.IsFalse(MicRecordPermission.ShouldLogGrantTransition(true, false, false));
        Assert.IsTrue(MicRecordPermission.ShouldLogGrantTransition(true, false, true));
        Assert.IsFalse(MicRecordPermission.ShouldLogGrantTransition(true, true, true));
    }
}
