using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

/// <summary>
/// The demo's item-queue overlay (demo.md beat 1: "item queue overlay (3 chips
/// with dims)"): up to three chips, each naming one placed item and the listed
/// W x D x H it was placed at.
///
/// Fed from ACCEPTED <c>place_generated</c> ops -- the same ops that plant the
/// box -- so a chip describes a placement that actually happened rather than a
/// re-reading of the script, and the dims are the listing's own numbers, which
/// is the entire scale claim. The overlay never invents a size and never
/// renders a pose the model produced: the model authors no geometry.
///
/// Head-relative like <see cref="VoiceCaption"/> rather than world-locked like
/// <see cref="HonestyChip"/>: the queue is a status panel that has to stay
/// readable while the wearer looks around the corner they are filling.
///
/// All text and layout math is static and pure so it is EditMode testable
/// without a headset.
/// </summary>
public class ItemQueueOverlay : MonoBehaviour
{
    /// <summary>demo.md beat 1: three chips, one per cart listing.</summary>
    public const int MaxChips = 3;

    /// <summary>
    /// Stored entries are capped like the drawing store's clutter cap
    /// (<see cref="DrawingStore.MaxDrawings"/>): past that the room cannot
    /// hold what the card would list, so the oldest is dropped rather than
    /// letting the log grow for the life of the session.
    /// </summary>
    public const int MaxEntries = 8;

    /// <summary>Longest listing name a chip shows before it is elided.</summary>
    public const int MaxNameChars = 32;

    /// <summary>Eye-local placement of chip 0; a legibility choice, not a spec.</summary>
    public const float ChipDistanceM = 0.9f;
    public const float ChipLateralOffsetM = -0.30f;
    public const float ChipTopOffsetM = 0.10f;
    public const float ChipSpacingM = 0.08f;

    /// <summary>One placed item: the name it was recorded under, and its extents.</summary>
    public struct Entry
    {
        public string Id;
        public string Name;
        public Vector3 ExtentM;
    }

    // Kept as one live list: TakeHomeCard holds a reference to Entries and
    // must see later placements. Never reassign, only mutate in place.
    readonly List<Entry> _entries = new List<Entry>();
    readonly List<TextMesh> _chips = new List<TextMesh>();
    Transform _eye;

    public void Bind(Transform eye) { _eye = eye; }

    public int Count { get { return _entries.Count; } }

    public IReadOnlyList<Entry> Entries { get { return _entries; } }

    /// <summary>Record one accepted placement and redraw the chips.</summary>
    public void Record(string id, string name, Vector3 extentM)
    {
        var entry = new Entry { Id = id ?? "", Name = name ?? "", ExtentM = extentM };
        List<Entry> next = Append(_entries, entry);
        _entries.Clear();
        _entries.AddRange(next);
        Refresh();
    }

    /// <summary>Drop the queue; a new session keeps no stale chips.</summary>
    public void Clear()
    {
        _entries.Clear();
        Refresh();
    }

    void LateUpdate()
    {
        if (_eye == null) return;
        // Active chips are always indices 0..n-1 (Refresh activates in order),
        // so a chip's index is also its slot.
        for (int i = 0; i < _chips.Count; i++)
        {
            TextMesh chip = _chips[i];
            if (chip == null || !chip.gameObject.activeSelf) continue;
            chip.transform.position = _eye.TransformPoint(ChipLocalPosition(i));
            chip.transform.rotation = _eye.rotation;
        }
    }

    void Refresh()
    {
        List<Entry> visible = Visible(_entries, MaxChips);
        for (int i = 0; i < visible.Count; i++)
        {
            TextMesh chip = EnsureChip(i);
            if (chip == null) continue;
            chip.text = FormatChip(visible[i]);
            chip.gameObject.SetActive(true);
        }
        for (int i = visible.Count; i < _chips.Count; i++)
        {
            if (_chips[i] != null) _chips[i].gameObject.SetActive(false);
        }
    }

    TextMesh EnsureChip(int index)
    {
        while (_chips.Count <= index)
        {
            var go = new GameObject("ItemQueueChip" + _chips.Count);
            go.transform.SetParent(transform, false);
            TextMesh mesh = go.AddComponent<TextMesh>();
            mesh.fontSize = 48;
            mesh.characterSize = 0.0025f;
            mesh.anchor = TextAnchor.MiddleLeft;
            mesh.alignment = TextAlignment.Left;
            mesh.color = Color.white;
            // Listing names are data, not markup: never interpret them.
            mesh.richText = false;
            go.SetActive(false);
            _chips.Add(mesh);
        }
        return _chips[index];
    }

    /// <summary>Pure: append, or replace the entry with the same id in place.</summary>
    public static List<Entry> Append(List<Entry> entries, Entry entry, int max = MaxEntries)
    {
        var next = new List<Entry>(entries ?? new List<Entry>());
        string id = entry.Id ?? "";
        if (id.Length > 0)
        {
            int at = next.FindIndex(e => (e.Id ?? "") == id);
            if (at >= 0)
            {
                next[at] = entry;
                return next;
            }
        }
        next.Add(entry);
        while (max > 0 && next.Count > max)
            next.RemoveAt(0);
        return next;
    }

    /// <summary>Pure: the newest <paramref name="max"/> entries, oldest first.</summary>
    public static List<Entry> Visible(List<Entry> entries, int max = MaxChips)
    {
        var all = entries ?? new List<Entry>();
        if (max <= 0 || all.Count == 0)
            return new List<Entry>();
        int skip = all.Count > max ? all.Count - max : 0;
        return all.GetRange(skip, all.Count - skip);
    }

    /// <summary>Pure: eye-local position of the chip in slot <paramref name="index"/>.</summary>
    public static Vector3 ChipLocalPosition(int index)
    {
        return new Vector3(
            ChipLateralOffsetM,
            ChipTopOffsetM - index * ChipSpacingM,
            ChipDistanceM);
    }

    /// <summary>
    /// Pure: one chip's text -- the name with the listed dims under it, or the
    /// dims alone when the op carried no name. The wire field is optional by
    /// contract, so an unnamed placement degrades to a dims-only label rather
    /// than rendering a blank chip.
    /// </summary>
    public static string FormatChip(Entry entry)
    {
        string dims = FormatDims(entry.ExtentM);
        string name = Elide((entry.Name ?? "").Trim());
        return name.Length == 0 ? dims : name + "\n" + dims;
    }

    /// <summary>
    /// Pure: the listed W x D x H in metres, in WIRE order (w, d, h) -- the
    /// order the listing stated and the order extent_m carries. Invariant
    /// culture, so a comma-decimal locale cannot render "0,55".
    /// </summary>
    public static string FormatDims(Vector3 extentM)
    {
        return Metres(extentM.x) + " x " + Metres(extentM.y) + " x " + Metres(extentM.z) + " m";
    }

    static string Metres(float value)
    {
        return value.ToString("0.00", CultureInfo.InvariantCulture);
    }

    static string Elide(string name)
    {
        if (name.Length <= MaxNameChars) return name;
        return name.Substring(0, MaxNameChars - 3) + "...";
    }
}
