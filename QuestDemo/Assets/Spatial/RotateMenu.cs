using UnityEngine;

/// <summary>
/// Double-tap the trigger on a piece to turn it: a small row of angle cards
/// floating just above whatever you double-tapped.
///
/// It anchors to the piece rather than the head so it reads as belonging to
/// that chair, and it closes as soon as a card is picked -- a rotate menu you
/// have to dismiss is slower than just rotating.
/// </summary>
public sealed class RotateMenu : MonoBehaviour
{
    public const float CardW = 0.16f;
    public const float CardH = 0.14f;
    public const float GapM = 0.06f;
    /// <summary>Clearance above the piece so the menu never sits inside it.</summary>
    public const float LiftM = 0.28f;

    /// <summary>
    /// Left-hand turns first, so the row reads like a dial. The trailing 0 is
    /// the "done" card: picking it closes the menu instead of turning.
    /// </summary>
    public static readonly float[] Steps = { -90f, -45f, 45f, 90f, 0f };

    public static int Count { get { return Steps.Length; } }

    public bool IsOpen { get; private set; }
    public int HoverIndex { get; private set; }
    public LayoutBox Target { get; private set; }

    readonly System.Collections.Generic.List<LayoutLabel> _labels =
        new System.Collections.Generic.List<LayoutLabel>();
    readonly System.Collections.Generic.List<GameObject> _cards =
        new System.Collections.Generic.List<GameObject>();

    void Awake()
    {
        HoverIndex = -1;
        gameObject.SetActive(false);
    }

    public static string StepLabel(int index)
    {
        float deg = Steps[Mathf.Clamp(index, 0, Steps.Length - 1)];
        if (Mathf.Approximately(deg, 0f))
            return "done";
        return deg < 0f
            ? "< " + Mathf.RoundToInt(-deg) + "°"
            : Mathf.RoundToInt(deg) + "° >";
    }

    /// <summary>Card centre in menu-local space; shared by layout and hit test.</summary>
    public static float CellX(int index)
    {
        int n = Count;
        float total = (n * CardW) + ((n - 1) * GapM);
        return -total * 0.5f + (CardW * 0.5f) + (index * (CardW + GapM));
    }

    Transform _eye;

    public void Open(LayoutBox target)
    {
        if (target == null)
            return;
        Build();
        Target = target;
        gameObject.SetActive(true);
        IsOpen = true;
        HoverIndex = -1;
        Reposition();
        Debug.Log("QUEST_LAYOUT rotate menu on " + target.ItemLabel);
    }

    /// <summary>
    /// Keep the menu above its piece and facing the wearer. Called every frame
    /// while open, so turning the piece does not leave the menu behind it.
    /// </summary>
    public void Reposition()
    {
        if (Target == null)
        {
            Close();
            return;
        }
        if (_eye == null)
        {
            var eye = GameObject.Find("CenterEyeAnchor");
            if (eye != null)
                _eye = eye.transform;
        }
        Vector3 top = Target.transform.position
                      + (Vector3.up * ((Target.SizeM.y * 0.5f) + LiftM));
        transform.position = top;
        if (_eye == null)
            return;
        Vector3 toEye = _eye.position - top;
        toEye.y = 0f;
        if (toEye.sqrMagnitude > 1e-6f)
            transform.rotation = Quaternion.LookRotation(-toEye.normalized, Vector3.up);
    }

    public void Close()
    {
        IsOpen = false;
        HoverIndex = -1;
        Target = null;
        gameObject.SetActive(false);
    }

    void Build()
    {
        if (_cards.Count == Count)
            return;
        for (int i = 0; i < Count; i++)
        {
            float x = CellX(i);
            var card = GameObject.CreatePrimitive(PrimitiveType.Quad);
            card.name = "Rotate_" + i;
            Collider col = card.GetComponent<Collider>();
            if (col != null)
                LayoutBox.DestroySafe(col);
            card.transform.SetParent(transform, false);
            card.transform.localPosition = new Vector3(x, 0f, 0f);
            card.transform.localScale = new Vector3(CardW, CardH, 1f);
            var rend = card.GetComponent<Renderer>();
            if (rend != null)
            {
                rend.sharedMaterial = LayoutBox.BuildMaterial(new Color(0.24f, 0.86f, 1f, 0.34f));
                rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            }
            _cards.Add(card);

            LayoutLabel label = LayoutLabel.Create(transform, Color.white);
            label.transform.localPosition = new Vector3(x, 0f, -0.01f);
            label.SetText(StepLabel(i));
            label.SetScale(0.8f);
            _labels.Add(label);
        }
    }

    /// <summary>Which card the ray crosses, or -1.</summary>
    public int IndexUnderRay(Ray ray, out Vector3 hitPoint)
    {
        hitPoint = Vector3.zero;
        if (!IsOpen)
            return -1;
        Plane plane = new Plane(-transform.forward, transform.position);
        float t;
        if (!plane.Raycast(ray, out t) || t > 8f)
            return -1;
        hitPoint = ray.GetPoint(t);
        Vector3 local = transform.InverseTransformPoint(hitPoint);
        if (Mathf.Abs(local.y) > CardH * 0.5f)
            return -1;
        for (int i = 0; i < Count; i++)
        {
            if (Mathf.Abs(local.x - CellX(i)) <= CardW * 0.5f)
                return i;
        }
        return -1;
    }

    public void SetHover(int index)
    {
        if (HoverIndex == index)
            return;
        HoverIndex = index;
        for (int i = 0; i < _labels.Count; i++)
            _labels[i].SetEmphasis(i == index);
    }

    /// <summary>
    /// Turn the target by the picked step. False means the menu is finished --
    /// "done" was picked, or the piece is gone -- and the caller closes it.
    /// Staying open on a real turn lets the wearer nudge round repeatedly.
    /// </summary>
    public bool Apply(int index)
    {
        if (Target == null || index < 0 || index >= Steps.Length)
            return false;
        if (Mathf.Approximately(Steps[index], 0f))
            return false;
        Target.transform.Rotate(0f, Steps[index], 0f, Space.World);
        Debug.Log("QUEST_LAYOUT rotated " + Target.ItemLabel + " by " + Steps[index]);
        Reposition();
        return true;
    }
}
