using UnityEngine;

/// <summary>
/// Furniture built from primitives, sized to fill its stated dimensions.
///
/// There is no mesh generator and no asset library in this project, and a
/// downloaded model would arrive at an arbitrary scale -- which is the one
/// thing this demo cannot afford, because the footprint is the answer. Built
/// this way the mesh *is* the bounding volume: every piece occupies exactly
/// w x d x h, so what the wearer sees is what the fit check measures.
///
/// Local frame: y = 0 is the floor, x spans [-w/2, w/2], z spans [-d/2, d/2].
/// </summary>
public static class FurnitureMesh
{
    /// <summary>Solid enough to read as furniture, clearly still virtual.</summary>
    public const float BodyAlpha = 0.78f;

    public static GameObject Build(string kind, Vector3 sizeM, Color tint, Transform parent)
    {
        var root = new GameObject("Furniture_" + (kind ?? "piece"));
        root.transform.SetParent(parent, false);
        // Parent's origin is the box centre; drop to the floor plane.
        root.transform.localPosition = new Vector3(0f, -sizeM.y * 0.5f, 0f);
        root.transform.localRotation = Quaternion.identity;

        Color body = tint;
        body.a = BodyAlpha;
        Material mat = LayoutBox.BuildMaterial(body);

        switch (kind)
        {
            case FurnitureCatalog.CoffeeTable:
            case FurnitureCatalog.SideTable:
                BuildTable(root.transform, sizeM, mat);
                break;
            case FurnitureCatalog.Bookshelf:
                BuildBookshelf(root.transform, sizeM, mat);
                break;
            case FurnitureCatalog.FloorLamp:
                BuildFloorLamp(root.transform, sizeM, mat);
                break;
            case FurnitureCatalog.Armchair:
            case FurnitureCatalog.Sofa:
            default:
                BuildSeat(root.transform, sizeM, mat);
                break;
        }
        return root;
    }

    // ---- shapes -----------------------------------------------------------

    /// <summary>Sofa / armchair: legs, seat base, cushions, back, two arms.</summary>
    static void BuildSeat(Transform root, Vector3 s, Material mat)
    {
        float w = s.x, h = s.y, d = s.z;
        float legH = h * 0.13f;
        float seatTop = h * 0.52f;
        float armW = Mathf.Min(w * 0.11f, 0.16f);
        float backD = Mathf.Min(d * 0.20f, 0.18f);

        float legS = Mathf.Min(w, d) * 0.07f;
        float lx = (w * 0.5f) - (legS * 0.9f);
        float lz = (d * 0.5f) - (legS * 0.9f);
        Box(root, "Leg0", new Vector3(-lx, legH * 0.5f, -lz), new Vector3(legS, legH, legS), mat);
        Box(root, "Leg1", new Vector3(lx, legH * 0.5f, -lz), new Vector3(legS, legH, legS), mat);
        Box(root, "Leg2", new Vector3(-lx, legH * 0.5f, lz), new Vector3(legS, legH, legS), mat);
        Box(root, "Leg3", new Vector3(lx, legH * 0.5f, lz), new Vector3(legS, legH, legS), mat);

        // Seat base between the arms, stopping short of the backrest.
        float seatW = w - (armW * 2f);
        float seatD = d - backD;
        Box(root, "Seat",
            new Vector3(0f, (legH + seatTop) * 0.5f, -backD * 0.5f),
            new Vector3(seatW, seatTop - legH, seatD), mat);

        // Backrest spans the full width and reaches the top of the piece.
        Box(root, "Back",
            new Vector3(0f, (legH + h) * 0.5f, (d - backD) * 0.5f),
            new Vector3(w, h - legH, backD), mat);

        Box(root, "ArmL",
            new Vector3(-(w - armW) * 0.5f, (legH + h * 0.74f) * 0.5f, -backD * 0.5f),
            new Vector3(armW, (h * 0.74f) - legH, seatD), mat);
        Box(root, "ArmR",
            new Vector3((w - armW) * 0.5f, (legH + h * 0.74f) * 0.5f, -backD * 0.5f),
            new Vector3(armW, (h * 0.74f) - legH, seatD), mat);
    }

