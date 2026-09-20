using UnityEngine;

/// <summary>
/// The picker shown when the app starts: Tracking, Tutorial, Interior Design.
///
/// Mode has to be decided before anything connects. `QuestStreamInput` reads
/// its enable flag once in Start and can permanently disable itself, and the
/// `hello` payload is built in `CoordinatorClient.Begin`, so a choice made
/// after the socket opens would arrive too late. `SpatialRuntime.TickCoordinator`
/// therefore waits on `HasChosen` before building the client at all.
///
/// Tracking and Tutorial deliberately send the same mode to the laptop. The
/// guided tutorial is a branch inside the tracking planner -- the model
/// returns a guide instead of a track when you ask for step-by-step help --
/// so the two cards differ only in what they tell you to say.
/// </summary>
public sealed class ModeLauncher : MonoBehaviour
{
    public enum Mode { None = 0, Tracking = 1, Tutorial = 2, Layout = 3 }

    public const float DistanceM = 1.35f;
    public const float CardW = 0.52f;
    public const float CardH = 0.36f;
    public const float GapM = 0.08f;
    /// <summary>Longest line a card can hold before the text runs past it.</summary>
    public const int WrapChars = 16;

    static ModeLauncher _instance;

    public static Mode Chosen { get; private set; }
    public static bool HasChosen { get { return Chosen != Mode.None; } }
    /// <summary>True while the picker is up: every other component stands down.</summary>
    public static bool IsOpen { get { return _instance != null && _instance._open; } }

    /// <summary>What the laptop is told in `hello`. Tutorial rides on tracking.</summary>
    public static string WireMode
    {
        get
        {
            switch (Chosen)
            {
                case Mode.Layout: return "layout";
                case Mode.Tutorial: return "tutorial";
                default: return "tracking";
            }
        }
    }

    struct Card
    {
        public Mode Mode;
        public string Title;
        public string Hint;
        public Color Tint;
    }

    static readonly Card[] _cards =
    {
        new Card { Mode = Mode.Tracking, Title = "Tracking",
                   Hint = "\"track the water bottle\"",
                   Tint = new Color(0.31f, 0.76f, 0.97f, 1f) },
        new Card { Mode = Mode.Tutorial, Title = "Tutorial",
                   Hint = "\"show me how to tidy this\"",
                   Tint = new Color(1f, 0.72f, 0.30f, 1f) },
        new Card { Mode = Mode.Layout, Title = "Interior Design",
                   Hint = "\"fill my corner\"",
                   Tint = new Color(0.51f, 0.89f, 0.55f, 1f) },
    };

    public static int Count { get { return _cards.Length; } }
    public static Mode ModeAt(int index) { return _cards[Mathf.Clamp(index, 0, Count - 1)].Mode; }
    public static string TitleAt(int index) { return _cards[Mathf.Clamp(index, 0, Count - 1)].Title; }
    public static string HintAt(int index) { return _cards[Mathf.Clamp(index, 0, Count - 1)].Hint; }

    bool _open;
    bool _built;
    bool _placed;
    int _hover = -1;
    Transform _eye;
    LayoutPointer _pointer;
    LayoutLabel _title;
    LayoutLabel _hint;
    readonly System.Collections.Generic.List<LayoutLabel> _labels =
        new System.Collections.Generic.List<LayoutLabel>();
    readonly System.Collections.Generic.List<LayoutLabel> _phrases =
        new System.Collections.Generic.List<LayoutLabel>();

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Bootstrap()
    {
        Ensure().Open();
    }

    public static ModeLauncher Ensure()
    {
        if (_instance != null)
            return _instance;
        var go = new GameObject("ModeLauncher");
        DontDestroyOnLoad(go);
        _instance = go.AddComponent<ModeLauncher>();
        return _instance;
    }

    void Awake()
    {
        _instance = this;
        _pointer = gameObject.AddComponent<LayoutPointer>();
    }

    /// <summary>Card centre in menu-local space; one source of truth.</summary>
    public static float CellX(int index)
    {
        float total = (Count * CardW) + ((Count - 1) * GapM);
        return -total * 0.5f + (CardW * 0.5f) + (index * (CardW + GapM));
    }

