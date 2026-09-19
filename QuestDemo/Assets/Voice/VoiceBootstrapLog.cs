using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

/// <summary>
/// Redacted lifecycle logs for the controller-free headset voice path.
/// Emits counts, types, and enums only — never PCM, base64, captions, or URLs.
/// </summary>
public static class VoiceBootstrapLog
{
    public const string Prefix = "VoiceBootstrap";
    public const string ComponentMic = "MicUtterance";
    public const string ComponentCoordinator = "CoordinatorClient";
    public const string ComponentSpeak = "SpeakCloudPlayer";

    static readonly HashSet<string> AllowedStringKeys = new HashSet<string>
    {
        "event",
        "component",
        "exception_type",
        "reason",
        "phase",
        "playback_state",
        "host",
    };

    /// <summary>PCM byte length from base64 without retaining or logging payload.</summary>
    public static int PcmBytesFromBase64(string dataB64)
    {
        if (string.IsNullOrEmpty(dataB64))
            return 0;
        int padding = 0;
        if (dataB64.Length >= 2 && dataB64[dataB64.Length - 1] == '=')
            padding++;
        if (dataB64.Length >= 2 && dataB64[dataB64.Length - 2] == '=')
            padding++;
        int len = dataB64.Length;
        if (len < 4)
            return 0;
        return Math.Max(0, (len / 4) * 3 - padding);
    }

    /// <summary>Test seam: diagnostic fields must not carry media or text payloads.</summary>
    public static bool IsAllowedField(string key, object value)
    {
        if (string.IsNullOrEmpty(key))
            return false;
        if (value == null)
            return true;
        if (value is bool || value is int || value is long || value is uint)
            return true;
        if (value is float || value is double)
            return true;
        if (value is string text)
        {
            if (!AllowedStringKeys.Contains(key))
                return false;
            if (text.Length > 64)
                return false;
            if (LooksLikePayload(text))
                return false;
            return true;
        }
        return false;
    }

    static bool LooksLikePayload(string text)
    {
        if (text.IndexOf(' ') >= 0)
            return true;
        if (text.StartsWith("http", StringComparison.OrdinalIgnoreCase))
            return true;
        if (text.StartsWith("ws://", StringComparison.OrdinalIgnoreCase))
            return true;
        if (text.Length >= 32 && IsMostlyBase64(text))
            return true;
        return false;
    }

    static bool IsMostlyBase64(string text)
    {
        int ok = 0;
        foreach (char c in text)
        {
            if (char.IsLetterOrDigit(c) || c == '+' || c == '/' || c == '=')
                ok++;
        }
        return ok >= text.Length - 2;
    }

    public static string FormatLine(string component, string evt, params (string key, object value)[] fields)
    {
        var sb = new StringBuilder(Prefix);
        sb.Append(" component=").Append(component);
        sb.Append(" event=").Append(evt);
        if (fields != null)
        {
            foreach (var pair in fields)
            {
                if (!IsAllowedField(pair.key, pair.value))
                    throw new ArgumentException("VoiceBootstrap field rejected: " + pair.key);
                if (pair.value == null)
                    continue;
                sb.Append(' ').Append(pair.key).Append('=').Append(FormatScalar(pair.value));
            }
        }
        return sb.ToString();
    }

    static string FormatScalar(object value)
    {
        if (value is bool b)
            return b ? "true" : "false";
        return Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
    }

    public static void Log(string component, string evt, params (string key, object value)[] fields)
    {
        Debug.Log(FormatLine(component, evt, fields));
    }

    public static void MicPermission(bool granted)
    {
        Log(ComponentMic, "mic_permission", ("granted", granted));
    }

    public static void MicStarted()
    {
        Log(ComponentMic, "mic_started");
    }

    public static void MicFailed(string exceptionType)
    {
        Log(ComponentMic, "mic_failed", ("exception_type", exceptionType));
    }

    public static void VadOpened(int prerollChunks)
    {
        Log(ComponentMic, "vad_opened", ("preroll_chunks", prerollChunks));
    }

    public static void VadEnded(int sentChunks, int pcmBytes)
    {
        Log(ComponentMic, "vad_ended", ("sent_chunks", sentChunks), ("pcm_bytes", pcmBytes));
    }

    public static void OnsetDropped(string reason)
    {
        Log(ComponentMic, "onset_dropped", ("reason", reason));
    }

    public static void WebsocketConnected(string host, int port)
    {
        Log(ComponentCoordinator, "websocket_connected", ("host", host), ("port", port));
    }

    public static void HelloAccepted()
    {
        Log(ComponentCoordinator, "hello_accepted");
    }

    public static void SocketFailure(string phase, string exceptionType)
    {
        Log(ComponentCoordinator, "socket_failure", ("phase", phase), ("exception_type", exceptionType));
    }

    public static void AudioDropOffline(int pcmBytes)
    {
        Log(ComponentCoordinator, "audio_drop_offline", ("pcm_bytes", pcmBytes));
    }

    public static void UtteranceEndDropOffline()
    {
        Log(ComponentCoordinator, "utterance_end_drop_offline");
    }

    public static void PlaybackAccepted(int turnId, int pcmBytes)
    {
        Log(ComponentSpeak, "playback_accepted", ("turn_id", turnId), ("pcm_bytes", pcmBytes));
    }

    public static void PlaybackRejected(int turnId, int pcmBytes, string reason)
    {
        Log(ComponentSpeak, "playback_rejected", ("turn_id", turnId), ("pcm_bytes", pcmBytes), ("reason", reason));
    }

    public static void PlaybackStarted(int turnId)
    {
        Log(ComponentSpeak, "playback_started", ("turn_id", turnId), ("playback_state", "playing"));
    }

    public static void PlaybackFinished(int turnId)
    {
        Log(ComponentSpeak, "playback_finished", ("turn_id", turnId), ("playback_state", "idle"));
    }
}
