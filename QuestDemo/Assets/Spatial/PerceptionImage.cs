using System;
using UnityEngine;

/// <summary>Bounded CPU resize/encode; no camera, network or recorded media.</summary>
public static class PerceptionImage
{
    public const int MaxBytes = 65536;

    public static Vector2Int FitSize(int width, int height)
    {
        if (width < 1 || height < 1) return Vector2Int.zero;
        float scale = Mathf.Min(1f, 640f / Mathf.Max(width, height));
        return new Vector2Int(Mathf.Max(1, Mathf.RoundToInt(width * scale)),
                              Mathf.Max(1, Mathf.RoundToInt(height * scale)));
    }

    public static void SetSentSize(CaptureEnvelope env, Vector2Int size)
    {
        env.SentW = size.x;
        env.SentH = size.y;
        // The wire crop is a pixel-centre affine, not normalized UV scale.
        // p_original = (p_sent + .5) * scale - .5.
        env.CropSx = (float)env.ImageW / size.x;
        env.CropSy = (float)env.ImageH / size.y;
        env.CropTx = (env.CropSx - 1f) * 0.5f;
        env.CropTy = (env.CropSy - 1f) * 0.5f;
    }

    public static bool IsFresh(DateTime captured, DateTime now)
    {
        double ageMs = (now - captured).TotalMilliseconds;
        return ageMs >= 0 && ageMs <= 500;
    }

    public static bool IsDark(Color32[] pixels)
    {
        if (pixels == null || pixels.Length == 0) return true;
        long total = 0;
        foreach (var p in pixels) total += p.r + p.g + p.b;
        return total < (long)pixels.Length * 3 * 4;
    }

    public static byte[] Encode(Color32[] pixels, int width, int height, out Vector2Int size)
    {
        size = FitSize(width, height);
        if (size == Vector2Int.zero || pixels == null || pixels.Length != (long)width * height)
            return null;
        var scaled = new Color32[size.x * size.y];
        for (int y = 0; y < size.y; y++)
        {
            for (int x = 0; x < size.x; x++)
            {
                int r = 0, g = 0, b = 0, count = 0;
                for (int sy = y * height / size.y; sy < (y + 1) * height / size.y; sy++)
                {
                    for (int sx = x * width / size.x; sx < (x + 1) * width / size.x; sx++)
                    {
                        Color32 p = pixels[sy * width + sx];
                        r += p.r; g += p.g; b += p.b; count++;
                    }
                }
                scaled[y * size.x + x] = new Color32((byte)(r / count), (byte)(g / count), (byte)(b / count), 255);
            }
        }
        var texture = new Texture2D(size.x, size.y, TextureFormat.RGB24, false);
        try
        {
            texture.SetPixels32(scaled);
            byte[] jpeg = texture.EncodeToJPG(60);
            if (jpeg.Length > MaxBytes) jpeg = texture.EncodeToJPG(35);
            return jpeg.Length <= MaxBytes ? jpeg : null;
        }
        finally
        {
            if (Application.isPlaying) UnityEngine.Object.Destroy(texture);
            else UnityEngine.Object.DestroyImmediate(texture);
        }
    }
}
