using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

/// <summary>
/// Task 7 Quest → laptop coordinator link over a single
/// <see cref="ClientWebSocket"/> to ws://{laptop_ipv4}:8765 (PlayerPrefs
/// <c>laptop_ipv4</c>, never a secret). One socket only: a background
/// transport moves bytes while all Unity work (placement, chips, timers)
/// runs on the main thread via <see cref="Tick"/> (called from
/// <see cref="SpatialRuntime"/> after PCA starts playing).
///
/// Wire contract: hello on connect (session_id null until hello_ok assigns
/// a new one), ping every 5 s, reconnect attempt every 2 s. The outbound
/// queue is priority-ordered — cancel (0) ahead of ack (2) ahead of ping
/// (8) ahead of frame (9) — so control never waits on frame encoding
/// (slice 2 frames carry the envelope only, no jpeg_b64). Inbound frames
/// are queued by the receive loop and drained on the main thread.
///
/// Laptop scene_op mark handling: resolve via the Task 4 capture cache and
/// the Task 5 resolver on the SAME capture-time ray, then ACK
/// placed/rejected/stale synchronously in the pump (same frame as
/// render-or-reject, inside the 500 ms budget). Op ids dedupe (repeats
/// ignored); stage-epoch mismatch fences as superseded; missing turn_id or
/// turn_id 0 rejects as invalid; omitted mark motion defaults to a 1.2 s
/// pulse. Non-mark kinds are ignored (no other scene operations in
/// slice 2) and audio_chunk is not implemented (slice 3).
///
/// Offline honesty: on socket down the exact chip "Laptop not connected."
/// shows; existing rings are kept (never cleared) and a reconnect mints a
/// new session_id via hello_ok without clearing drawings.
/// </summary>
public class CoordinatorClient : MonoBehaviour
{
    public const string LaptopIpv4PrefKey = "laptop_ipv4";
    public const float PingIntervalS = 5f;
    public const float ReconnectIntervalS = 2f;
    public const string OfflineChipText = "Laptop not connected.";

    /// <summary>Send priorities (spec order): cancel ahead of ack ahead of frames.</summary>
    public const int PriorityCancel = 0;
    public const int PriorityUtteranceEnd = 9;
    public const int PriorityAck = 2;
    public const int PriorityPing = 8;
    public const int PriorityAudioChunk = 9;
    public const int PriorityFrame = 9;

    /// <summary>Hello shares the top lane (sent once, queue empty at connect).</summary>
    public const int PriorityHello = 0;

    /// <summary>
    /// Priority outbox seam: lowest number dequeues first. Shared between
    /// the main thread (enqueue/pump) and the background send loop.
    /// </summary>
    internal sealed class SendQueue
    {
        readonly SortedDictionary<int, Queue<string>> _queues =
            new SortedDictionary<int, Queue<string>>();
        readonly object _gate = new object();

        public void Enqueue(int priority, string json)
        {
            if (json == null)
                return;
            lock (_gate)
            {
                Queue<string> q;
                if (!_queues.TryGetValue(priority, out q))
                {
                    q = new Queue<string>();
                    _queues[priority] = q;
                }
                q.Enqueue(json);
            }
        }

        public bool TryDequeue(out string json)
        {
            lock (_gate)
            {
                foreach (var kv in _queues)
                {
                    if (kv.Value.Count > 0)
                    {
                        json = kv.Value.Dequeue();
                        return true;
                    }
                }
            }
            json = null;
            return false;
        }

        public void Clear()
        {
            lock (_gate) _queues.Clear();
        }

        public int Count
        {
            get
            {
                lock (_gate)
                {
                    int n = 0;
                    foreach (var kv in _queues)
                        n += kv.Value.Count;
                    return n;
                }
            }
        }
    }

    // Collaborators wired by SpatialRuntime (main thread only).
    internal CaptureGeometryCache Cache;
    internal DrawingStore Store;
    internal HonestyChip Chip;
    internal Transform CenterEye;
    internal Func<int> GetStageEpoch = () => 1;
    internal Func<Ray, Vector3?> DelayedHit;
    internal Func<string> NewDrawingId;
    internal SpeakCloudPlayer SpeakPlayer;
    internal VoiceCaption Caption;
    internal TrackingMaskOverlay TrackingOverlay;
    internal PerceptionCapture TrackCapture;
    internal bool TrackStreaming { get { return _trackStreaming; } }

