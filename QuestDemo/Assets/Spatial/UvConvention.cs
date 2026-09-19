using UnityEngine;

public static class UvConvention
{
    public static Vector2 SpecUvToPcaViewport(float u, float v)
    {
        return new Vector2(u, 1f - v);
    }

    public static Vector2 SpecUvToPixelCenter(float u, float v, float sentW, float sentH)
    {
        return new Vector2(u * sentW - 0.5f, v * sentH - 0.5f);
    }
}
