using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// Quest-side handler for coordinator-authored <c>place_generated</c> ops.
/// Fetches GLB bytes from the laptop artifact HTTP server, imports the mesh
/// with glTFast (com.atteneder.gltfast), and pins it at the capture-time
/// surface (uniform scale to fit a 1 m sphere). If the import fails, a
/// placeholder cube preserves the harness behaviour.
/// </summary>
public static class GeneratedMeshPlacer
{
    public const int MaxGlbBytes = 25 * 1024 * 1024;
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
        CaptureGeometryCache cache)
    {
        if (op == null || op.Kind != "place_generated")
            return false;
        if (host == null || client == null)
            return false;
        host.StartCoroutine(FetchAndPlace(client, op, laptopIpv4, artifactPort, cache));
        return true;
    }

    static IEnumerator FetchAndPlace(
        CoordinatorClient client,
        ProtocolJson.SceneOpMsg op,
        string laptopIpv4,
        int artifactPort,
        CaptureGeometryCache cache)
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

        using (UnityWebRequest req = UnityWebRequest.Get(url))
        {
            req.downloadHandler = new DownloadHandlerBuffer();
            yield return req.SendWebRequest();
#if UNITY_2020_2_OR_NEWER
            if (req.result != UnityWebRequest.Result.Success)
#else
            if (req.isNetworkError || req.isHttpError)
#endif
            {
                client.EnqueueAck(op, "rejected", null, "invalid", null);
                yield break;
            }
            byte[] data = req.downloadHandler.data;
            if (data == null || data.Length < 12 || data.Length > MaxGlbBytes)
            {
                client.EnqueueAck(op, "rejected", null, "invalid", null);
                yield break;
            }
            if (!StartsWithGltf(data))
            {
                client.EnqueueAck(op, "rejected", null, "invalid", null);
                yield break;
            }
            GameObject root = null;
            string tmpPath = null;
            try
            {
                tmpPath = System.IO.Path.Combine(Application.temporaryCachePath, jobId + ".glb");
                System.IO.File.WriteAllBytes(tmpPath, data);
            }
            catch (System.Exception e)
            {
                Debug.LogWarning("GeneratedMeshPlacer: cache write failed (" + e.GetType().Name + ")");
                tmpPath = null;
            }
            if (tmpPath != null)
            {
                GameObject holder = new GameObject("Generated_" + jobId);
                GLTFast.GltfAsset asset = holder.AddComponent<GLTFast.GltfAsset>();
                System.Threading.Tasks.Task<bool> loadTask = asset.Load("file://" + tmpPath);
                while (!loadTask.IsCompleted)
                    yield return null;
                bool ok = loadTask.Status == System.Threading.Tasks.TaskStatus.RanToCompletion
                    && loadTask.Result
                    && holder.GetComponentsInChildren<Renderer>().Length > 0;
                if (ok)
                {
                    root = holder;
                }
                else
                {
                    Debug.LogWarning("GeneratedMeshPlacer: import failed, placeholder cube job=" + jobId);
                    Object.Destroy(holder);
                }
                try { System.IO.File.Delete(tmpPath); } catch (System.Exception) { }
            }
            if (root == null)
            {
                root = GameObject.CreatePrimitive(PrimitiveType.Cube);
                root.name = "Generated_" + jobId;
                root.transform.localScale = Vector3.one * 0.1f;
            }
            root.transform.position = result.Point;
            Vector3 n = result.Normal;
            if (n.sqrMagnitude < 1e-6f)
                n = Vector3.up;
            root.transform.rotation = Quaternion.FromToRotation(Vector3.up, n.normalized);
            FitInsideUnitSphere(root);
            string drawingId = client.NewDrawingId != null
                ? client.NewDrawingId()
                : SpatialRuntime.NewFrameId();
            try
            {
                if (root.GetComponent<OVRSpatialAnchor>() == null)
                    root.AddComponent<OVRSpatialAnchor>();
            }
            catch (System.Exception e)
            {
                Debug.LogWarning("GeneratedMeshPlacer: anchor unavailable (" + e.GetType().Name + ")");
            }
            client.EnqueueAck(op, "placed", drawingId, null, "surface");
            Debug.Log("GENERATED_PLACED op=" + op.OpId + " job=" + jobId + " drawing=" + drawingId);
        }
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

    static void FitInsideUnitSphere(GameObject root)
    {
        Renderer[] renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers == null || renderers.Length == 0)
            return;
        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++)
            bounds.Encapsulate(renderers[i].bounds);
        float m = Mathf.Max(bounds.size.x, bounds.size.y, bounds.size.z);
        if (m > 1f)
            root.transform.localScale *= 1f / m;
    }
}
