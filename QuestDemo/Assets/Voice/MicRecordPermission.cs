using UnityEngine;

/// <summary>
/// Microphone authorization: Android device builds use
/// <see cref="UnityEngine.Android.Permission"/>; editor and other runtimes
/// use Unity's <see cref="Application"/> authorization API.
/// </summary>
public static class MicRecordPermission
{
    public enum PermissionBackend
    {
        UnityApplication,
        AndroidRuntime,
    }

    /// <summary>Which permission API the mic path uses on this runtime.</summary>
    public static PermissionBackend BackendForRuntime()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        return PermissionBackend.AndroidRuntime;
#else
        return PermissionBackend.UnityApplication;
#endif
    }

    public static bool IsGranted()
    {
        return IsGranted(BackendForRuntime());
    }

    public static bool IsGranted(PermissionBackend backend)
    {
        switch (backend)
        {
            case PermissionBackend.AndroidRuntime:
                return UnityEngine.Android.Permission.HasUserAuthorizedPermission(
                    UnityEngine.Android.Permission.Microphone);
            default:
                return Application.HasUserAuthorization(UserAuthorization.Microphone);
        }
    }

    public static void RequestOnce(ref bool requested)
    {
        RequestOnce(ref requested, BackendForRuntime());
    }

    public static void RequestOnce(ref bool requested, PermissionBackend backend)
    {
        if (requested)
            return;
        requested = true;
        switch (backend)
        {
            case PermissionBackend.AndroidRuntime:
                UnityEngine.Android.Permission.RequestUserPermission(
                    UnityEngine.Android.Permission.Microphone);
                break;
            default:
                Application.RequestUserAuthorization(UserAuthorization.Microphone);
                break;
        }
    }

    /// <summary>Test seam for mic_permission log deduplication.</summary>
    public static bool ShouldLogGrantTransition(
        bool requestIssued,
        bool? lastLoggedGrant,
        bool currentGrant)
    {
        if (!requestIssued)
            return false;
        if (lastLoggedGrant.HasValue && lastLoggedGrant.Value == currentGrant)
            return false;
        return true;
    }
}