    public void Open()
    {
        // Claim the static slot: a launcher built directly (tests, or a second
        // one added to the scene) must be the one IsOpen reports on.
        _instance = this;
        Build();
        SetChildrenVisible(true);
        _open = true;
        _hover = -1;
        // Placed by Update, not here. Bootstrap opens this at AfterSceneLoad,
        // before head tracking has a pose -- placing now left the menu at the
        // world origin, which is why it appeared in a corner of the room.
        _placed = false;
        Debug.Log("QUEST_MODE launcher open");
    }

    /// <summary>
    /// Put the menu squarely in front of the wearer, centred on where they are
    /// actually looking -- pitch included, so it is not on the floor when they
    /// look down. Runs once per open, as soon as a real head pose exists.
    /// </summary>
    /// <summary>Below this the head pose is not tracked yet, not crouching.</summary>
    public const float MinHeadHeightM = 0.6f;

    bool TryPlace()
    {
        FindEye();
        if (_eye == null)
            return false;
        Vector3 eye = _eye.position;
        // The tracking origin is the floor, so a tracked head is ~1.6 m up.
        // An untracked pose sits at the origin, and placing against it put the
        // menu on the floor. Checking `forward` here is useless: an identity
        // rotation still has a unit forward.
        if (eye.y < MinHeadHeightM)
            return false;

        Vector3 flat = _eye.forward;
        flat.y = 0f;
        if (flat.sqrMagnitude < 1e-6f)
        {
            // Looking straight up or down: fall back to the head's yaw.
            flat = _eye.rotation * Vector3.forward;
            flat.y = 0f;
            if (flat.sqrMagnitude < 1e-6f)
                return false;
        }
        flat.Normalize();
        // Dead ahead at eye level. Using the pitched forward meant the menu
        // landed wherever the wearer happened to be looking when it opened --
        // on the floor if they were looking down.
        transform.position = new Vector3(eye.x, eye.y, eye.z) + (flat * DistanceM);
        transform.rotation = Quaternion.LookRotation(flat, Vector3.up);
        return true;
    }

    public void Close()
    {
        _open = false;
        _hover = -1;
        SetChildrenVisible(false);
        if (_pointer != null)
            _pointer.SetVisible(false);
    }

    void FindEye()
    {
        if (_eye != null)
            return;
        var go = GameObject.Find("CenterEyeAnchor");
        if (go != null)
            _eye = go.transform;
    }

    void Build()
    {
        if (_built)
            return;
        _built = true;
        for (int i = 0; i < Count; i++)
        {
            float x = CellX(i);
            var card = GameObject.CreatePrimitive(PrimitiveType.Quad);
            card.name = "ModeCard_" + _cards[i].Mode;
            Collider col = card.GetComponent<Collider>();
            if (col != null)
                LayoutBox.DestroySafe(col);
            card.transform.SetParent(transform, false);
            card.transform.localPosition = new Vector3(x, 0f, 0f);
            card.transform.localScale = new Vector3(CardW, CardH, 1f);
            var rend = card.GetComponent<Renderer>();
            if (rend != null)
            {
                Color c = _cards[i].Tint;
                c.a = 0.34f;
                rend.sharedMaterial = LayoutBox.BuildMaterial(c);
                rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            }

            // Title and phrase are separate labels so the name can be big and
            // the phrase small; one label at one size made the longest card
            // overflow before the shortest was readable.
            LayoutLabel label = LayoutLabel.Create(transform, _cards[i].Tint);
            label.transform.localPosition = new Vector3(x, CardH * 0.06f, -0.01f);
            label.SetTextWrapped(_cards[i].Title, WrapChars);
            label.SetScale(0.9f);
            _labels.Add(label);

            LayoutLabel phrase = LayoutLabel.Create(transform, Color.white);
            phrase.transform.localPosition = new Vector3(x, -CardH * 0.22f, -0.01f);
            phrase.SetTextWrapped(_cards[i].Hint, WrapChars);
            phrase.SetScale(0.5f);
            _phrases.Add(phrase);
        }
        _title = LayoutLabel.Create(transform, Color.white);
        _title.transform.localPosition = new Vector3(0f, CardH * 0.78f, -0.01f);
        _title.SetText("Choose a mode");
        _hint = LayoutLabel.Create(transform, Color.white);
        _hint.transform.localPosition = new Vector3(0f, -CardH * 0.80f, -0.01f);
        _hint.SetText("Point and pull the trigger\n"
                      + "Then hold the side trigger and say the phrase\n"
                      + "Left Menu or Y reopens this");
        _hint.SetScale(0.55f);
    }

