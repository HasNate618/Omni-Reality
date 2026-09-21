using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;

public class TakeHomeCardTests
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
    public void EmptyLogIsStatedNotBlank()
    {
        // An empty panel leaves the wearer wondering whether it worked.
        Assert.AreEqual("Nothing placed yet.", TakeHomeCard.FormatCard(null));
        Assert.AreEqual("Nothing placed yet.",
            TakeHomeCard.FormatCard(new List<ItemQueueOverlay.Entry>()));
    }

    [Test]
    public void CardListsOneLinePerItemWithDims()
    {
        var entries = new List<ItemQueueOverlay.Entry>
        {
            Item("a", "arc lamp", 0.40f, 0.40f, 1.50f),
            Item("b", "side table", 0.55f, 0.55f, 0.40f),
        };
        string[] lines = TakeHomeCard.FormatCard(entries).Split('\n');
        Assert.AreEqual(3, lines.Length);
        Assert.AreEqual("2 items placed", lines[0]);
        Assert.AreEqual("arc lamp  0.40 x 0.40 x 1.50 m", lines[1]);
        Assert.AreEqual("side table  0.55 x 0.55 x 0.40 m", lines[2]);
    }

    [Test]
    public void SingularCountReadsAsOne()
    {
        var entries = new List<ItemQueueOverlay.Entry> { Item("a", "arc lamp", 0.4f, 0.4f, 1.5f) };
        Assert.AreEqual("1 item placed", TakeHomeCard.FormatCard(entries).Split('\n')[0]);
    }

    [Test]
    public void UnnamedItemStillCarriesItsDims()
    {
        // extent_m is guaranteed on the op; only the name is optional.
        var entries = new List<ItemQueueOverlay.Entry> { Item("a", null, 0.55f, 0.40f, 0.72f) };
        Assert.AreEqual("0.55 x 0.40 x 0.72 m", TakeHomeCard.FormatLine(entries[0]));
    }

    [Test]
    public void ShowRaisesTheCardOverTheQueuesContents()
    {
        var cardGo = new GameObject("CardTest");
        var queueGo = new GameObject("QueueTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var queue = queueGo.AddComponent<ItemQueueOverlay>();
            queue.Record("a", "arc lamp", new Vector3(0.40f, 0.40f, 1.50f));
            var card = cardGo.AddComponent<TakeHomeCard>();
            card.Bind(eye.transform, queue);
            Assert.IsFalse(card.IsShowing);

            card.Show();
            Assert.IsTrue(card.IsShowing);
            var label = cardGo.GetComponentInChildren<TextMesh>(true);
            Assert.IsNotNull(label);
            Assert.That(label.text, Does.Contain("arc lamp"));
            Assert.That(label.text, Does.Contain("0.40 x 0.40 x 1.50 m"));
        }
        finally
        {
            Object.DestroyImmediate(cardGo);
            Object.DestroyImmediate(queueGo);
            Object.DestroyImmediate(eye);
        }
    }

    [Test]
    public void ToggleFlipsVisibility()
    {
        var go = new GameObject("CardToggleTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var card = go.AddComponent<TakeHomeCard>();
            card.Bind(eye.transform, null);
            card.Toggle();
            Assert.IsTrue(card.IsShowing);
            card.Toggle();
            Assert.IsFalse(card.IsShowing);
        }
        finally
        {
            Object.DestroyImmediate(go);
            Object.DestroyImmediate(eye);
        }
    }

    [Test]
    public void HideKeepsTheCardTextForTheNextShow()
    {
        var cardGo = new GameObject("CardHideTest");
        var queueGo = new GameObject("QueueTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var queue = queueGo.AddComponent<ItemQueueOverlay>();
            queue.Record("a", "arc lamp", new Vector3(0.40f, 0.40f, 1.50f));
            var card = cardGo.AddComponent<TakeHomeCard>();
            card.Bind(eye.transform, queue);
            card.Show();
            card.Hide();
            Assert.IsFalse(card.IsShowing);
            var label = cardGo.GetComponentInChildren<TextMesh>(true);
            Assert.IsFalse(label.gameObject.activeSelf);
            Assert.That(label.text, Does.Contain("arc lamp"));
        }
        finally
        {
            Object.DestroyImmediate(cardGo);
            Object.DestroyImmediate(queueGo);
            Object.DestroyImmediate(eye);
        }
    }

    [Test]
    public void CardSeesItemsPlacedAfterItWasBound()
    {
        var cardGo = new GameObject("CardLateTest");
        var queueGo = new GameObject("QueueTest");
        var eye = new GameObject("EyeTest");
        try
        {
            var queue = queueGo.AddComponent<ItemQueueOverlay>();
            var card = cardGo.AddComponent<TakeHomeCard>();
            card.Bind(eye.transform, queue);
            queue.Record("a", "arc lamp", new Vector3(0.40f, 0.40f, 1.50f));
            card.Show();
            var label = cardGo.GetComponentInChildren<TextMesh>(true);
            Assert.That(label.text, Does.Contain("arc lamp"));
        }
        finally
        {
            Object.DestroyImmediate(cardGo);
            Object.DestroyImmediate(queueGo);
            Object.DestroyImmediate(eye);
        }
    }
}
