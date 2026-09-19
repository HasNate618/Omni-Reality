using NUnit.Framework;

public class CaptureEnvelopeJsonTests
{
    static CaptureEnvelope HandBuiltEnvelope()
    {
        return new CaptureEnvelope
        {
            FrameId = "01k5j8g0008q3m7b2d6h9n4r5v",
            StageEpoch = 1,
            TUnixNs = 1758326400000000000L,
            Camera = "left",
            ImageW = 1280,
            ImageH = 960,
            SentW = 1280,
            SentH = 960,
            Fx = 657.5f,
            Fy = 657.5f,
            Cx = 640f,
            Cy = 480f,
            PosePx = 0f,
            PosePy = 1.6f,
            PosePz = 0f,
            PoseQx = 0f,
            PoseQy = 0f,
            PoseQz = 0f,
            PoseQw = 1f,
            CropSx = 1f,
            CropSy = 1f,
            CropTx = 0f,
            CropTy = 0f,
            Pointing = new CapturePointing
            {
                Source = "head",
                TUnixNs = 1758326400000000000L,
                OriginPx = 0f,
                OriginPy = 1.6f,
                OriginPz = 0f,
                DirX = 0f,
                DirY = 0f,
                DirZ = 1f,
            },
            WorldHint = new CaptureWorldHint
            {
                Px = 0f,
                Py = 1.4f,
                Pz = 2f,
                Nx = 0f,
                Ny = 1f,
                Nz = 0f,
                Source = "centre",
                TUnixNs = 1758326400000000000L,
            },
            CaptureGeometryAvailable = true,
        };
    }

    [Test]
    public void EnvelopeSerializesCameraLeftAndStagePoseFrame()
    {
        var json = HandBuiltEnvelope().ToSpecJson();
        Assert.That(json, Does.Contain("\"camera\":\"left\""));
        Assert.That(json, Does.Contain("\"frame\":\"openxr_floor_stage\""));
    }

    [Test]
    public void EnvelopeSerializesEverySpecKey()
    {
        var json = HandBuiltEnvelope().ToSpecJson();
        foreach (var key in ProtocolJson.SpecKeys)
        {
            Assert.That(json, Does.Contain("\"" + key + "\":"), "missing key: " + key);
        }
    }

    [Test]
    public void MissingPointingAndHintSerializeAsNull()
    {
        var env = HandBuiltEnvelope();
        env.Pointing = null;
        env.WorldHint = null;
        env.CaptureGeometryAvailable = false;
        var json = env.ToSpecJson();
        Assert.That(json, Does.Contain("\"pointing\":null"));
        Assert.That(json, Does.Contain("\"world_hint\":null"));
    }

    [Test]
    public void FullFrameSymmetricIntrinsicsPassThrough()
    {
        // Output == sensor, centred principal point: output pixels equal sensor pixels.
        bool ok = PcaIntrinsics.TryToOutputPixels(
            1315f, 1315f, 1280f, 960f, 2560f, 1920f, 2560f, 1920f,
            out float fx, out float fy, out float cx, out float cy);
        Assert.That(ok, Is.True);
        Assert.That(fx, Is.EqualTo(1315f).Within(1e-3f));
        Assert.That(fy, Is.EqualTo(1315f).Within(1e-3f));
        Assert.That(cx, Is.EqualTo(1280f).Within(1e-3f));
        Assert.That(cy, Is.EqualTo(960f).Within(1e-3f));
    }

    [Test]
    public void ResizedOutputScalesIntrinsics()
    {
        // Half-res output of the same 4:3 sensor: focal halves, centre halves.
        bool ok = PcaIntrinsics.TryToOutputPixels(
            1315f, 1315f, 1280f, 960f, 2560f, 1920f, 1280f, 960f,
            out float fx, out float fy, out float cx, out float cy);
        Assert.That(ok, Is.True);
        Assert.That(fx, Is.EqualTo(657.5f).Within(1e-3f));
        Assert.That(fy, Is.EqualTo(657.5f).Within(1e-3f));
        Assert.That(cx, Is.EqualTo(640f).Within(1e-3f));
        Assert.That(cy, Is.EqualTo(480f).Within(1e-3f));
    }

    [Test]
    public void AsymmetricPrincipalPointSurvivesCrop()
    {
        // Off-centre sensor principal point stays off-centre in output pixels.
        bool ok = PcaIntrinsics.TryToOutputPixels(
            1315f, 1315f, 1300f, 1000f, 2560f, 1920f, 1280f, 960f,
            out float fx, out float fy, out float cx, out float cy);
        Assert.That(ok, Is.True);
        Assert.That(cx, Is.EqualTo(650f).Within(1e-3f));
        Assert.That(cy, Is.EqualTo(460f).Within(1e-3f));
        Assert.That(fx, Is.EqualTo(657.5f).Within(1e-3f));
    }

    [Test]
    public void OffCentrePixelMatchesSensorRay()
    {
        // Converted output intrinsics must reproduce the SDK sensor-space ray
        // for an off-centre pixel (top-left pixel-centre convention).
        const float sensorW = 2560f, sensorH = 1920f, outW = 1280f, outH = 960f;
        Assert.That(PcaIntrinsics.TryToOutputPixels(
            1315f, 1315f, 1300f, 1000f, sensorW, sensorH, outW, outH,
            out float fx, out float fy, out float cx, out float cy), Is.True);
        PcaIntrinsics.SensorCropRegion(sensorW, sensorH, outW, outH,
            out float ox, out float oy, out float cw, out float ch);
        const float i = 100f, j = 900f;
        float u = (i + 0.5f) / outW;
        float v = 1f - (j + 0.5f) / outH;
        PcaIntrinsics.SensorRayDirection(u, v, 1315f, 1315f, 1300f, 1000f,
            ox, oy, cw, ch, out float sdx, out float sdy);
        PcaIntrinsics.OutputRayDirection(i + 0.5f, j + 0.5f, fx, fy, cx, cy,
            out float odx, out float ody);
        Assert.That(odx, Is.EqualTo(sdx).Within(1e-4f));
        Assert.That(ody, Is.EqualTo(sdy).Within(1e-4f));
    }

    [Test]
    public void LeadingRandomBytesChangeUlid()
    {
        // All 80 random bits must be encoded: flipping the leading random
        // byte must change the ID (the old ulong-shift build dropped it).
        var t = new System.DateTime(2026, 9, 19, 0, 0, 0, System.DateTimeKind.Utc);
        var a = new byte[] { 0x01, 0, 0, 0, 0, 0, 0, 0, 0, 0 };
        var b = new byte[] { 0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0 };
        string idA = SpatialRuntime.NewFrameId(t, a);
        string idB = SpatialRuntime.NewFrameId(t, b);
        Assert.That(idA, Has.Length.EqualTo(26));
        Assert.That(idA, Is.Not.EqualTo(idB));
    }
}
