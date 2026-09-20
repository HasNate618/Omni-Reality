using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// Quest-side handler for coordinator-authored <c>place_generated</c> ops.
/// The store plants the listed box first: that box IS the size claim and it
/// stays whether or not a mesh ever arrives. The GLB is then fetched from the
/// laptop artifact server, imported with glTFast (com.atteneder.gltfast), and
/// fitted uniformly inside the box. A missing or timed-out artifact leaves
/// the box at its listed size, never a smaller substitute.
/// </summary>
public static class GeneratedMeshPlacer
{
    public const int MaxGlbBytes = 25 * 1024 * 1024;

    /// <summary>
    /// Artifact fetch attempts. The coordinator only serves a file once its
    /// job is <c>ready</c>, and a sized job is planted before that, so the
    /// first 404s are expected and must be retried rather than rejected.
    /// </summary>
    public const int MaxFetchAttempts = 6;

    public const float FetchRetrySeconds = 15f;

    static readonly byte[] GltfMagic = { (byte)'g', (byte)'l', (byte)'T', (byte)'F' };

    public static string ArtifactUrl(string laptopIpv4, int artifactPort, string jobId)
    {
        if (string.IsNullOrEmpty(laptopIpv4) || string.IsNullOrEmpty(jobId))
            return null;
        if (jobId.Length != 26)
            return null;
        if (jobId.IndexOf('/') >= 0 || jobId.IndexOf('\\') >= 0 || jobId.IndexOf('.') >= 0)
            return null;
        return "http://" + laptopIpv4 + ":" + artifactPort + "/artifacts/" + jobId + ".glb";
    }

    public static bool TryHandle(
        CoordinatorClient client,
        MonoBehaviour host,
        ProtocolJson.SceneOpMsg op,
        string laptopIpv4,
        int artifactPort,
        CaptureGeometryCache cache,
        DrawingStore store)
    {
        if (op == null || op.Kind != "place_generated")
            return false;
        if (host == null || client == null)
            return false;
        host.StartCoroutine(FetchAndPlace(client, op, laptopIpv4, artifactPort, cache, store));
        return true;
    }

