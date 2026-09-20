using NUnit.Framework;

public class ProtocolExtentTests
{
    const string WithExtent =
        "{\"op_id\":\"01k5j8g0019q3m7b2d6h9n4r5v\",\"turn_id\":4,\"stage_epoch\":1," +
        "\"kind\":\"place_generated\",\"drawing_id\":null," +
        "\"job_id\":\"01m2xbae3n81b4scq0k83teqjw\"," +
        "\"extent_m\":[0.55,0.40,0.72]," +
        "\"target\":{\"type\":\"capture_hint\",\"frame_id\":\"01k5j8g0008q3m7b2d6h9n4r5v\"}}";

    const string WithoutExtent =
        "{\"op_id\":\"01k5j8g0019q3m7b2d6h9n4r5v\",\"turn_id\":4,\"stage_epoch\":1," +
        "\"kind\":\"place_generated\",\"drawing_id\":null," +
        "\"job_id\":\"01m2xbae3n81b4scq0k83teqjw\"," +
        "\"target\":{\"type\":\"capture_hint\",\"frame_id\":\"01k5j8g0008q3m7b2d6h9n4r5v\"}}";

    [Test]
    public void ParsesExtentMInWireOrder()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithExtent, out op));
        Assert.IsTrue(op.HasExtentM);
        Assert.AreEqual(0.55f, op.ExtentM.x, 1e-5f);
        Assert.AreEqual(0.40f, op.ExtentM.y, 1e-5f);
        Assert.AreEqual(0.72f, op.ExtentM.z, 1e-5f);
    }

    [Test]
    public void AbsentExtentLeavesHasExtentMFalse()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithoutExtent, out op));
        Assert.IsFalse(op.HasExtentM);
    }

    [Test]
    public void ParsesOffsetM()
    {
        string withOffset = WithExtent.Replace(
            "\"extent_m\":[0.55,0.40,0.72]", "\"extent_m\":[0.55,0.40,0.72],\"offset_m\":1.15");
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(withOffset, out op));
        Assert.IsTrue(op.HasOffsetM);
        Assert.AreEqual(1.15f, op.OffsetM, 1e-5f);
    }

    [Test]
    public void AbsentOffsetLeavesHasOffsetMFalse()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithExtent, out op));
        Assert.IsFalse(op.HasOffsetM);
        Assert.AreEqual(0f, op.OffsetM, 1e-5f);
    }

    [Test]
    public void ShortArrayIsIgnoredNotPartiallyApplied()
    {
        string twoAxes = WithExtent.Replace("[0.55,0.40,0.72]", "[0.55,0.40]");
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(twoAxes, out op));
        Assert.IsFalse(op.HasExtentM, "a malformed array must not claim an extent");
    }
}
