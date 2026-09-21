using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.XR;

/// <summary>
/// The demo's take-home card (demo.md beat 5: "take-home card overlay; layout
/// holds as camera pulls back"): one summary of what the run placed, a line
/// per item, raised on demand as the wearer pulls back from the finished
/// corner.
///
/// It reads the SAME entries the item-queue overlay accumulated, so the card
/// cannot disagree with the chips about what was placed, and it can only ever
/// list placements the coordinator accepted.
///
/// Trigger: the B/Y face button, which nothing else binds -- A is the
/// listening toggle, the grips grab, the index triggers capture. Toggle() is
/// also public, so a spoken path can raise the card without the button.
/// </summary>
public class TakeHomeCard : MonoBehaviour
{
    public const float CardDistanceM = 1.1f;
    public const float CardVerticalOffsetM = 0f;

    TextMesh _label;
    Transform _eye;
    ItemQueueOverlay _queue;
    bool _showing;
    bool _toggleWasHeld;

    public bool IsShowing { get { return _showing; } }

    public void Bind(Transform eye, ItemQueueOverlay queue)
    {
        _eye = eye;
        _queue = queue;
    }

    public void Toggle()
    {
        if (_showing) Hide();
        else Show();
    }

    /// <summary>Raise the card over the run's placements.</summary>
    public void Show()
    {
        EnsureVisual();
        if (_label == null) return;
        IReadOnlyList<ItemQueueOverlay.Entry> entries =
            _queue == null ? null : _queue.Entries;
        _label.text = FormatCard(entries);
        _label.gameObject.SetActive(true);
        _showing = true;
        LateUpdate();
    }

    public void Hide()
    {
        _showing = false;
        if (_label != null) _label.gameObject.SetActive(false);
    }

    void Update()
    {
        // Edge-detected locally so the OpenXR fallback below, which is a level
        // query, cannot toggle the card once per frame while the button is down.
        bool held = ToggleHeld();
        if (held && !_toggleWasHeld)
            Toggle();
        _toggleWasHeld = held;
    }

    void LateUpdate()
    {
        if (!_showing || _eye == null || _label == null) return;
        _label.transform.position = _eye.TransformPoint(
            new Vector3(0f, CardVerticalOffsetM, CardDistanceM));
        _label.transform.rotation = _eye.rotation;
    }

    /// <summary>
    /// B/Y face button held on either controller. OVRInput first, OpenXR input
    /// devices as fallback (dead under some loaders), matching the mic's
    /// PttHeld so both bindings survive the same loader differences.
    /// </summary>
    internal static bool ToggleHeld()
    {
        try
        {
            if (OVRInput.Get(OVRInput.Button.Two))
                return true;
        }
        catch (System.Exception)
        {
        }
        try
        {
            var right = InputDevices.GetDeviceAtXRNode(XRNode.RightHand);
            if (right.TryGetFeatureValue(CommonUsages.secondaryButton, out bool r) && r)
                return true;
            var left = InputDevices.GetDeviceAtXRNode(XRNode.LeftHand);
            return left.TryGetFeatureValue(CommonUsages.secondaryButton, out bool l) && l;
        }
        catch (System.Exception)
        {
            return false;
        }
    }

    void EnsureVisual()
    {
        if (_label != null)
            return;
        var go = new GameObject("TakeHomeCardText");
        go.transform.SetParent(transform, false);
        _label = go.AddComponent<TextMesh>();
        _label.fontSize = 48;
        _label.characterSize = 0.003f;
        _label.anchor = TextAnchor.MiddleCenter;
        _label.alignment = TextAlignment.Left;
        _label.color = Color.white;
        // Listing names are data, not markup: never interpret them.
        _label.richText = false;
        go.SetActive(false);
    }

    /// <summary>Pure: one item's line -- name and dims, dims alone when unnamed.</summary>
    public static string FormatLine(ItemQueueOverlay.Entry entry)
    {
        string dims = ItemQueueOverlay.FormatDims(entry.ExtentM);
        string name = (entry.Name ?? "").Trim();
        return name.Length == 0 ? dims : name + "  " + dims;
    }

    /// <summary>
    /// Pure: the whole card. An empty log is stated rather than left blank, so
    /// the wearer never faces an empty panel wondering whether it worked.
    /// </summary>
    public static string FormatCard(IReadOnlyList<ItemQueueOverlay.Entry> entries)
    {
        int count = entries == null ? 0 : entries.Count;
        if (count == 0)
            return "Nothing placed yet.";
        var text = new StringBuilder();
        text.Append(count == 1 ? "1 item placed" : count + " items placed");
        for (int i = 0; i < count; i++)
        {
            text.Append('\n');
            text.Append(FormatLine(entries[i]));
        }
        return text.ToString();
    }
}