    static IEnumerator FetchAndPlace(
        CoordinatorClient client,
        ProtocolJson.SceneOpMsg op,
        string laptopIpv4,
        int artifactPort,
        CaptureGeometryCache cache,
        DrawingStore store)
    {
        string jobId = op.JobId;
        if (string.IsNullOrEmpty(jobId))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        string url = ArtifactUrl(laptopIpv4, artifactPort, jobId);
        if (url == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        if (string.IsNullOrEmpty(op.TargetFrameId))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        CaptureGeometryCache.Entry entry = null;
        bool hasEntry = cache != null && cache.TryGet(op.TargetFrameId, out entry) && entry != null;
        if (!hasEntry || !entry.HasHit)
        {
            client.EnqueueAck(op, "rejected", null, "no_surface", null);
            yield break;
        }
        Vector3? delayed = client.DelayedHit != null
            ? client.DelayedHit(PlacementResolver.CachedRay(entry))
            : (Vector3?)null;
        PlacementResult result =
            PlacementResolver.TryPlaceFromCapture(entry, "placed", delayed, true);
        if (!result.ShouldPin)
        {
            if (result.Outcome == PlacementOutcome.TooClose)
                client.EnqueueAck(op, "rejected", null, "too_close", null);
            else if (result.Outcome == PlacementOutcome.Stale)
                client.EnqueueAck(op, "stale", null, "superseded", null);
            else
                client.EnqueueAck(op, "rejected", null, "no_surface", null);
            yield break;
        }

        byte[] data = null;
        for (int attempt = 0; attempt < MaxFetchAttempts && data == null; attempt++)
        {
            if (attempt > 0)
                yield return new WaitForSeconds(FetchRetrySeconds);
            using (UnityWebRequest req = UnityWebRequest.Get(url))
            {
                req.downloadHandler = new DownloadHandlerBuffer();
                yield return req.SendWebRequest();
#if UNITY_2020_2_OR_NEWER
                bool ok = req.result == UnityWebRequest.Result.Success;
#else
                bool ok = !req.isNetworkError && !req.isHttpError;
#endif
                if (!ok)
                    continue;
                byte[] candidate = req.downloadHandler.data;
                if (candidate == null || candidate.Length < 12 || candidate.Length > MaxGlbBytes)
                    continue;
                if (!StartsWithGltf(candidate))
                    continue;
                data = candidate;
            }
        }

        string drawingId = client.NewDrawingId != null
            ? client.NewDrawingId()
            : SpatialRuntime.NewFrameId();
        Vector3? extent = op.HasExtentM ? (Vector3?)op.ExtentM : null;
        if (store == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        GameObject drawing = store.PlaceGenerated(
            result.Point, result.Normal, drawingId, extent,
            op.HasOffsetM ? op.OffsetM : 0f);
        if (drawing == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        // The box is placed and real whether or not the mesh ever arrives.
        // ACK now: placement_ack 'placed' needs drawing_id + pin only.
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
        Debug.Log("GENERATED_BOX_PLACED op=" + op.OpId + " job=" + jobId + " drawing=" + drawingId);

        if (data == null)
        {
            Debug.LogWarning("GeneratedMeshPlacer: no artifact after "
                + MaxFetchAttempts + " attempts; box stays at listed size job=" + jobId);
            client.NotifyMeshMissing(drawingId);
            yield break;
        }

        PendingImport pending = BeginImport(data, jobId);
        if (pending == null)
        {
            client.NotifyMeshMissing(drawingId);
            yield break;
        }
        while (!pending.Task.IsCompleted)
            yield return null;
        GameObject mesh = FinishImport(pending, jobId);
        if (mesh == null)
        {
            client.NotifyMeshMissing(drawingId);
            yield break;
        }
        if (!store.FitMeshIntoBox(drawingId, mesh))
            Object.Destroy(mesh);
    }

    sealed class PendingImport
    {
        public GameObject Holder;
        public System.Threading.Tasks.Task<bool> Task;
        public string TmpPath;
    }

    /// <summary>Start the import. Caller yields on PendingImport.Task.</summary>
    static PendingImport BeginImport(byte[] data, string jobId)
    {
        string tmpPath;
        try
        {
            tmpPath = System.IO.Path.Combine(Application.temporaryCachePath, jobId + ".glb");
            System.IO.File.WriteAllBytes(tmpPath, data);
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("GeneratedMeshPlacer: cache write failed (" + e.GetType().Name + ")");
            return null;
        }
        GameObject holder = new GameObject("Generated_" + jobId);
        GLTFast.GltfAsset asset = holder.AddComponent<GLTFast.GltfAsset>();
        return new PendingImport
        {
            Holder = holder,
            Task = asset.Load("file://" + tmpPath),
            TmpPath = tmpPath,
        };
    }

    /// <summary>Resolve a finished import. Null when it did not produce a mesh.</summary>
    static GameObject FinishImport(PendingImport pending, string jobId)
    {
        if (pending == null)
            return null;
        try { System.IO.File.Delete(pending.TmpPath); } catch (System.Exception) { }
        bool ok = pending.Task.Status == System.Threading.Tasks.TaskStatus.RanToCompletion
            && pending.Task.Result
            && pending.Holder != null
            && pending.Holder.GetComponentsInChildren<Renderer>().Length > 0;
        if (ok)
            return pending.Holder;
        Debug.LogWarning("GeneratedMeshPlacer: import failed job=" + jobId);
        if (pending.Holder != null)
            Object.Destroy(pending.Holder);
        return null;
    }

    static bool StartsWithGltf(byte[] data)
    {
        if (data.Length < GltfMagic.Length)
            return false;
        for (int i = 0; i < GltfMagic.Length; i++)
        {
            if (data[i] != GltfMagic[i])
                return false;
        }
        return true;
    }
}
