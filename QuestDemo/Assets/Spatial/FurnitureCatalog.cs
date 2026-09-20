using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// The furniture the selector offers, and the size each one ships at.
///
/// Sizes are ordinary furniture dimensions. They are defaults for pieces the
/// wearer adds by hand; anything that arrives from the cart carries the cart's
/// own numbers instead. Either way the mesh is built to fill the
/// size exactly, so what you see is the footprint the fit check uses.
/// </summary>
public static class FurnitureCatalog
{
    public struct Entry
    {
        public string Kind;
        public string Label;
        /// <summary>Width (x), depth (z), height (y) in metres.</summary>
        public Vector3 SizeM;
        public Color Tint;
    }

    public const string Sofa = "sofa";
    public const string Armchair = "armchair";
    public const string CoffeeTable = "coffee_table";
    public const string SideTable = "side_table";
    public const string Bookshelf = "bookshelf";
    public const string FloorLamp = "floor_lamp";

    static readonly List<Entry> _entries = new List<Entry>
    {
        Make(Sofa,        "2-seat sofa",  1.80f, 0.90f, 0.83f, "#4FC3F7"),
        Make(Armchair,    "armchair",     0.78f, 0.82f, 1.00f, "#FFB74D"),
        Make(CoffeeTable, "coffee table", 1.10f, 0.60f, 0.42f, "#81C784"),
        Make(SideTable,   "side table",   0.55f, 0.55f, 0.45f, "#BA9BE8"),
        Make(Bookshelf,   "bookshelf",    0.80f, 0.30f, 1.06f, "#F06292"),
        Make(FloorLamp,   "floor lamp",   0.36f, 0.36f, 1.55f, "#FFD54F"),
    };

    static Entry Make(string kind, string label, float w, float d, float h, string hex)
    {
        Color tint;
        if (!ColorUtility.TryParseHtmlString(hex, out tint))
            tint = Color.cyan;
        return new Entry { Kind = kind, Label = label, SizeM = new Vector3(w, h, d), Tint = tint };
    }

    public static IList<Entry> All { get { return _entries; } }
    public static int Count { get { return _entries.Count; } }

    public static Entry At(int index)
    {
        return _entries[Mathf.Clamp(index, 0, _entries.Count - 1)];
    }

    /// <summary>Catalog entry for a kind, or the sofa when the kind is unknown.</summary>
    public static Entry Find(string kind)
    {
        for (int i = 0; i < _entries.Count; i++)
        {
            if (_entries[i].Kind == kind)
                return _entries[i];
        }
        return _entries[0];
    }

    public static bool IsKnown(string kind)
    {
        if (string.IsNullOrEmpty(kind))
            return false;
        for (int i = 0; i < _entries.Count; i++)
        {
            if (_entries[i].Kind == kind)
                return true;
        }
        return false;
    }
}
