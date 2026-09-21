using NUnit.Framework;

/// <summary>
/// The optional `name` field on a place_generated op. It is optional by
/// contract, so both the present and absent cases are behaviour, not just
/// parsing detail: the absent case is what keeps an older op or a pre-baked
/// placement from rendering a blank chip.
/// </summary>
public class ProtocolNameTests
{
    const string WithName =
        "{\"op_id\":\"01k5j8g0019q3m7b2d6h9n4r5v\",\"turn_id\":4,\"stage_epoch\":1," +
        "\"kind\":\"place_generated\",\"drawing_id\":null," +
        "\"job_id\":\"01m2xbae3n81b4scq0k83teqjw\"," +
        "\"name\":\"oak side table\"," +
        "\"extent_m\":[0.55,0.40,0.72]," +
        "\"target\":{\"type\":\"capture_hint\",\"frame_id\":\"01k5j8g0008q3m7b2d6h9n4r5v\"}}";

    const string WithoutName =
        "{\"op_id\":\"01k5j8g0019q3m7b2d6h9n4r5v\",\"turn_id\":4,\"stage_epoch\":1," +
        "\"kind\":\"place_generated\",\"drawing_id\":null," +
        "\"job_id\":\"01m2xbae3n81b4scq0k83teqjw\"," +
        "\"extent_m\":[0.55,0.40,0.72]," +
        "\"target\":{\"type\":\"capture_hint\",\"frame_id\":\"01k5j8g0008q3m7b2d6h9n4r5v\"}}";

    [Test]
    public void ParsesTheListingName()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithName, out op));
        Assert.AreEqual("oak side table", op.Name);
    }

    [Test]
    public void AbsentNameStaysNull()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithoutName, out op));
        Assert.IsNull(op.Name);
    }

    [Test]
    public void ExplicitJsonNullNameStaysNull()
    {
        string withNull = WithName.Replace("\"name\":\"oak side table\"", "\"name\":null");
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(withNull, out op));
        Assert.IsNull(op.Name);
    }

    [Test]
    public void AnEmptyNameParsesAsEmptyNotNull()
    {
        string withEmpty = WithName.Replace("\"oak side table\"", "\"\"");
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(withEmpty, out op));
        Assert.AreEqual("", op.Name);
    }

    [Test]
    public void NameDoesNotDisturbTheOtherFields()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithName, out op));
        Assert.AreEqual("place_generated", op.Kind);
        Assert.AreEqual("01m2xbae3n81b4scq0k83teqjw", op.JobId);
        Assert.IsTrue(op.HasExtentM);
        Assert.AreEqual(0.55f, op.ExtentM.x, 1e-5f);
        Assert.AreEqual(4, op.TurnId);
    }

    [Test]
    public void AnUnnamedOpStillFeedsAReadableChip()
    {
        // End to end over the defensive path: parse an op with no name and
        // hand it to the chip formatter exactly as the placer does.
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithoutName, out op));
        string chip = ItemQueueOverlay.FormatChip(new ItemQueueOverlay.Entry
        {
            Id = op.JobId,
            Name = op.Name,
            ExtentM = op.ExtentM,
        });
        Assert.AreEqual("0.55 x 0.40 x 0.72 m", chip);
    }

    [Test]
    public void ANamedOpFeedsANamedChip()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithName, out op));
        string chip = ItemQueueOverlay.FormatChip(new ItemQueueOverlay.Entry
        {
            Id = op.JobId,
            Name = op.Name,
            ExtentM = op.ExtentM,
        });
        Assert.AreEqual("oak side table\n0.55 x 0.40 x 0.72 m", chip);
    }
}
