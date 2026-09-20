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
    internal SpeakCloudPlayer SpeakPlayer;
    internal VoiceCaption Caption;

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
    int _trackingGeneration;
    string _guideTrackingUtterance;
    int _guideTrackingTurn;
    int _finishedGuideGeneration = -1;
    int _activeTurn;
    string _activeUtterance;
    int _droppedResults;
    string _stoppedUtterance;
    string _helloJson;
    string _pendingReplyId;
    float _replyStartedAt;
    public bool IsReady { get { return IsOpen && _sessionId != null; } }
    public bool PerceptionEnabled { get; private set; }
    /// <summary>B-mode: the live conversation loop owns the microphone.</summary>
    public bool LiveConversation { get; private set; }
    /// <summary>True when an utterance should carry a camera frame.</summary>
    public bool WantsCameraFrame { get { return PerceptionEnabled || LiveConversation; } }
    public string SessionId { get { return _sessionId; } }
    public bool AwaitingReply { get { return _pendingReplyId != null; } }
    public TrackingResult LatestTrackingResult { get; private set; }
    public GuideStep ActiveGuideStep { get; private set; }
    public event Action<GuideStep> GuideStepReceived;
    public event Action<GuideFinished> GuideFinishedReceived;
    public event Action<TrackingResult> TrackingResultReceived;
    public event Action<TrackingStatus> TrackingStatusReceived;

    [Serializable]
    class HelloOptions { public bool perception_qa; }

    /// <summary>
    /// Hand the microphone between A-mode push-to-talk (QuestStreamInput) and
    /// B-mode continuous conversation (MicUtterance). Exactly one holds the
    /// device: MicUtterance.OnDisable calls Microphone.End when it loses it.
    /// Frame streaming is unaffected and continues in both modes.
    /// </summary>
    internal void SetLiveConversation(bool on)
    {
        if (LiveConversation == on)
            return;
        LiveConversation = on;
        var mic = GetComponent<MicUtterance>();
        if (mic != null)
            mic.enabled = on;
        if (!on)
        {
            _pendingReplyId = null;
            if (SpeakPlayer != null)
                SpeakPlayer.StopPlayback();
        }
        Debug.Log("QUEST_MODE live conversation " + (on ? "ON (B held the mic)"
                                                       : "OFF (A push-to-talk)"));
        ShowVoiceFeedback(on ? "Conversation on. Just speak." : "Conversation off. Hold A to talk.");
    }

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
            VoiceBootstrapLog.WebsocketConnected(host, port);
            _outbox.Enqueue(PriorityHello, _helloJson);
            Task.Run(() => SendLoopAsync(sock, token));
            Task.Run(() => ReceiveLoopAsync(sock, token));
        }
        catch (Exception e)
        {
            try { connectingSocket?.Dispose(); } catch (Exception) { }
            _errors.Enqueue("coordinator connect (" + e.GetType().Name + ")");
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
        if (type == "guide_step" || type == "guide_finished")
        {
            string session, payload;
            if (!ProtocolJson.TryGetSessionId(text, out session) || session == null || session != _sessionId
                || !ProtocolJson.TryGetPayloadObject(text, out payload)) return;
            if (type == "guide_step")
            {
                var step = JsonUtility.FromJson<GuideStep>(payload);
                if (step == null || !step.IsValid || step.generation < _trackingGeneration
                    || step.generation <= _finishedGuideGeneration) return;
                if (ActiveGuideStep == null || ActiveGuideStep.guide_id != step.guide_id)
                {
                    ProtocolJson.TryGetUtteranceId(text, out _guideTrackingUtterance);
                    _guideTrackingTurn = JsonUtility.FromJson<TrackingMessageHeader>(text).turn_id;
                    LatestTrackingResult = null;
                }
                _trackingGeneration = step.generation;
                ActiveGuideStep = step;
                if (SpeakPlayer != null) SpeakPlayer.StopPlayback();
                QuestSpeech.Stop();
                GuideStepReceived?.Invoke(step);
                // Speech belongs to the following speak message, which carries cloud PCM.
                ShowVoiceFeedback(step.instruction, 30f);
            }
            else
            {
                var finished = JsonUtility.FromJson<GuideFinished>(payload);
                if (finished == null || ActiveGuideStep == null
                    || finished.guide_id != ActiveGuideStep.guide_id
                    || finished.generation < ActiveGuideStep.generation) return;
                FinishGuide(finished);
                if (!string.IsNullOrEmpty(finished.instruction)) ShowVoiceFeedback(finished.instruction);
            }
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
            bool guideTracking = type != "turn_started" && ActiveGuideStep != null
                && header.utterance_id == _guideTrackingUtterance;
            if (header.utterance_id != _activeUtterance && !guideTracking)
            {
                // A newer request supersedes older results locally. Logged
                // because a silent drop here looks exactly like "no masks".
                if (type == "tracking_result" && _droppedResults++ % 30 == 0)
                    Debug.Log("QUEST_TRACKING dropping " + type + " for utterance "
                              + header.utterance_id + "; active is " + (_activeUtterance ?? "none"));
                return;
            }
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
                if (status.generation < _trackingGeneration || status.generation <= _finishedGuideGeneration) return;
                _trackingGeneration = status.generation;
                if (status.state != "tracking") LatestTrackingResult = null;
                Debug.Log("QUEST_TRACKING " + status.state + ": " + status.text);
                TrackingStatusReceived?.Invoke(status);
            }
            else
            {
                var result = JsonUtility.FromJson<TrackingResult>(payload);
                if (result.generation < _trackingGeneration || result.generation <= _finishedGuideGeneration
                    || result.stage_epoch != GetStageEpoch()) return;
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
            if (op.Kind == "place_box")
            {
                if (!PrepareSceneOp(op))
                    return;
                bool placed = LayoutMode.Ensure().TryPlaceBox(op);
                if (placed)
                    EnqueueAck(op, "placed", op.OpId, null, "surface");
                else
                    EnqueueAck(op, "rejected", null, "invalid", null);
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
            // B-mode (live conversation) replies carry cloud PCM. A-mode
            // push-to-talk sends audio:null, and the headset speaks it with
            // Android TTS -- no credit, and it still works once the key dies.
            byte[] pcm;
            if (SpeakPlayer != null && SpeakCloudPlayer.TryDecodePcmBase64(speak.AudioDataB64, out pcm))
            {
                ShowVoiceFeedback(speak.Text, Mathf.Max(8f, pcm.Length / 32000f + 2f));
                SpeakPlayer.TryPlay(speak.HasTurnId ? speak.TurnId : 0, speak.Text, pcm);
                return;
            }
            if (!string.IsNullOrEmpty(speak.Text))
                QuestSpeech.Speak(speak.Text);
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
        if (type == "turn_started")
            return;        // Other scene operations are not handled here.
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

    /// <summary>Queue the final JPEG in the SAME FIFO lane as audio and end.</summary>
    public bool EnqueuePerceptionFrame(string utteranceId, CaptureEnvelope env, byte[] jpeg)
    {
        if (!IsConnected || !WantsCameraFrame || OpenUtteranceId != utteranceId
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
        // B-mode turns can seed tracking too, and results are matched against
        // _activeUtterance. Without this every B-mode mask was discarded.
        _activeUtterance = utteranceId;
        _activeTurn = 0;
        _stoppedUtterance = null;
        if (ActiveGuideStep == null) LatestTrackingResult = null;
        _outbox.Enqueue(PriorityUtteranceEnd,
            ProtocolJson.BuildUtteranceEnd(_sessionId, utteranceId));
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
        if (ActiveGuideStep == null) LatestTrackingResult = null;
        // All audio chunks and utterance_end share a FIFO priority: end cannot
        // overtake its own PCM. The pinned JPEG occupies the next higher lane.
        const int chunkBytes = 3200;
        for (int i = 0; i < pcm.Length; i += chunkBytes)
            _outbox.Enqueue(8, ProtocolJson.BuildAudioChunk(_sessionId, utteranceId, pcm, i,
                Math.Min(chunkBytes, pcm.Length - i)));
        _outbox.Enqueue(8, ProtocolJson.BuildUtteranceEnd(_sessionId, utteranceId, frameId));
    }

    void FinishGuide(GuideFinished finished)
    {
        _finishedGuideGeneration = Mathf.Max(_finishedGuideGeneration, finished.generation);
        ActiveGuideStep = null;
        _guideTrackingUtterance = null;
        LatestTrackingResult = null;
        if (SpeakPlayer != null) SpeakPlayer.StopPlayback();
        QuestSpeech.Stop();
        if (Caption != null) Caption.Hide();
        GuideFinishedReceived?.Invoke(finished);
    }

    void ClearGuide(string reason)
    {
        if (ActiveGuideStep != null)
            FinishGuide(new GuideFinished { guide_id = ActiveGuideStep.guide_id,
                generation = ActiveGuideStep.generation, reason = reason });
    }

    public void StopTracking()
    {
        int cancelTurn = ActiveGuideStep != null ? _guideTrackingTurn : _activeTurn;
        ClearGuide("stopped");
        _stoppedUtterance = _activeUtterance;
        LatestTrackingResult = null;
        if (IsReady && cancelTurn > 0)
            _outbox.Enqueue(PriorityCancel, ProtocolJson.BuildTrackingCancel(_sessionId, cancelTurn));
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
        ClearGuide("stopped");
        _finishedGuideGeneration = -1;
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
        // _sessionId / tracking generation are already reset at the top of
        // CleanupSocket; these are the live-conversation fields.
        PerceptionEnabled = false;
        _pendingReplyId = null;
        OpenUtteranceId = null;
        _outbox.Clear();
        while (_inbound.TryDequeue(out _)) { }
    }

    void Shutdown()
    {
        _beginRequested = false;
        CleanupSocket();
    }
}
