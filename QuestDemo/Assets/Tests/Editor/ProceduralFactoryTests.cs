using System.Collections.Generic;
using NUnit.Framework;

public class ProceduralFactoryTests
{
    static ProtocolJson.ProceduralElement El(
        string element, string color = "cyan", string size = "small",
        string material = "solid", string text = null)
    {
        return new ProtocolJson.ProceduralElement
        {
            Element = element, Color = color, Size = size, Material = material, Text = text,
        };
    }

    [Test]
    public void RejectsSevenElements()
    {
        var els = new List<ProtocolJson.ProceduralElement>();
        for (int i = 0; i < 7; i++)
            els.Add(El("cube"));
        Assert.AreEqual("too_many_elements", ProceduralFactory.ValidateElements(els));
    }

    [Test]
    public void RejectsBadColor()
    {
        var els = new List<ProtocolJson.ProceduralElement> { El("cube", "hotpink") };
        Assert.AreEqual("bad_color", ProceduralFactory.ValidateElements(els));
    }

    [Test]
    public void RejectsTextOnCube()
    {
        var els = new List<ProtocolJson.ProceduralElement> { El("cube", "cyan", "small", "solid", "nope") };
        Assert.AreEqual("bad_text_target", ProceduralFactory.ValidateElements(els));
    }

    [Test]
    public void RejectsLongPanelText()
    {
        var els = new List<ProtocolJson.ProceduralElement>
            { El("panel", "white", "small", "solid", new string('x', 41)) };
        Assert.AreEqual("text_too_long", ProceduralFactory.ValidateElements(els));
    }

    [Test]
    public void RejectsOversizeComposition()
    {
        var els = new List<ProtocolJson.ProceduralElement>();
        for (int i = 0; i < 6; i++)
            els.Add(El("cube", "cyan", "large"));
        Assert.AreEqual("too_big", ProceduralFactory.ValidateElements(els));
    }

    [Test]
    public void AcceptsValidComposition()
    {
        var els = new List<ProtocolJson.ProceduralElement>
        {
            El("arrow", "cyan", "medium", "glow"),
            El("panel", "white", "small", "solid", "Inlet valve"),
        };
        Assert.IsNull(ProceduralFactory.ValidateElements(els));
    }

    [Test]
    public void ParsesElementsArray()
    {
        string arr = "[{\"element\":\"arrow\",\"color\":\"cyan\",\"size\":\"medium\",\"material\":\"glow\"}," +
            "{\"element\":\"panel\",\"color\":\"white\",\"size\":\"small\",\"material\":\"solid\",\"text\":\"Hi\"}]";
        List<ProtocolJson.ProceduralElement> els;
        Assert.IsTrue(ProtocolJson.TryParseProceduralElements(arr, out els));
        Assert.AreEqual(2, els.Count);
        Assert.AreEqual("arrow", els[0].Element);
        Assert.AreEqual("Hi", els[1].Text);
        Assert.IsNull(ProceduralFactory.ValidateElements(els));
    }
}