    readonly ConcurrentQueue<string> _inbound = new ConcurrentQueue<string>();
    readonly ConcurrentQueue<string> _errors = new ConcurrentQueue<string>();
    readonly SendQueue _outbox = new SendQueue();
    readonly HashSet<string> _seenOpIds = new HashSet<string>();

    string _ipv4;
    bool _beginRequested;
    volatile ClientWebSocket _socket;
    volatile bool _socketDown;
    volatile bool _connecting;
    CancellationTokenSource _cts;
    string _sessionId;
    int _artifactPort = 8766;
    float _lastPingAt;
    float _lastConnectAttemptAt = -1000f;
    bool _wasOpen;
    bool _offlineShown;
    bool _attemptFailed;
    string _pendingReplyId;
    float _replyStartedAt;
    bool _trackStreaming;
    int _trackingGeneration = -1;
    float _lastTrackFrameAt = -1000f;
    internal const float TrackFrameIntervalS = 0.2f;
    public bool PerceptionEnabled { get; private set; }
    public string SessionId { get { return _sessionId; } }
    public bool AwaitingReply { get { return _pendingReplyId != null; } }

    [Serializable]
    class HelloOptions { public bool perception_qa; }

    /// <summary>Queue a hello + start supervision. No socket work happens here.</summary>
    internal void Begin(string ipv4)
    {
        _ipv4 = ipv4;
        _beginRequested = true;
    }

    internal bool ConnectRequested
    {
        get { return _beginRequested; }
    }

    /// <summary>True while the socket is open (main thread).</summary>
    internal bool IsConnected
    {
        get { return IsOpen && _sessionId != null; }
    }

    /// <summary>Outbound depth for diagnostics (main or background).</summary>
    internal int OutboxDepth
    {
        get { return _outbox.Count; }
    }

    void OnDisable()
    {
        Shutdown();
    }

    void OnDestroy()
    {
        Shutdown();
    }

