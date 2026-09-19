using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Capture-time geometry retained per frame_id with a 10 s TTL. Delayed work
/// (later slices) must read the cached capture-time pose/ray/hit, never the
/// current head pose. Every valid capture is stored — including the identity
/// crop and an explicit optional-hit state on miss/too-close frames — so
/// <see cref="Available"/> (cache existence) drives
/// <c>capture_geometry_available</c>, not success-hit only. Misses keep a null
/// world_hint and unchanged honesty; the entry records that geometry was
/// evaluated and found absent/too close.
/// </summary>
public sealed class CaptureGeometryCache
{
    public const float TtlSeconds = 10f;

    public sealed class Entry
    {
        public Pose CameraPose;
        public Vector2 FocalLength;
        public Vector2 PrincipalPoint;
        public Vector2Int ImageResolution;
        public float CropSx = 1f;
        public float CropSy = 1f;
        public float CropTx;
        public float CropTy;
        public Vector3 RayOrigin;
        public Vector3 RayDirection;
        public bool HasHit;
        public Vector3 HitPoint;
        public bool HasNormal;
        public Vector3 HitNormal;
        public long TUnixNs;
        public float StoredRealtime;
    }

    readonly Dictionary<string, Entry> _entries = new Dictionary<string, Entry>();

    public void Store(string frameId, Entry entry)
    {
        if (string.IsNullOrEmpty(frameId) || entry == null)
            return;
        Prune();
        entry.StoredRealtime = Time.realtimeSinceStartup;
        _entries[frameId] = entry;
    }

    public bool Available(string frameId)
    {
        if (string.IsNullOrEmpty(frameId))
            return false;
        Prune();
        return _entries.ContainsKey(frameId);
    }

    public bool TryGet(string frameId, out Entry entry)
    {
        Prune();
        if (string.IsNullOrEmpty(frameId))
        {
            entry = null;
            return false;
        }
        return _entries.TryGetValue(frameId, out entry);
    }

    public int Count
    {
        get
        {
            Prune();
            return _entries.Count;
        }
    }

    public void Prune()
    {
        float now = Time.realtimeSinceStartup;
        List<string> expired = null;
        foreach (var kv in _entries)
        {
            if (now - kv.Value.StoredRealtime > TtlSeconds)
            {
                if (expired == null)
                    expired = new List<string>();
                expired.Add(kv.Key);
            }
        }
        if (expired == null)
            return;
        foreach (var key in expired)
            _entries.Remove(key);
    }
}
