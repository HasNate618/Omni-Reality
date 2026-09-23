using UnityEngine;

/// <summary>
/// The A-button furniture menu: a world-space card per catalog entry, picked
/// with the pointer and the index trigger.
///
/// It is placed once, in front of the wearer where they are looking, and does
/// not follow the head -- a menu that chases you is impossible to point at.
/// </summary>
public sealed class FurnitureSelector : MonoBehaviour
{
    public const float DistanceM = 1.15f;
    public const float CardW = 0.30f;
    public const float CardH = 0.22f;
    /// <summary>Generous: labels like "coffee table" ran into their neighbours.</summary>
    public const float GapX = 0.13f;
    public const float GapY = 0.10f;
    public const int Columns = 3;
    public const float HeightDropM = 0.05f;
    /// <summary>Menu labels sit smaller than a name plate so they fit a card.</summary>
    public const float LabelScale = 0.72f;

    public static int Rows
    {
        get { return Mathf.CeilToInt(FurnitureCatalog.Count / (float)Columns); }
    }

    /// <summary>Card centre in menu-local space. One source of truth for
    /// layout and for hit testing, so the two can never drift apart.</summary>
    public static Vector2 CellCentre(int index)
    {
        int col = index % Columns;
        int row = index / Columns;
        float x = (col - ((Columns - 1) * 0.5f)) * (CardW + GapX);
        float y = (((Rows - 1) * 0.5f) - row) * (CardH + GapY);
        return new Vector2(x, y);
    }

    public bool IsOpen { get; private set; }
    public int HoverIndex { get; private set; }

    readonly System.Collections.Generic.List<GameObject> _cards =
        new System.Collections.Generic.List<GameObject>();
    readonly System.Collections.Generic.List<LayoutLabel> _labels =
        new System.Collections.Generic.List<LayoutLabel>();
    Transform _eye;
    LayoutLabel _title;

    void Awake()
    {
        HoverIndex = -1;
        var eye = GameObject.Find("CenterEyeAnchor");
        if (eye != null)
            _eye = eye.transform;
        gameObject.SetActive(false);
    }

    public void Toggle()
    {
        if (IsOpen)
            Close();
        else
            Open();
    }

    public void Open()
    {
        if (_eye == null)
        {
            var eye = GameObject.Find("CenterEyeAnchor");
            if (eye != null)
                _eye = eye.transform;
            if (_eye == null)
                return;
        }
        Build();
        Vector3 forward = _eye.forward;
        forward.y = 0f;
        if (forward.sqrMagnitude < 1e-6f)
            forward = Vector3.forward;
        forward.Normalize();
        transform.position = _eye.position + (forward * DistanceM) + (Vector3.down * HeightDropM);
        transform.rotation = Quaternion.LookRotation(forward, Vector3.up);
        gameObject.SetActive(true);
        IsOpen = true;
        HoverIndex = -1;
        Debug.Log("QUEST_LAYOUT selector open");
    }

    public void Close()
    {
        IsOpen = false;
        HoverIndex = -1;
        gameObject.SetActive(false);
        Debug.Log("QUEST_LAYOUT selector closed");
    }

    bool _built;

    void Build()
    {
        // Counting _cards cannot work: each entry contributes both a card and
        // a preview mesh, so the count never equalled the catalog size and
        // every Open() stacked a second label on the first -- which read as
        // the text being drawn twice.
        if (_built)
            return;
        _built = true;
        int n = FurnitureCatalog.Count;
        for (int i = 0; i < n; i++)
        {
            FurnitureCatalog.Entry e = FurnitureCatalog.At(i);
            Vector2 cell = CellCentre(i);
            float x = cell.x;
            float y = cell.y;

            var card = GameObject.CreatePrimitive(PrimitiveType.Quad);
            card.name = "Card_" + e.Kind;
            Collider col = card.GetComponent<Collider>();
            if (col != null)
                LayoutBox.DestroySafe(col);
            card.transform.SetParent(transform, false);
            card.transform.localPosition = new Vector3(x, y, 0f);
            card.transform.localScale = new Vector3(CardW, CardH, 1f);
            var rend = card.GetComponent<Renderer>();
            if (rend != null)
            {
                Color c = e.Tint;
                c.a = 0.35f;
                rend.sharedMaterial = LayoutBox.BuildMaterial(c);
                rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            }
            _cards.Add(card);

            // A small version of the real piece, so you pick by shape not word.
            Vector3 preview = PreviewSize(e.SizeM);
            GameObject mesh = FurnitureMesh.Build(e.Kind, preview, e.Tint, card.transform.parent);
            mesh.transform.localPosition = new Vector3(x, y - (CardH * 0.30f), -0.06f);
            mesh.transform.localScale = Vector3.one;
            _cards.Add(mesh);

            LayoutLabel label = LayoutLabel.Create(transform, e.Tint);
            label.transform.localPosition = new Vector3(x, y + (CardH * 0.52f), -0.01f);
            label.SetScale(LabelScale);
            label.SetText(e.Label);
            _labels.Add(label);
        }
        _title = LayoutLabel.Create(transform, Color.white);
        _title.transform.localPosition = new Vector3(
            0f, (Rows * 0.5f * (CardH + GapY)) + (CardH * 0.45f), -0.01f);
        _title.SetText("Point and pull the trigger to add");
    }

    /// <summary>Shrink a real piece to card size, keeping its proportions.</summary>
    public static Vector3 PreviewSize(Vector3 sizeM)
    {
        float longest = Mathf.Max(sizeM.x, Mathf.Max(sizeM.y, sizeM.z));
        if (longest <= 0f)
            return Vector3.one * 0.1f;
        return sizeM * (0.14f / longest);
    }

    /// <summary>
    /// Which card the ray crosses, or -1. Tested against the menu plane, so a
    /// near-miss above or below a card does not select the wrong one.
    /// </summary>
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
        // Half the gap is claimed either way, so there is no dead strip
        // between cards for the pointer to fall into.
        float padX = (CardW + GapX) * 0.5f;
        float padY = (CardH + GapY) * 0.5f;
        for (int i = 0; i < FurnitureCatalog.Count; i++)
        {
            Vector2 cell = CellCentre(i);
            if (Mathf.Abs(local.x - cell.x) <= padX && Mathf.Abs(local.y - cell.y) <= padY)
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
}