    /// <summary>
    /// Main-thread pump + supervision. Called from SpatialRuntime after PCA
    /// starts playing. Never blocks: socket IO lives on background tasks.
    /// </summary>
    internal void Tick()
    {
        DrainErrors();
        if (!_beginRequested || string.IsNullOrEmpty(_ipv4))
            return;
        float now = Time.realtimeSinceStartup;
        if (AwaitingReply && now - _replyStartedAt >= 45f)
        {
            VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator, "reply_timeout");
            CleanupSocket();
            ShowVoiceFeedback("Reply timed out. Reconnecting; please ask again.");
            _wasOpen = false;
            _lastConnectAttemptAt = now;
            return;
        }
        if (IsOpen)
        {
            if (!_wasOpen)
            {
                _wasOpen = true;
                _offlineShown = false;
                _lastPingAt = now;
            }
            PumpInbound();
            PumpTrackingStream(now);
            if (now - _lastPingAt >= PingIntervalS)
            {
                _lastPingAt = now;
                _outbox.Enqueue(PriorityPing, ProtocolJson.BuildPing(_sessionId));
            }
            return;
        }
        if (_wasOpen || (_socket != null && !_connecting))
        {
            _wasOpen = false;
            CleanupSocket();
            ShowOffline();
            _offlineShown = true;
            _lastConnectAttemptAt = now;
            return;
        }
        if (!_offlineShown && _attemptFailed)
        {
            ShowOffline();
            _offlineShown = true;
        }
        if (_socket != null || _connecting)
            return;
        if (now - _lastConnectAttemptAt >= ReconnectIntervalS)
            StartConnect(now);
    }

    bool IsOpen
    {
        get
        {
            var sock = _socket;
            return sock != null && sock.State == WebSocketState.Open && !_socketDown;
        }
    }

    void StartConnect(float now)
    {
        _lastConnectAttemptAt = now;
        _connecting = true;
        if (_cts == null)
            _cts = new CancellationTokenSource();
        string host = _ipv4;
        int port = ProtocolJson.CoordinatorPort;
        CancellationToken token = _cts.Token;
        Task.Run(() => ConnectAndServeAsync(host, port, token));
    }

    async Task ConnectAndServeAsync(string host, int port, CancellationToken token)
    {
        try
        {
            var sock = new ClientWebSocket();
            using (var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10)))
            using (var linked = CancellationTokenSource.CreateLinkedTokenSource(token, timeout.Token))
            {
                await sock.ConnectAsync(new Uri("ws://" + host + ":" + port), linked.Token);
            }
            if (token.IsCancellationRequested)
            {
                try { sock.Dispose(); } catch (Exception) { }
                return;
            }
            _socketDown = false;
            _socket = sock;
            VoiceBootstrapLog.WebsocketConnected(host, port);
            _outbox.Enqueue(PriorityHello, ProtocolJson.BuildHello(null));
            Task.Run(() => SendLoopAsync(sock, token));
            Task.Run(() => ReceiveLoopAsync(sock, token));
        }
        catch (Exception e)
        {
            VoiceBootstrapLog.SocketFailure("connect", e.GetType().Name);
            _attemptFailed = true;
        }
        finally
        {
            _connecting = false;
        }
    }

    async Task SendLoopAsync(ClientWebSocket sock, CancellationToken token)
    {
        try
        {
            while (!token.IsCancellationRequested && sock.State == WebSocketState.Open)
            {
                string json;
                if (_outbox.TryDequeue(out json))
                {
                    byte[] bytes = Encoding.UTF8.GetBytes(json);
                    await sock.SendAsync(
                        new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, token);
                }
                else
                {
                    await Task.Delay(10, token);
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception e)
        {
            VoiceBootstrapLog.SocketFailure("send", e.GetType().Name);
            if (ReferenceEquals(_socket, sock)) _socketDown = true;
        }
    }

    async Task ReceiveLoopAsync(ClientWebSocket sock, CancellationToken token)
    {
        var buffer = new byte[65536];
        var frame = new List<byte>(65536);
        try
        {
            while (!token.IsCancellationRequested && sock.State == WebSocketState.Open)
            {
                WebSocketReceiveResult result = await sock.ReceiveAsync(
                    new ArraySegment<byte>(buffer), token);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    if (ReferenceEquals(_socket, sock)) _socketDown = true;
                    return;
                }
                if (result.MessageType == WebSocketMessageType.Binary)
                    continue;
                for (int i = 0; i < result.Count; i++)
                    frame.Add(buffer[i]);
                if (result.EndOfMessage)
                {
                    string text = Encoding.UTF8.GetString(frame.ToArray());
                    frame.Clear();
                    if (!string.IsNullOrEmpty(text) && ReferenceEquals(_socket, sock))
                        _inbound.Enqueue(text);
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception e)
        {
            VoiceBootstrapLog.SocketFailure("receive", e.GetType().Name);
            if (ReferenceEquals(_socket, sock)) _socketDown = true;
        }
    }

    void DrainErrors()
    {
        while (_errors.TryDequeue(out _))
        {
        }
    }

    void PumpInbound()
    {
        string text;
        while (_inbound.TryDequeue(out text))
        {
            try
            {
                HandleMessage(text);
            }
            catch (Exception e)
            {
                Debug.LogWarning("CoordinatorClient: inbound handling (" + e.GetType().Name + ")");
            }
        }
    }

    void HandleMessage(string text)
    {
        string type;
        if (!ProtocolJson.TryGetMessageType(text, out type))
            return;
        if (type == "hello_ok")
        {
            string payload;
            if (ProtocolJson.TryGetPayloadObject(text, out payload))
            {
                PerceptionEnabled = JsonUtility.FromJson<HelloOptions>(payload).perception_qa;
                // session_id lookup works on any object substring, payload included.
                string session;
                if (ProtocolJson.TryGetSessionId(payload, out session) && session != null)
                    _sessionId = session;
                int port;
                if (ProtocolJson.TryGetIntField(payload, "artifact_port", out port) && port > 0)
                    _artifactPort = port;
            }
            VoiceBootstrapLog.HelloAccepted();
            // New session never clears drawings: no Store call here by design.
            return;
        }
        if (type == "pong")
            return;
        if (type == "scene_op")
        {
            if (PerceptionEnabled)
            {
                VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator, "scene_op_blocked");
                return;
            }
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.SceneOpMsg op;
            if (!ProtocolJson.TryParseSceneOp(payload, out op))
            {
                Debug.LogWarning("CoordinatorClient: malformed scene_op ignored");
                return;
            }
            if (op.Kind == "place_generated")
            {
                if (!PrepareSceneOp(op))
                    return;
                GeneratedMeshPlacer.TryHandle(this, this, op, _ipv4, _artifactPort, Cache);
                return;
            }
            if (op.Kind == "label" || op.Kind == "ghost" || op.Kind == "connect")
            {
                if (!PrepareSceneOp(op))
                    return;
                GhostLabelConnect.TryHandle(this, op);
                return;
            }
            if (op.Kind == "place_procedural" || op.Kind == "revise_procedural")
            {
                if (!PrepareSceneOp(op))
                    return;
                ProceduralFactory.TryHandle(this, op);
                return;
            }
            HandleMark(op);
            return;
        }
        if (type == "speak")
        {
            string replyId;
            ProtocolJson.TryGetUtteranceId(text, out replyId);
            if (PerceptionEnabled && (_pendingReplyId == null || replyId != _pendingReplyId))
                return;
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.SpeakMsg speak;
            if (!ProtocolJson.TryParseSpeak(payload, out speak))
                return;
            _pendingReplyId = null;
            ShowVoiceFeedback(speak.Text);
            if (SpeakPlayer == null)
            {
                Debug.LogWarning("CoordinatorClient: speak received but no SpeakCloudPlayer");
                return;
            }
            byte[] pcm;
            if (!SpeakCloudPlayer.TryDecodePcmBase64(speak.AudioDataB64, out pcm))
            {
                ShowVoiceFeedback(speak.Text, 12f);
                SpeakPlayer.TryPlay(speak.HasTurnId ? speak.TurnId : 0, speak.Text, null);
                return;
            }
            ShowVoiceFeedback(speak.Text, Mathf.Max(8f, pcm.Length / 32000f + 2f));
            SpeakPlayer.TryPlay(speak.HasTurnId ? speak.TurnId : 0, speak.Text, pcm);
            return;
        }
        if (type == "stop_speak")
        {
            _pendingReplyId = null;
            if (SpeakPlayer != null)
                SpeakPlayer.StopPlayback();
            return;
        }
        if (type == "speak_chunk")
        {
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.SpeakChunkMsg chunk;
            if (!ProtocolJson.TryParseSpeakChunk(payload, out chunk))
                return;
            if (SpeakPlayer == null)
                return;
            byte[] pcm;
            if (!SpeakCloudPlayer.TryDecodePcmBase64(chunk.AudioDataB64, out pcm))
                return;
            SpeakPlayer.AppendChunk(chunk.TurnId, pcm);
            return;
        }
        if (type == "speak_final")
        {
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.SpeakFinalMsg final;
            if (!ProtocolJson.TryParseSpeakFinal(payload, out final))
                return;
            string replyId;
            ProtocolJson.TryGetUtteranceId(text, out replyId);
            if (PerceptionEnabled && (_pendingReplyId == null || replyId != _pendingReplyId))
                return;
            _pendingReplyId = null;
            ShowVoiceFeedback(final.Text);
            if (SpeakPlayer != null)
                SpeakPlayer.FinishTurn(final.TurnId);
            return;
        }
        if (type == "tracking_status")
        {
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.TrackingStatusMsg status;
            if (!ProtocolJson.TryParseTrackingStatus(payload, out status))
                return;
            VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator,
                "tracking_status", ("track_state", status.State), ("generation", status.Generation));
            if (status.State == "selecting" || status.State == "initializing"
                || status.State == "tracking")
            {
                _trackingGeneration = Math.Max(_trackingGeneration, status.Generation);
                _trackStreaming = true;
            }
            else
            {
                _trackStreaming = false;
                if (TrackingOverlay != null)
                    TrackingOverlay.Hide();
            }
            if (!string.IsNullOrEmpty(status.Text))
                ShowVoiceFeedback(status.Text);
            return;
        }
        if (type == "tracking_result")
        {
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.TrackingResultMsg result;
            if (!ProtocolJson.TryParseTrackingResult(payload, out result))
                return;
            if (result.Generation < _trackingGeneration || TrackingOverlay == null)
            {
                VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator,
                    "tracking_result_stale", ("generation", result.Generation));
                return;
            }
            if (result.HasStageEpoch && GetStageEpoch != null
                && result.StageEpoch != GetStageEpoch())
            {
                VoiceBootstrapLog.Log(VoiceBootstrapLog.ComponentCoordinator,
                    "tracking_stage_mismatch", ("generation", result.Generation));
                _trackStreaming = false;
                if (TrackingOverlay != null)
                    TrackingOverlay.Hide();
                return;
            }
            _trackingGeneration = result.Generation;
            byte[] mask = null;
            if (!string.IsNullOrEmpty(result.MaskB64))
            {
                try { mask = Convert.FromBase64String(result.MaskB64); }
                catch (Exception) { mask = null; }
            }
            TrackingOverlay.Show(mask, result.Width, result.Height);
            return;
        }
        if (type == "turn_started")
            return;
        Debug.Log("CoordinatorClient: ignoring " + type);
    }

    /// <summary>
    /// Laptop mark path: dedupe, fence, resolve from the capture cache, pin,
    /// and ACK synchronously (same frame as render-or-reject).
    /// </summary>
    bool PrepareSceneOp(ProtocolJson.SceneOpMsg op)
    {
        if (op == null || string.IsNullOrEmpty(op.OpId))
            return false;
        if (!_seenOpIds.Add(op.OpId))
        {
            Debug.Log("CoordinatorClient: duplicate op ignored op=" + op.OpId);
            return false;
        }
        if (!op.HasTurnId || op.TurnId == 0)
        {
            EnqueueAck(op, "rejected", null, "invalid", null);
            Debug.LogWarning("CoordinatorClient: op rejected invalid turn op=" + op.OpId);
            return false;
        }
        int epoch = GetStageEpoch != null ? GetStageEpoch() : 1;
        if (!op.HasStageEpoch || op.StageEpoch != epoch)
        {
            EnqueueAck(op, "stale", null, "superseded", null);
            Debug.Log("CoordinatorClient: op fenced superseded op=" + op.OpId);
            return false;
        }
        return true;
    }

    internal void HandleMark(ProtocolJson.SceneOpMsg op)
    {
        if (!PrepareSceneOp(op))
            return;
        if (op.Kind != "mark")
        {
            Debug.Log("CoordinatorClient: ignoring non-mark kind=" + op.Kind);
            return;
        }
        PlacementResult result;
        if (!TryResolveTargetOp(op, out result))
            return;
        float period = op.HasMotion && op.MotionPeriodS > 0f
            ? op.MotionPeriodS
            : ProtocolJson.DefaultMarkPeriodS;
        if (result.ShouldPin)
        {
            string drawingId = !string.IsNullOrEmpty(op.DrawingId)
                ? op.DrawingId
                : (NewDrawingId != null ? NewDrawingId() : SpatialRuntime.NewFrameId());
            if (Store != null)
            {
                GameObject mark = Store.PlaceMark(result.Point, result.Normal, drawingId);
                if (mark != null)
                {
                    PulsingRing ring = mark.GetComponent<PulsingRing>();
                    if (ring != null)
                        ring.periodS = period;
                }
                EnqueueAck(op, "placed", drawingId, null, "surface");
                Debug.Log("COORDINATOR_PLACED op=" + op.OpId + " drawing=" + drawingId);
            }
            else
            {
                Debug.LogWarning("CoordinatorClient: store missing, cannot pin op=" + op.OpId);
                EnqueueAck(op, "rejected", null, "no_surface", null);
            }
            return;
        }
        EnqueueAckForResult(op, result);
    }

    internal bool TryResolveTargetOp(ProtocolJson.SceneOpMsg op, out PlacementResult result)
    {
        result = default;
        if (string.IsNullOrEmpty(op.TargetFrameId))
        {
            EnqueueAck(op, "rejected", null, "invalid", null);
            Debug.LogWarning("CoordinatorClient: op rejected missing target op=" + op.OpId);
            return false;
        }
        CaptureGeometryCache.Entry entry = null;
        bool hasEntry = Cache != null && Cache.TryGet(op.TargetFrameId, out entry) && entry != null;
        if (!hasEntry || !entry.HasHit)
        {
            ShowChipText(PlacementResolver.ChipNoSurfaceText);
            EnqueueAck(op, "rejected", null, "no_surface", null);
            return false;
        }
        Vector3? delayed = DelayedHit != null
            ? DelayedHit(PlacementResolver.CachedRay(entry))
            : (Vector3?)null;
        result = PlacementResolver.TryPlaceFromCapture(entry, "placed", delayed, true);
        return true;
    }

    internal void EnqueueAckForResult(ProtocolJson.SceneOpMsg op, PlacementResult result)
    {
        if (result.Outcome == PlacementOutcome.TooClose)
        {
            ShowChipText(PlacementResolver.ChipTooCloseText);
            EnqueueAck(op, "rejected", null, "too_close", null);
        }
        else if (result.Outcome == PlacementOutcome.Stale)
        {
            ShowChipText(PlacementResolver.ChipStaleText);
            EnqueueAck(op, "stale", null, "superseded", null);
        }
        else
        {
            ShowChipText(PlacementResolver.ChipNoSurfaceText);
            EnqueueAck(op, "rejected", null, "no_surface", null);
        }
    }

    /// <summary>
    /// ACK enqueue (priority 2, ahead of ping/frame). Main thread. Turn id
    /// is normalized to minimum 1: a mark rejected for a missing/zero turn
    /// still settles server-side, and placement_ack requires turn_id >= 1
    /// (a turn-0 ACK would fail validation, be dropped, and leave the op
    /// lingering in pending_ops forever).
    /// </summary>
    internal void EnqueueAck(
        ProtocolJson.SceneOpMsg op, string status, string drawingId, string reason, string pin)
    {
        int ackTurnId = op.TurnId < 1 ? 1 : op.TurnId;
        _outbox.Enqueue(PriorityAck, ProtocolJson.BuildAck(
            _sessionId, ackTurnId, op.OpId, op.StageEpoch, status, drawingId, reason, pin));
    }

    /// <summary>
    /// Frame enqueue (priority 9, envelope only, no jpeg_b64). Dropped while
    /// offline: frames are capture-time geometry and go stale anyway.
    /// Main thread.
    /// </summary>
    internal void EnqueueFrame(CaptureEnvelope env)
    {
        if (env == null || !IsOpen)
            return;
        _outbox.Enqueue(PriorityFrame, ProtocolJson.BuildFrame(_sessionId, env, OpenUtteranceId));
    }

    /// <summary>SAM2 stream frame while a highlight is live (5 fps, small JPEG).</summary>
    internal void PumpTrackingStream(float now)
    {
        if (!_trackStreaming || !IsConnected || TrackCapture == null)
            return;
        // An open utterance is about to need the gate for its question frame.
        if (OpenUtteranceId != null)
            return;
        if (now - _lastTrackFrameAt < TrackFrameIntervalS)
            return;
        _lastTrackFrameAt = now;
        TrackCapture.Capture((env, jpeg, reason) =>
        {
            if (reason != null || env == null || jpeg == null)
                return;
            EnqueueTrackingFrame(env, jpeg);
        });
    }

    /// <summary>Drop any in-flight tracking capture so a question frame wins the gate.</summary>
    internal void CancelTrackCapture()
    {
        if (TrackCapture != null)
            TrackCapture.Cancel();
    }

    internal bool EnqueueTrackingFrame(CaptureEnvelope env, byte[] jpeg)
    {
        if (!IsConnected || env == null || jpeg == null || jpeg.Length == 0
            || jpeg.Length > 65536)
            return false;
        _outbox.Enqueue(PriorityFrame,
            ProtocolJson.BuildTrackingFrame(_sessionId, env, Convert.ToBase64String(jpeg)));
        return true;
    }

    /// <summary>Queue the final JPEG in the SAME FIFO lane as audio and end.</summary>
    public bool EnqueuePerceptionFrame(string utteranceId, CaptureEnvelope env, byte[] jpeg)
    {
        if (!IsConnected || !PerceptionEnabled || OpenUtteranceId != utteranceId
            || env == null || jpeg == null || jpeg.Length == 0 || jpeg.Length > 65536)
            return false;
        _outbox.Enqueue(PriorityFrame,
            ProtocolJson.BuildFrame(_sessionId, env, utteranceId, Convert.ToBase64String(jpeg)));
        return true;
    }

    /// <summary>Abandon the pending reply (voice barge-in moved on).</summary>
    public void AbandonReply()
    {
        _pendingReplyId = null;
    }

    /// <summary>Open voice utterance (set by the mic uplink, cleared on end).</summary>
    public string OpenUtteranceId;

    public void EnqueueAudioChunk(string utteranceId, string dataB64)
    {
        if (string.IsNullOrEmpty(utteranceId) || string.IsNullOrEmpty(dataB64))
            return;
        if (!IsConnected)
        {
            VoiceBootstrapLog.AudioDropOffline(VoiceBootstrapLog.PcmBytesFromBase64(dataB64));
            return;
        }
        _outbox.Enqueue(PriorityAudioChunk,
            ProtocolJson.BuildAudioChunk(_sessionId, utteranceId, dataB64));
    }

    public void EnqueueUtteranceEnd(string utteranceId, bool expectReply = true)
    {
        if (string.IsNullOrEmpty(utteranceId))
            return;
        if (OpenUtteranceId == utteranceId)
            OpenUtteranceId = null;
        if (!IsConnected)
        {
            VoiceBootstrapLog.UtteranceEndDropOffline();
            return;
        }
        if (expectReply)
        {
            _pendingReplyId = utteranceId;
            _replyStartedAt = Time.realtimeSinceStartup;
        }
        _outbox.Enqueue(PriorityUtteranceEnd,
            ProtocolJson.BuildUtteranceEnd(_sessionId, utteranceId));
    }

    /// <summary>
    /// Cancel enqueue (priority 0). No slice-2 trigger calls this yet; the
    /// slot exists so a future trigger jumps ahead of queued frames.
    /// </summary>
    internal void EnqueueCancel(string opId, int turnId)
    {
        _outbox.Enqueue(PriorityCancel, ProtocolJson.BuildCancel(_sessionId, turnId, opId));
    }

    internal void ShowVoiceFeedback(string text, float seconds = 8f)
    {
        if (Caption != null) Caption.Show(text, CenterEye, seconds);
        else ShowChipText(text);
    }

    internal void ShowChipText(string text)
    {
        if (Chip == null)
            return;
        try
        {
            Chip.Show(text, CenterEye);
        }
        catch (Exception e)
        {
            Debug.LogWarning("CoordinatorClient: honesty chip unavailable (" + e.GetType().Name + ")");
        }
    }

    void ShowOffline()
    {
        // Offline honesty: exact copy, rings preserved (never Store.Clear).
        ShowVoiceFeedback(OfflineChipText);
        Debug.Log("CoordinatorClient: offline, drawings kept");
    }

    void CleanupSocket()
    {
        try
        {
            if (_cts != null)
                _cts.Cancel();
        }
        catch (Exception)
        {
        }
        var sock = _socket;
        _socket = null;
        _socketDown = false;
        if (sock != null)
        {
            try { sock.Dispose(); } catch (Exception) { }
        }
        try
        {
            if (_cts != null)
                _cts.Dispose();
        }
        catch (Exception)
        {
        }
        _cts = null;
        _sessionId = null;
        PerceptionEnabled = false;
        _pendingReplyId = null;
        OpenUtteranceId = null;
        _trackStreaming = false;
        _trackingGeneration = -1;
        if (TrackingOverlay != null)
            TrackingOverlay.Hide();
        _outbox.Clear();
        while (_inbound.TryDequeue(out _)) { }
    }

    void Shutdown()
    {
        _beginRequested = false;
        CleanupSocket();
    }
}