    /// <summary>Table: a top on four inset legs.</summary>
    static void BuildTable(Transform root, Vector3 s, Material mat)
    {
        float w = s.x, h = s.y, d = s.z;
        float topT = Mathf.Min(h * 0.14f, 0.06f);
        Box(root, "Top", new Vector3(0f, h - (topT * 0.5f), 0f), new Vector3(w, topT, d), mat);

        float legS = Mathf.Min(w, d) * 0.09f;
        float legH = h - topT;
        float lx = (w * 0.5f) - (legS * 0.85f);
        float lz = (d * 0.5f) - (legS * 0.85f);
        Box(root, "Leg0", new Vector3(-lx, legH * 0.5f, -lz), new Vector3(legS, legH, legS), mat);
        Box(root, "Leg1", new Vector3(lx, legH * 0.5f, -lz), new Vector3(legS, legH, legS), mat);
        Box(root, "Leg2", new Vector3(-lx, legH * 0.5f, lz), new Vector3(legS, legH, legS), mat);
        Box(root, "Leg3", new Vector3(lx, legH * 0.5f, lz), new Vector3(legS, legH, legS), mat);
    }

    /// <summary>Bookshelf: back panel, two uprights, evenly spaced shelves.</summary>
    static void BuildBookshelf(Transform root, Vector3 s, Material mat)
    {
        float w = s.x, h = s.y, d = s.z;
        float panel = Mathf.Min(d * 0.12f, 0.03f);
        float side = Mathf.Min(w * 0.06f, 0.04f);

        Box(root, "Back", new Vector3(0f, h * 0.5f, (d - panel) * 0.5f),
            new Vector3(w, h, panel), mat);
        Box(root, "SideL", new Vector3(-(w - side) * 0.5f, h * 0.5f, 0f),
            new Vector3(side, h, d), mat);
        Box(root, "SideR", new Vector3((w - side) * 0.5f, h * 0.5f, 0f),
            new Vector3(side, h, d), mat);

        const int shelves = 4;
        float inner = w - (side * 2f);
        for (int i = 0; i < shelves; i++)
        {
            // Includes the floor of the unit at i = 0 and the top at the end.
            float y = (h - panel) * (i / (float)(shelves - 1));
            y = Mathf.Clamp(y, panel * 0.5f, h - (panel * 0.5f));
            Box(root, "Shelf" + i, new Vector3(0f, y, -panel * 0.5f),
                new Vector3(inner, panel, d - panel), mat);
        }
    }

    /// <summary>Floor lamp: weighted base, slim pole, drum shade.</summary>
    static void BuildFloorLamp(Transform root, Vector3 s, Material mat)
    {
        float w = s.x, h = s.y;
        float baseH = Mathf.Min(h * 0.03f, 0.04f);
        Cylinder(root, "Base", new Vector3(0f, baseH * 0.5f, 0f), w * 0.72f, baseH, mat);

        float shadeH = h * 0.22f;
        float poleH = h - shadeH;
        Cylinder(root, "Pole", new Vector3(0f, poleH * 0.5f, 0f),
                 Mathf.Min(w * 0.09f, 0.05f), poleH, mat);
        Cylinder(root, "Shade", new Vector3(0f, h - (shadeH * 0.5f), 0f), w, shadeH, mat);
    }

    // ---- primitives -------------------------------------------------------

    static void Box(Transform parent, string name, Vector3 centre, Vector3 size, Material mat)
    {
        if (size.x <= 0f || size.y <= 0f || size.z <= 0f)
            return;
        GameObject go = GameObject.CreatePrimitive(PrimitiveType.Cube);
        Strip(go, name, parent, mat);
        go.transform.localPosition = centre;
        go.transform.localScale = size;
    }

    static void Cylinder(Transform parent, string name, Vector3 centre,
                         float diameter, float height, Material mat)
    {
        if (diameter <= 0f || height <= 0f)
            return;
        GameObject go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        Strip(go, name, parent, mat);
        go.transform.localPosition = centre;
        // Unity's cylinder mesh is 1 across and 2 tall.
        go.transform.localScale = new Vector3(diameter, height * 0.5f, diameter);
    }

    static void Strip(GameObject go, string name, Transform parent, Material mat)
    {
        go.name = name;
        Collider col = go.GetComponent<Collider>();
        if (col != null)
            LayoutBox.DestroySafe(col);
        go.transform.SetParent(parent, false);
        var rend = go.GetComponent<Renderer>();
        if (rend != null)
        {
            rend.sharedMaterial = mat;
            rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            rend.receiveShadows = false;
        }
    }
}
