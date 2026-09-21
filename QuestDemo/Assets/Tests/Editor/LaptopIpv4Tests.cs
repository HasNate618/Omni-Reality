using NUnit.Framework;

/// <summary>
/// The laptop address is the switch that turns the whole coordinator on, so it
/// must resolve identically for a device-seeded pref and a build-time bake.
/// </summary>
public class LaptopIpv4Tests
{
    [Test]
    public void PrefWinsOverTheBakedAddress()
    {
        Assert.AreEqual("10.0.0.8",
            SpatialRuntime.ChooseLaptopIpv4("10.0.0.8", "192.168.1.50"));
    }

    [Test]
    public void BakedAddressIsUsedWhenThePrefIsEmpty()
    {
        Assert.AreEqual("192.168.1.50",
            SpatialRuntime.ChooseLaptopIpv4("", "192.168.1.50"));
    }

    [Test]
    public void NoSourceResolvesEmptyRatherThanAGuess()
    {
        Assert.AreEqual("", SpatialRuntime.ChooseLaptopIpv4("", ""));
    }

    [Test]
    public void WhitespaceIsTrimmedAndDoesNotCountAsConfigured()
    {
        Assert.AreEqual("", SpatialRuntime.ChooseLaptopIpv4("   ", ""));
        Assert.AreEqual("10.0.0.8", SpatialRuntime.ChooseLaptopIpv4("", " 10.0.0.8\n"));
    }
}
