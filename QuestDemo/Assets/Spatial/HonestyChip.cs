using UnityEngine;

/// <summary>
/// World-locked honesty chip (Task 5). Shows the exact resolver copy ~0.12 m
/// in front of CenterEyeAnchor stamped AT SHOW TIME, then stays world-locked
/// (never follows the head, never a screen-centre HUD) and auto-hides after
/// 3 s. Must live on its own GameObject: Show/Hide toggle that object, so
/// sharing ARDirector would hide the whole director.
/// </summary>
public class HonestyChip : MonoBehaviour
{
    public const float ForwardDistanceM = 0.12f;
    public const float VisibleSeconds = 3f;

    TextMesh _label;
    float _hideAt = -1f;

    void Awake()
    {
        EnsureVisual();
        HideImmediate();
    }

    void Update()
    {
        if (ShouldHide(Time.time, _hideAt))
            HideImmediate();
    }

    /// <summary>Show copy at a world-locked pose in front of the eye.</summary>
    public void Show(string text, Transform centerEye)
    {
        EnsureVisual();
        // World-locked: pose is stamped once, no follow, no camera parent.
        transform.SetParent(null, true);
        if (centerEye != null)
        {
            transform.position = ComputeChipPosition(centerEye.position, centerEye.forward);
            Vector3 toChip = transform.position - centerEye.position;
            if (toChip.sqrMagnitude > 1e-8f)
                transform.rotation = Quaternion.LookRotation(toChip, Vector3.up);
        }
        if (_label != null)
            _label.text = text ?? string.Empty;
        gameObject.SetActive(true);
        _hideAt = Time.time + VisibleSeconds;
    }

    public void HideImmediate()
    {
        _hideAt = -1f;
        if (gameObject.activeSelf)
            gameObject.SetActive(false);
    }

    public bool IsShowing
    {
        get { return gameObject.activeSelf; }
    }

    void EnsureVisual()
    {
        if (_label != null)
            return;
        var labelGO = new GameObject("HonestyLabel");
        labelGO.transform.SetParent(transform, false);
        labelGO.transform.localPosition = Vector3.zero;
        labelGO.transform.localRotation = Quaternion.identity;
        _label = labelGO.AddComponent<TextMesh>();
        _label.anchor = TextAnchor.MiddleCenter;
        _label.alignment = TextAlignment.Center;
        _label.fontSize = 48;
        _label.characterSize = 0.0025f;
        _label.color = Color.white;
    }

    /// <summary>Pure pose seam: chip position for an eye pose. Test-covered.</summary>
    public static Vector3 ComputeChipPosition(Vector3 eyePosition, Vector3 eyeForward)
    {
        Vector3 fwd = eyeForward;
        if (fwd.sqrMagnitude < 1e-8f)
            fwd = Vector3.forward;
        return eyePosition + fwd.normalized * ForwardDistanceM;
    }

    /// <summary>Pure hide-timing seam: visible for VisibleSeconds. Test-covered.</summary>
    public static bool ShouldHide(float nowS, float hideAtS)
    {
        return hideAtS > 0f && nowS >= hideAtS;
    }
}
