using UnityEngine;

/// <summary>
/// Quest handlers for label, ghost, and connect scene ops (capture_hint /
/// pointing targets only until image_point unprojection lands).
/// </summary>
public static class GhostLabelConnect
{
    public const float LabelOffsetM = 0.08f;
    public const float ConnectMaxDistanceM = 4f;
    public const float GhostSizeM = 0.12f;

    public static void TryHandle(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        if (client == null || op == null)
            return;
        if (op.Kind == "label")
            HandleLabel(client, op);
        else if (op.Kind == "ghost")
            HandleGhost(client, op);
        else if (op.Kind == "connect")
            HandleConnect(client, op);
    }

    static void HandleLabel(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        if (string.IsNullOrEmpty(op.Text))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        PlacementResult result;
        if (!client.TryResolveTargetOp(op, out result))
            return;
        if (!result.ShouldPin)
        {
            client.EnqueueAckForResult(op, result);
            return;
        }
        if (client.Store != null && DrawingStore.NeedsEviction(client.Store.Count) && string.IsNullOrEmpty(op.DrawingId))
        {
            client.ShowChipText("Too many drawings on screen.");
            client.EnqueueAck(op, "rejected", null, "clutter", null);
            return;
        }
        string drawingId = !string.IsNullOrEmpty(op.DrawingId)
            ? op.DrawingId
            : (client.NewDrawingId != null ? client.NewDrawingId() : SpatialRuntime.NewFrameId());
        Vector3 pos = result.Point + result.Normal.normalized * LabelOffsetM;
        GameObject go = client.Store != null
            ? client.Store.PlaceLabel(pos, result.Normal, drawingId, op.Text)
            : null;
        if (go == null)
        {
            client.EnqueueAck(op, "rejected", null, "no_surface", null);
            return;
        }
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
    }

    static void HandleGhost(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        if (op.MotionKind != "rotate" && op.MotionKind != "slide")
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        PlacementResult result;
        if (!client.TryResolveTargetOp(op, out result))
            return;
        if (!result.ShouldPin)
        {
            client.EnqueueAckForResult(op, result);
            return;
        }
        if (client.Store != null && DrawingStore.NeedsEviction(client.Store.Count) && string.IsNullOrEmpty(op.DrawingId))
        {
            client.ShowChipText("Too many drawings on screen.");
            client.EnqueueAck(op, "rejected", null, "clutter", null);
            return;
        }
        string drawingId = !string.IsNullOrEmpty(op.DrawingId)
            ? op.DrawingId
            : (client.NewDrawingId != null ? client.NewDrawingId() : SpatialRuntime.NewFrameId());
        GameObject go = client.Store != null
            ? client.Store.PlaceGhost(result.Point, result.Normal, drawingId, op)
            : null;
        if (go == null)
        {
            client.EnqueueAck(op, "rejected", null, "no_surface", null);
            return;
        }
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
    }

    static void HandleConnect(CoordinatorClient client, ProtocolJson.SceneOpMsg op)
    {
        if (string.IsNullOrEmpty(op.FromTargetFrameId) || string.IsNullOrEmpty(op.ToTargetFrameId))
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        var fromOp = CloneWithFrame(op, op.FromTargetFrameId);
        var toOp = CloneWithFrame(op, op.ToTargetFrameId);
        PlacementResult fromResult;
        PlacementResult toResult;
        if (!client.TryResolveTargetOp(fromOp, out fromResult))
            return;
        if (!client.TryResolveTargetOp(toOp, out toResult))
            return;
        if (!fromResult.ShouldPin || !toResult.ShouldPin)
        {
            client.EnqueueAckForResult(op, !fromResult.ShouldPin ? fromResult : toResult);
            return;
        }
        float dist = Vector3.Distance(fromResult.Point, toResult.Point);
        if (dist > ConnectMaxDistanceM)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            return;
        }
        if (client.Store != null && DrawingStore.NeedsEviction(client.Store.Count) && string.IsNullOrEmpty(op.DrawingId))
        {
            client.ShowChipText("Too many drawings on screen.");
            client.EnqueueAck(op, "rejected", null, "clutter", null);
            return;
        }
        string drawingId = !string.IsNullOrEmpty(op.DrawingId)
            ? op.DrawingId
            : (client.NewDrawingId != null ? client.NewDrawingId() : SpatialRuntime.NewFrameId());
        GameObject go = client.Store != null
            ? client.Store.PlaceConnect(fromResult.Point, toResult.Point, drawingId)
            : null;
        if (go == null)
        {
            client.EnqueueAck(op, "rejected", null, "no_surface", null);
            return;
        }
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
    }

    static ProtocolJson.SceneOpMsg CloneWithFrame(ProtocolJson.SceneOpMsg op, string frameId)
    {
        return new ProtocolJson.SceneOpMsg
        {
            OpId = op.OpId,
            HasTurnId = op.HasTurnId,
            TurnId = op.TurnId,
            HasStageEpoch = op.HasStageEpoch,
            StageEpoch = op.StageEpoch,
            Kind = op.Kind,
            TargetFrameId = frameId,
        };
    }
}
