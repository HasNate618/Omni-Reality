using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;

public class ItemQueueOverlayTests
{
    static ItemQueueOverlay.Entry Item(string id, string name, float w, float d, float h)
    {
        return new ItemQueueOverlay.Entry
        {
            Id = id,
            Name = name,
            ExtentM = new Vector3(w, d, h),
        };
    }

    [Test]
    public void DimsRenderInWireOrderWidthDepthHeight()
    {
        // extent_m is (w, d, h). Rendering it as (x, y, z) would transpose
        // depth and height on every chip.
        Assert.AreEqual("0.55 x 0.40 x 0.72 m",
            ItemQueueOverlay.FormatDims(new Vector3(0.55f, 0.40f, 0.72f)));
    }

    [Test]
    public void DimsUseTwoDecimals()
    {
        Assert.AreEqual("1.00 x 0.50 x 0.10 m",
            ItemQueueOverlay.FormatDims(new Vector3(1f, 0.5f, 0.1f)));
    }

    [Test]
    public void ChipShowsNameOverDims()
    {
        string text = ItemQueueOverlay.FormatChip(Item("a", "oak side table", 0.55f, 0.40f, 0.72f));
        Assert.AreEqual("oak side table\n0.55 x 0.40 x 0.72 m", text);
    }

    [Test]
    public void ChipWithoutANameFallsBackToDimsOnly()
    {
        // `name` is optional on the wire by contract: the pre-baked path, an
        // older op, or a future emit site that omits it. A blank chip or a
        // throw here is the visible failure this guards.
        Assert.AreEqual("0.55 x 0.40 x 0.72 m",
            ItemQueueOverlay.FormatChip(Item("a", null, 0.55f, 0.40f, 0.72f)));
    }

    [Test]
    public void ChipTreatsAWhitespaceOnlyNameAsMissing()
    {
        Assert.AreEqual("0.55 x 0.40 x 0.72 m",
            ItemQueueOverlay.FormatChip(Item("a", "   ", 0.55f, 0.40f, 0.72f)));
    }

    [Test]
    public void ChipNeverLeadsWithAnEmptyNameLine()
    {
        string text = ItemQueueOverlay.FormatChip(Item("a", "", 0.55f, 0.40f, 0.72f));
        Assert.IsFalse(text.StartsWith("\n"), text);
        Assert.AreEqual(1, text.Split('\n').Length);
    }

    [Test]
    public void LongNameIsElidedRatherThanBlowingOutTheChip()
    {
        string text = ItemQueueOverlay.FormatChip(Item("a", new string('x', 80), 1f, 1f, 1f));
        string first = text.Split('\n')[0];
        Assert.AreEqual(ItemQueueOverlay.MaxNameChars, first.Length);
        Assert.IsTrue(first.EndsWith("..."));
    }

    [Test]
    public void AppendReplacesAnEntryWithTheSameIdInPlace()
    {
        var entries = new List<ItemQueueOverlay.Entry>();
        entries = ItemQueueOverlay.Append(entries, Item("a", "one", 1f, 1f, 1f));
        entries = ItemQueueOverlay.Append(entries, Item("b", "two", 1f, 1f, 1f));
        entries = ItemQueueOverlay.Append(entries, Item("a", "one revised", 2f, 1f, 1f));
        Assert.AreEqual(2, entries.Count);
        Assert.AreEqual("one revised", entries[0].Name);
        Assert.AreEqual("two", entries[1].Name);
    }

    [Test]
    public void AppendDropsTheOldestPastTheCap()
    {
        var entries = new List<ItemQueueOverlay.Entry>();
        for (int i = 0; i < ItemQueueOverlay.MaxEntries + 2; i++)
            entries = ItemQueueOverlay.Append(entries, Item("id" + i, "n" + i, 1f, 1f, 1f));
        Assert.AreEqual(ItemQueueOverlay.MaxEntries, entries.Count);
        Assert.AreEqual("id2", entries[0].Id, "the two oldest must be gone");
    }

