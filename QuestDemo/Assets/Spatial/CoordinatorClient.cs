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
/// slice 2). Opt-in QuestStreamInput adds JPEGs, recorded PCM, and tracking
/// result events without routing segmentation through surface placement.
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
    public const int PriorityAck = 2;
    public const int PriorityPing = 8;
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
        string _latestVideoFrame;

        public void VideoFrame(string json, bool selected)
        {
            lock (_gate)
            {
                _latestVideoFrame = null;
                if (selected) Enqueue(7, json); // pinned snapshot, not replaceable
                else _latestVideoFrame = json;
            }
        }

        public void Clear()
        {
            lock (_gate) { _queues.Clear(); _latestVideoFrame = null; }
        }

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
                if (_latestVideoFrame != null)
                {
                    json = _latestVideoFrame;
                    _latestVideoFrame = null;
                    return true;
                }
            }
            json = null;
            return false;
        }

        public int Count
        {
            get
            {
                lock (_gate)
                {
                    int n = _latestVideoFrame == null ? 0 : 1;
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
    float _lastPingAt;
    float _lastConnectAttemptAt = -1000f;
    bool _wasOpen;
    bool _offlineShown;
    bool _attemptFailed;
    int _trackingGeneration;
    int _activeTurn;
    string _activeUtterance;
    string _stoppedUtterance;
    string _helloJson;
    public bool IsReady { get { return IsOpen && _sessionId != null; } }
    public string SessionId { get { return _sessionId; } }
    public TrackingResult LatestTrackingResult { get; private set; }
    public event Action<TrackingResult> TrackingResultReceived;
    public event Action<TrackingStatus> TrackingStatusReceived;

    /// <summary>Queue a hello + start supervision. No socket work happens here.</summary>
    internal void Begin(string ipv4)
    {
        _ipv4 = ipv4;
        var settings = QuestTrackingSettings.Load();
        _helloJson = settings != null && settings.enableTracking
            ? ProtocolJson.BuildTrackingHello(SystemInfo.operatingSystem)
            : ProtocolJson.BuildHello(null);
        _beginRequested = true;
    }

    internal bool ConnectRequested
    {
        get { return _beginRequested; }
    }

    /// <summary>True while the socket is open (main thread).</summary>
    internal bool IsConnected
    {
        get { return IsOpen; }
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
        if (IsOpen)
        {
            if (!_wasOpen)
            {
                _wasOpen = true;
                _offlineShown = false;
                _lastPingAt = now;
            }
            PumpInbound();
            if (now - _lastPingAt >= PingIntervalS)
            {
                _lastPingAt = now;
                _outbox.Enqueue(PriorityPing, ProtocolJson.BuildPing(_sessionId));
            }
            return;
        }
        if (_wasOpen)
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
        _outbox.Clear();
        while (_inbound.TryDequeue(out _)) { }
        if (_cts == null)
            _cts = new CancellationTokenSource();
        string host = _ipv4;
        int port = ProtocolJson.CoordinatorPort;
        CancellationToken token = _cts.Token;
        Task.Run(() => ConnectAndServeAsync(host, port, token));
    }

    async Task ConnectAndServeAsync(string host, int port, CancellationToken token)
    {
        ClientWebSocket connectingSocket = null;
        try
        {
            var sock = connectingSocket = new ClientWebSocket();
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
            _outbox.Enqueue(PriorityHello, _helloJson);
            Task.Run(() => SendLoopAsync(sock, token));
            Task.Run(() => ReceiveLoopAsync(sock, token));
        }
        catch (Exception e)
        {
            try { connectingSocket?.Dispose(); } catch (Exception) { }
            _errors.Enqueue("coordinator connect (" + e.GetType().Name + ")");
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
            _errors.Enqueue("coordinator send (" + e.GetType().Name + ")");
            _socketDown = true;
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
                    _socketDown = true;
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
                    if (token.IsCancellationRequested || !ReferenceEquals(_socket, sock))
                        return;
                    if (!string.IsNullOrEmpty(text))
                        _inbound.Enqueue(text);
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception e)
        {
            _errors.Enqueue("coordinator receive (" + e.GetType().Name + ")");
            _socketDown = true;
        }
    }

    void DrainErrors()
    {
        string error;
        while (_errors.TryDequeue(out error))
            Debug.LogWarning("CoordinatorClient: " + error);
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
                // session_id lookup works on any object substring, payload included.
                string session;
                if (ProtocolJson.TryGetSessionId(payload, out session) && session != null)
                    _sessionId = session;
            }
            // New session never clears drawings: no Store call here by design.
            return;
        }
        if (type == "pong")
            return;
        if (type == "turn_started" || type == "tracking_status" || type == "tracking_result")
        {
            string session;
            if (!ProtocolJson.TryGetSessionId(text, out session) || session != _sessionId)
                return;
            var header = JsonUtility.FromJson<TrackingMessageHeader>(text);
            if (header.utterance_id != _activeUtterance)
                return; // a new push-to-talk request supersedes old results locally
            if (type == "turn_started")
            {
                _activeTurn = header.turn_id;
                if (_stoppedUtterance == _activeUtterance)
                    _outbox.Enqueue(PriorityCancel, ProtocolJson.BuildTrackingCancel(_sessionId, _activeTurn));
                return;
            }
            if (_stoppedUtterance != null && _stoppedUtterance == _activeUtterance)
                return;
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            if (type == "tracking_status")
            {
                var status = JsonUtility.FromJson<TrackingStatus>(payload);
                if (status.generation < _trackingGeneration) return;
                _trackingGeneration = status.generation;
                if (status.state != "tracking") LatestTrackingResult = null;
                Debug.Log("QUEST_TRACKING " + status.state + ": " + status.text);
                TrackingStatusReceived?.Invoke(status);
            }
            else
            {
                var result = JsonUtility.FromJson<TrackingResult>(payload);
                if (result.generation < _trackingGeneration || result.stage_epoch != GetStageEpoch()) return;
                _trackingGeneration = result.generation;
                result.RawPayloadJson = payload;
                bool first = LatestTrackingResult == null;
                LatestTrackingResult = result;
                if (first) Debug.Log("QUEST_TRACKING first mask frame=" + result.frame_id);
                TrackingResultReceived?.Invoke(result);
            }
            return;
        }
        if (type == "scene_op")
        {
            string payload;
            if (!ProtocolJson.TryGetPayloadObject(text, out payload))
                return;
            ProtocolJson.SceneOpMsg op;
            if (!ProtocolJson.TryParseSceneOp(payload, out op))
            {
                Debug.LogWarning("CoordinatorClient: malformed scene_op ignored");
                return;
            }
            HandleMark(op);
            return;
        }
        // Other scene operations and speech playback are not handled here.
        Debug.Log("CoordinatorClient: ignoring " + type);
    }

    /// <summary>
    /// Laptop mark path: dedupe, fence, resolve from the capture cache, pin,
    /// and ACK synchronously (same frame as render-or-reject).
    /// </summary>
    internal void HandleMark(ProtocolJson.SceneOpMsg op)
    {
        if (op == null || string.IsNullOrEmpty(op.OpId))
            return;
        if (!_seenOpIds.Add(op.OpId))
        {
            Debug.Log("CoordinatorClient: duplicate op ignored op=" + op.OpId);
            return;
        }
        if (!op.HasTurnId || op.TurnId == 0)
        {
            EnqueueAck(op, "rejected", null, "invalid", null);
            Debug.LogWarning("CoordinatorClient: op rejected invalid turn op=" + op.OpId);
            return;
        }
        int epoch = GetStageEpoch != null ? GetStageEpoch() : 1;
        if (!op.HasStageEpoch || op.StageEpoch != epoch)
        {
            EnqueueAck(op, "stale", null, "superseded", null);
            Debug.Log("CoordinatorClient: op fenced superseded op=" + op.OpId);
            return;
        }
        if (op.Kind != "mark")
        {
            Debug.Log("CoordinatorClient: ignoring non-mark kind=" + op.Kind);
            return;
        }
        if (string.IsNullOrEmpty(op.TargetFrameId))
        {
            EnqueueAck(op, "rejected", null, "invalid", null);
            Debug.LogWarning("CoordinatorClient: op rejected missing target op=" + op.OpId);
            return;
        }
        CaptureGeometryCache.Entry entry = null;
        bool hasEntry = Cache != null && Cache.TryGet(op.TargetFrameId, out entry) && entry != null;
        if (!hasEntry || !entry.HasHit)
        {
            // No cached surface for this frame: honest miss, never a pin.
            // (A remote too_close is indistinguishable here and reports the
            // same no_surface copy rather than floating a pin.)
            ShowChipText(PlacementResolver.ChipNoSurfaceText);
            EnqueueAck(op, "rejected", null, "no_surface", null);
            return;
        }
        Vector3? delayed = DelayedHit != null
            ? DelayedHit(PlacementResolver.CachedRay(entry))
            : (Vector3?)null;
        PlacementResult result =
            PlacementResolver.TryPlaceFromCapture(entry, "placed", delayed, true);
        float period = op.HasMotion && op.MotionPeriodS > 0f
            ? op.MotionPeriodS
            : ProtocolJson.DefaultMarkPeriodS;
        if (result.ShouldPin)
        {
            string drawingId = NewDrawingId != null ? NewDrawingId() : SpatialRuntime.NewFrameId();
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
        _outbox.Enqueue(PriorityFrame, ProtocolJson.BuildFrame(_sessionId, env));
    }

    [Serializable]
    class TrackingMessageHeader
    {
        public string utterance_id;
        public int turn_id;
    }

    public void EnqueueVideoFrame(CaptureEnvelope env, byte[] jpeg, bool selected)
    {
        if (!IsReady) return;
        _outbox.VideoFrame(ProtocolJson.BuildVideoFrame(_sessionId, env, jpeg), selected);
    }

    public void EnqueueUtterance(string utteranceId, byte[] pcm, string frameId)
    {
        if (!IsReady) return;
        _activeUtterance = utteranceId;
        _activeTurn = 0;
        _stoppedUtterance = null;
        LatestTrackingResult = null;
        // All audio chunks and utterance_end share a FIFO priority: end cannot
        // overtake its own PCM. The pinned JPEG occupies the next higher lane.
        const int chunkBytes = 3200;
        for (int i = 0; i < pcm.Length; i += chunkBytes)
            _outbox.Enqueue(8, ProtocolJson.BuildAudioChunk(_sessionId, utteranceId, pcm, i,
                Math.Min(chunkBytes, pcm.Length - i)));
        _outbox.Enqueue(8, ProtocolJson.BuildUtteranceEnd(_sessionId, utteranceId, frameId));
    }

    public void StopTracking()
    {
        _stoppedUtterance = _activeUtterance;
        LatestTrackingResult = null;
        if (IsReady && _activeTurn > 0)
            _outbox.Enqueue(PriorityCancel, ProtocolJson.BuildTrackingCancel(_sessionId, _activeTurn));
        TrackingStatusReceived?.Invoke(new TrackingStatus { state = "stopped", text = "Tracking stopped.", generation = _trackingGeneration });
        Debug.Log("QUEST_TRACKING stopped locally");
    }

    /// <summary>
    /// Cancel enqueue (priority 0). No slice-2 trigger calls this yet; the
    /// slot exists so a future trigger jumps ahead of queued frames.
    /// </summary>
    internal void EnqueueCancel(string opId, int turnId)
    {
        _outbox.Enqueue(PriorityCancel, ProtocolJson.BuildCancel(_sessionId, turnId, opId));
    }

    void ShowChipText(string text)
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
        ShowChipText(OfflineChipText);
        Debug.Log("CoordinatorClient: offline, drawings kept");
    }

    void CleanupSocket()
    {
        TrackingStatusReceived?.Invoke(new TrackingStatus {
            state = "stopped", text = "Laptop disconnected.", generation = _trackingGeneration
        });
        _sessionId = null;
        _activeTurn = 0;
        _trackingGeneration = 0;
        _activeUtterance = null;
        _stoppedUtterance = null;
        LatestTrackingResult = null;
        _outbox.Clear();
        while (_inbound.TryDequeue(out _)) { }
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
        _outbox.Clear();
        while (_inbound.TryDequeue(out _)) { }
    }

    void Shutdown()
    {
        _beginRequested = false;
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
    }
}
