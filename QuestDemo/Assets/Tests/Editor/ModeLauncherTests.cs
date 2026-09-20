using NUnit.Framework;
using UnityEngine;

/// <summary>
/// The startup mode picker: card layout, hit testing, and what each choice
/// tells the laptop. The gesture itself needs a device; these cover the parts
/// that decide whether the right mode is requested at all.
/// </summary>
public class ModeLauncherTests
{
    GameObject _eye;

    [SetUp]
    public void MakeEye()
    {
        ModeLauncher.ResetForTests();
        _eye = new GameObject("CenterEyeAnchor");
        _eye.transform.position = new Vector3(0f, 1.6f, 0f);
    }

    [TearDown]
    public void DropEye()
    {
        ModeLauncher.ResetForTests();
        if (_eye != null)
            Object.DestroyImmediate(_eye);
    }

    [Test]
    public void ThreeModesAreOffered()
    {
        Assert.That(ModeLauncher.Count, Is.EqualTo(3));
        Assert.That(ModeLauncher.ModeAt(0), Is.EqualTo(ModeLauncher.Mode.Tracking));
        Assert.That(ModeLauncher.ModeAt(1), Is.EqualTo(ModeLauncher.Mode.Tutorial));
        Assert.That(ModeLauncher.ModeAt(2), Is.EqualTo(ModeLauncher.Mode.Layout));
    }

    [Test]
    public void EveryCardSaysWhatToDo()
    {
        for (int i = 0; i < ModeLauncher.Count; i++)
        {
            Assert.That(ModeLauncher.TitleAt(i), Is.Not.Null.And.Not.Empty);
            // The hint is the whole point of the Tutorial card: the mode is
            // identical to Tracking, so only the prompt distinguishes them.
            Assert.That(ModeLauncher.HintAt(i), Does.Contain("trigger"));
        }
    }

    [Test]
    public void NothingIsChosenBeforeThePickerIsUsed()
    {
        Assert.That(ModeLauncher.HasChosen, Is.False);
        Assert.That(ModeLauncher.Chosen, Is.EqualTo(ModeLauncher.Mode.None));
    }

