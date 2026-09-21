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

    /// <summary>
    /// Per-attempt timeout in seconds. Without one, an unreachable host can
    /// outlive the whole retry budget on top of the 5 x 15 s waits.
    /// </summary>
    const int FetchAttemptTimeoutSeconds = 4;

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

    /// <summary>
    /// The facing to plant a listed box with: the capture-time camera
    /// forward, flattened (spec §6.2). Capture-time and not the current head
    /// pose, because every other placement decision on this path -- the hit
    /// point, the range, the stale rules -- is capture-time geometry too.
    /// Null when the entry cannot supply a usable forward, so the store falls
    /// back instead of rotating the box by a zero vector.
    /// </summary>
    public static Vector3? CaptureFacing(CaptureGeometryCache.Entry entry)
    {
        if (entry == null)
            return null;
        Vector3 flat = new Vector3(entry.CameraPose.forward.x, 0f, entry.CameraPose.forward.z);
        if (flat.sqrMagnitude < 1e-6f)
            return null;
        return flat.normalized;
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
        if (store != null)
        {
            // The store owns the fit verdicts and has no client of its own, so
            // it gets this client's chip surface (spec §8.1) while this
            // placement is handled. A swap re-fits a mesh later, with no
            // placer running to report it.
            store.ChipSurface = client.ShowChipText;
        }
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

        string drawingId = client.NewDrawingId != null
            ? client.NewDrawingId()
            : SpatialRuntime.NewFrameId();
        Vector3? extent = op.HasExtentM ? (Vector3?)op.ExtentM : null;
        if (store == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        // §6.2: the planted box takes its facing from the capture-time view.
        // The hit normal cannot supply one -- on a floor it is gravity up.
        GameObject drawing = store.PlaceGenerated(
            result.Point, result.Normal, drawingId, extent,
            op.HasOffsetM ? op.OffsetM : 0f, CaptureFacing(entry));
        if (drawing == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        // The box is real at the listed size, so this is the honest moment to
        // claim the item. Recorded here rather than at op arrival so a refusal
        // (no_surface, too_close, stale) never puts a chip in the queue.
        // Reaching here guarantees HasExtentM: PlaceGenerated refuses a
        // placement with no size rather than inventing one.
        if (client.ItemQueue != null)
            client.ItemQueue.Record(drawingId, op.Name, op.ExtentM);
        // Plant and ACK the box BEFORE the artifact fetch. The coordinator
        // waits only ACK_TIMEOUT_S = 1.5 s before speaking SAY_UNCONFIRMED,
        // and the artifact 404s until the job is ready, so an ACK below the
        // retry loop would arrive up to ~75 s late and read as a failure
        // while the box already exists. placement_ack 'placed' needs only
        // drawing_id + pin, and the box is real without any mesh.
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
        Debug.Log("GENERATED_BOX_PLACED op=" + op.OpId + " job=" + jobId + " drawing=" + drawingId);

        byte[] data = null;
        for (int attempt = 0; attempt < MaxFetchAttempts && data == null; attempt++)
        {
            if (attempt > 0)
                yield return new WaitForSeconds(FetchRetrySeconds);
            using (UnityWebRequest req = UnityWebRequest.Get(url))
            {
                req.downloadHandler = new DownloadHandlerBuffer();
                req.timeout = FetchAttemptTimeoutSeconds;
                yield return req.SendWebRequest();
#if UNITY_2020_2_OR_NEWER
                bool ok = req.result == UnityWebRequest.Result.Success;
#else
                bool ok = !req.isNetworkError && !req.isHttpError;
#endif
                if (!ok)
                {
                    // Only a 404 (artifact not baked yet -- the designed
                    // case) or a transport error can improve by waiting. A
                    // 403, a wrong host, or any other definitive status is
                    // permanent, so stop rather than burn the retry budget.
                    if (IsTransientFailure(req))
                        continue;
                    break;
                }
                byte[] candidate = req.downloadHandler.data;
                if (candidate == null || candidate.Length < 12 || candidate.Length > MaxGlbBytes)
                    break;
                if (!StartsWithGltf(candidate))
                    break;
                data = candidate;
            }
        }

        if (data == null)
        {
            Debug.LogWarning("GeneratedMeshPlacer: no usable artifact; "
                + "box stays at listed size job=" + jobId);
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
        bool approximate;
        if (!store.FitMeshIntoBox(drawingId, mesh, out approximate))
        {
            Object.Destroy(mesh);
        }
        else if (approximate)
        {
            // The box stays and the wearer is told the mesh is approximate,
            // carried by the existing honesty chip (spec §8.1).
            client.ShowChipText(DrawingStore.ApproximateMeshText);
        }
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

    /// <summary>
    /// True when a failed attempt is plausibly transient: the artifact is
    /// not baked yet (HTTP 404) or the request never reached the server.
    /// </summary>
    static bool IsTransientFailure(UnityWebRequest req)
    {
        if (req.responseCode == 404)
            return true;
#if UNITY_2020_2_OR_NEWER
        return req.result == UnityWebRequest.Result.ConnectionError;
#else
        return req.isNetworkError;
#endif
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