    void SetChildrenVisible(bool on)
    {
        for (int i = 0; i < _labels.Count; i++)
            _labels[i].SetVisible(on);
        for (int i = 0; i < _phrases.Count; i++)
            _phrases[i].SetVisible(on);
        if (_title != null)
            _title.SetVisible(on);
        if (_hint != null)
            _hint.SetVisible(on);
        foreach (Transform child in transform)
        {
            var rend = child.GetComponent<MeshRenderer>();
            if (rend != null && child.GetComponent<TextMesh>() == null)
                rend.enabled = on;
        }
    }

    void Update()
    {
        // Two ways back to the picker, both on the left controller: Menu, and
        // Y as a backstop because some Horizon versions swallow Menu for the
        // system UI. Neither is used by anything else in the project.
        if (!_open && (OVRInput.GetDown(OVRInput.Button.Start, OVRInput.Controller.LTouch)
                       || OVRInput.GetDown(OVRInput.Button.Two, OVRInput.Controller.LTouch)))
        {
            Open();
            return;
        }
        if (!_open)
            return;
        if (!_placed)
        {
            // Keep it hidden until it has somewhere sensible to be.
            SetChildrenVisible(_placed = TryPlace());
            if (!_placed)
                return;
        }
        if (_pointer == null || !_pointer.HasAim)
            return;

        Ray ray = _pointer.AimRay;
        Vector3 hit;
        int index = IndexUnderRay(ray, out hit);
        SetHover(index);
        if (index >= 0)
            _pointer.Draw(hit, true, false);
        else
            _pointer.DrawMiss();

        if (index >= 0 && OVRInput.GetDown(
                OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch))
            Choose(index);
    }

    public void Choose(int index)
    {
        Mode picked = ModeAt(index);
        bool changed = picked != Chosen;
        Chosen = picked;
        Close();
        Debug.Log("QUEST_MODE chose " + picked);
        // Switching modes needs a fresh connection: the laptop builds its
        // planner from the hello, and the hello is sent once per socket.
        if (changed)
        {
            var runtime = FindAnyObjectByType<SpatialRuntime>();
            if (runtime != null)
                runtime.RestartCoordinator();
        }
    }

    /// <summary>Which card the ray crosses, or -1. Plane test, no colliders.</summary>
    public int IndexUnderRay(Ray ray, out Vector3 hitPoint)
    {
        hitPoint = Vector3.zero;
        if (!_open)
            return -1;
        var plane = new Plane(-transform.forward, transform.position);
        float t;
        if (!plane.Raycast(ray, out t) || t > 8f)
            return -1;
        hitPoint = ray.GetPoint(t);
        Vector3 local = transform.InverseTransformPoint(hitPoint);
        if (Mathf.Abs(local.y) > CardH * 0.5f)
            return -1;
        for (int i = 0; i < Count; i++)
        {
            if (Mathf.Abs(local.x - CellX(i)) <= (CardW + GapM) * 0.5f)
                return i;
        }
        return -1;
    }

    public void SetHover(int index)
    {
        if (_hover == index)
            return;
        _hover = index;
        for (int i = 0; i < _labels.Count; i++)
            _labels[i].SetEmphasis(i == index);
    }

    /// <summary>Test seam: forget the choice so a test starts from scratch.</summary>
    public static void ResetForTests()
    {
        Chosen = Mode.None;
    }
}