    [Test]
    public void TutorialAsksTheLaptopForTracking()
    {
        // The guided tutorial is a branch inside the tracking planner, so the
        // laptop must build the tracking configuration for it.
        var go = new GameObject("Launcher");
        var launcher = go.AddComponent<ModeLauncher>();
        try
        {
            launcher.Choose(1);
            Assert.That(ModeLauncher.Chosen, Is.EqualTo(ModeLauncher.Mode.Tutorial));
            Assert.That(ModeLauncher.WireMode, Is.EqualTo("tutorial"));

            launcher.Choose(2);
            Assert.That(ModeLauncher.WireMode, Is.EqualTo("layout"));

            launcher.Choose(0);
            Assert.That(ModeLauncher.WireMode, Is.EqualTo("tracking"));
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void ChoosingArmsTheAppAndClosesThePicker()
    {
        var go = new GameObject("Launcher");
        var launcher = go.AddComponent<ModeLauncher>();
        try
        {
            launcher.Open();
            Assert.That(ModeLauncher.IsOpen, Is.True);
            launcher.Choose(2);
            Assert.That(ModeLauncher.IsOpen, Is.False, "picker must close on choice");
            Assert.That(ModeLauncher.HasChosen, Is.True);
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void CardCopyFitsInsideItsCard()
    {
        for (int i = 0; i < ModeLauncher.Count; i++)
        {
            foreach (string line in LayoutLabel.Wrap(ModeLauncher.TitleAt(i),
                                                     ModeLauncher.WrapChars).Split('\n'))
                Assert.That(line.Length, Is.LessThanOrEqualTo(ModeLauncher.WrapChars),
                            "title overflows: " + line);
            foreach (string line in LayoutLabel.Wrap(ModeLauncher.HintAt(i),
                                                     ModeLauncher.WrapChars).Split('\n'))
                Assert.That(line.Length, Is.LessThanOrEqualTo(ModeLauncher.WrapChars),
                            "phrase overflows: " + line);
        }
    }

    [Test]
    public void WrapBreaksOnWordsAndKeepsEveryWord()
    {
        string wrapped = LayoutLabel.Wrap("show me how to tidy this desk", 10);
        foreach (string line in wrapped.Split('\n'))
            Assert.That(line.Length, Is.LessThanOrEqualTo(10), line);
        Assert.That(wrapped.Replace("\n", " "), Is.EqualTo("show me how to tidy this desk"));
    }

    [Test]
    public void WrapKeepsAuthoredLineBreaks()
    {
        Assert.That(LayoutLabel.Wrap("one\ntwo", 20), Is.EqualTo("one\ntwo"));
        Assert.That(LayoutLabel.Wrap(null, 10), Is.EqualTo(""));
        Assert.That(LayoutLabel.Wrap("word", 0), Is.EqualTo("word"));
    }

    [Test]
    public void EveryModeTellsYouHowToLeaveIt()
    {
        // Without this the picker is a one-way door: nothing on screen said
        // which button goes back.
        foreach (ModeLauncher.Mode m in new[] { ModeLauncher.Mode.Tracking,
                                                ModeLauncher.Mode.Tutorial,
                                                ModeLauncher.Mode.Layout })
        {
            Assert.That(LayoutMode.ControlsFor(m, false), Does.Contain(LayoutMode.ExitCopy), m.ToString());
        }
        Assert.That(LayoutMode.ControlsFor(ModeLauncher.Mode.Layout, true),
                    Does.Contain(LayoutMode.ExitCopy), "also while resizing");
        Assert.That(LayoutMode.ExitCopy.ToLower(), Does.Contain("menu"));
    }

    [Test]
    public void EachModeGetsItsOwnControls()
    {
        string tracking = LayoutMode.ControlsFor(ModeLauncher.Mode.Tracking, false);
        string tutorial = LayoutMode.ControlsFor(ModeLauncher.Mode.Tutorial, false);
        string layout = LayoutMode.ControlsFor(ModeLauncher.Mode.Layout, false);
        Assert.That(tracking, Is.Not.EqualTo(tutorial));
        Assert.That(tutorial, Is.Not.EqualTo(layout));
        Assert.That(layout, Does.Contain("resize"));
        Assert.That(tutorial.ToLower(), Does.Contain("next"));
    }

    [Test]
    public void MenuWaitsForATrackedHeadBeforePlacingItself()
    {
        // An untracked head sits at the play-space origin, which is the floor.
        // Placing against it is what put the menu at the bottom of the view.
        Assert.That(ModeLauncher.MinHeadHeightM, Is.GreaterThan(0.2f));
    }

    [Test]
    public void CardsDoNotOverlap()
    {
        for (int i = 1; i < ModeLauncher.Count; i++)
        {
            float gap = ModeLauncher.CellX(i) - ModeLauncher.CellX(i - 1);
            Assert.That(gap, Is.GreaterThan(ModeLauncher.CardW), "mode cards touch");
        }
    }

    [Test]
    public void CardsAreCentredOnTheMenu()
    {
        float first = ModeLauncher.CellX(0);
        float last = ModeLauncher.CellX(ModeLauncher.Count - 1);
        Assert.That(first + last, Is.EqualTo(0f).Within(1e-4f));
    }

    [Test]
    public void PickerHitTestsTheCardUnderTheRay()
    {
        var go = new GameObject("Launcher");
        var launcher = go.AddComponent<ModeLauncher>();
        try
        {
            launcher.Open();
            Vector3 centre = launcher.transform.position;
            Vector3 hit;
            var straight = new Ray(centre - (launcher.transform.forward * 1f),
                                   launcher.transform.forward);
            Assert.That(launcher.IndexUnderRay(straight, out hit),
                        Is.InRange(0, ModeLauncher.Count - 1));

            var high = new Ray(centre - (launcher.transform.forward * 1f) + (Vector3.up * 2f),
                               launcher.transform.forward);
            Assert.That(launcher.IndexUnderRay(high, out hit), Is.EqualTo(-1));
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void ClosedPickerNeverPicks()
    {
        var go = new GameObject("Launcher");
        var launcher = go.AddComponent<ModeLauncher>();
        try
        {
            Vector3 hit;
            Assert.That(launcher.IndexUnderRay(new Ray(Vector3.zero, Vector3.forward), out hit),
                        Is.EqualTo(-1));
        }
        finally
        {
            Object.DestroyImmediate(go);
        }
    }

    [Test]
    public void HelloCarriesTheChosenMode()
    {
        string json = ProtocolJson.BuildTrackingHello("74", "layout");
        Assert.That(json, Does.Contain("\"mode\":\"layout\""));
        Assert.That(json, Does.Contain("\"capabilities\""));

        // An omitted mode must not add the key at all, so older laptops and
        // the headset-free tools keep the payload they already validate.
        string plain = ProtocolJson.BuildTrackingHello("74", null);
        Assert.That(plain, Does.Not.Contain("mode"));
    }
}