    [Test]
    public void VisibleWindowsToTheNewestChips()
    {
        var entries = new List<ItemQueueOverlay.Entry>();
        for (int i = 0; i < 5; i++)
            entries = ItemQueueOverlay.Append(entries, Item("id" + i, "n" + i, 1f, 1f, 1f));
        List<ItemQueueOverlay.Entry> visible = ItemQueueOverlay.Visible(entries);
        Assert.AreEqual(ItemQueueOverlay.MaxChips, visible.Count);
        Assert.AreEqual("id2", visible[0].Id);
        Assert.AreEqual("id4", visible[visible.Count - 1].Id);
    }

    [Test]
    public void VisibleBelowTheCapShowsEverything()
    {
        var entries = new List<ItemQueueOverlay.Entry> { Item("a", "one", 1f, 1f, 1f) };
        Assert.AreEqual(1, ItemQueueOverlay.Visible(entries).Count);
    }

    [Test]
    public void VisibleOfNothingIsEmpty()
    {
        Assert.AreEqual(0, ItemQueueOverlay.Visible(null).Count);
        Assert.AreEqual(0, ItemQueueOverlay.Visible(new List<ItemQueueOverlay.Entry>()).Count);
    }

    [Test]
    public void ChipsStackDownwardWithoutOverlapping()
    {
        Vector3 first = ItemQueueOverlay.ChipLocalPosition(0);
        Vector3 second = ItemQueueOverlay.ChipLocalPosition(1);
        Assert.Less(second.y, first.y);
        Assert.AreEqual(ItemQueueOverlay.ChipSpacingM, first.y - second.y, 1e-5f);
        Assert.AreEqual(first.z, second.z, 1e-5f);
    }

    [Test]
    public void ChipsSitLeftOfGazeAndInFront()
    {
        Vector3 position = ItemQueueOverlay.ChipLocalPosition(0);
        Assert.Less(position.x, 0f);
        Assert.Greater(position.z, 0f);
    }

    [Test]
    public void RecordRendersOnlyTheChipWindow()
    {
        var go = new GameObject("ItemQueueTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var overlay = go.AddComponent<ItemQueueOverlay>();
            overlay.Bind(eye.transform);
            overlay.Record("a", "arc lamp", new Vector3(0.40f, 0.40f, 1.50f));
            overlay.Record("b", "side table", new Vector3(0.55f, 0.55f, 0.40f));
            overlay.Record("c", "speaker", new Vector3(0.20f, 0.20f, 0.30f));
            overlay.Record("d", "fourth", new Vector3(0.10f, 0.10f, 0.10f));
            Assert.AreEqual(4, overlay.Count, "the log keeps what the chips window out");

            int active = 0;
            foreach (TextMesh mesh in go.GetComponentsInChildren<TextMesh>(true))
                if (mesh.gameObject.activeSelf) active++;
            Assert.AreEqual(ItemQueueOverlay.MaxChips, active);
        }
        finally
        {
            Object.DestroyImmediate(go);
            Object.DestroyImmediate(eye);
        }
    }

    [Test]
    public void ClearEmptiesBothTheLogAndTheChips()
    {
        var go = new GameObject("ItemQueueClearTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var overlay = go.AddComponent<ItemQueueOverlay>();
            overlay.Bind(eye.transform);
            overlay.Record("a", "arc lamp", new Vector3(0.40f, 0.40f, 1.50f));
            overlay.Clear();
            Assert.AreEqual(0, overlay.Count);
            foreach (TextMesh mesh in go.GetComponentsInChildren<TextMesh>(true))
                Assert.IsFalse(mesh.gameObject.activeSelf);
        }
        finally
        {
            Object.DestroyImmediate(go);
            Object.DestroyImmediate(eye);
        }
    }

    [Test]
    public void EntriesStayLiveAcrossRecordsForTheCard()
    {
        // TakeHomeCard holds this list. A Record that swapped the instance
        // rather than mutating it in place would leave the card reading an
        // empty log while the chips showed three items.
        var go = new GameObject("ItemQueueLiveTest");
        try
        {
            var overlay = go.AddComponent<ItemQueueOverlay>();
            IReadOnlyList<ItemQueueOverlay.Entry> shared = overlay.Entries;
            overlay.Record("a", "arc lamp", new Vector3(0.40f, 0.40f, 1.50f));
            Assert.AreEqual(1, shared.Count);
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }
}
