using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Speaks the assistant's reply out of the headset with Android's built-in
/// text-to-speech. The coordinator sends the model's sentence as a `speak`
/// message; <see cref="CoordinatorClient"/> hands it here.
///
/// Android calls onInit on its own thread and the engine needs a few hundred
/// milliseconds to load, so requests queue until it is ready and are spoken
/// from Update on the main thread. Creates itself at startup; no scene wiring.
/// </summary>
public class QuestSpeech : MonoBehaviour
{
    /// <summary>Longest reply we will speak; longer text is cut at a word break.</summary>
    public const int MaxChars = 240;
    const int QueueFlush = 0;   // TextToSpeech.QUEUE_FLUSH: a new reply replaces the old one
    const int Success = 0;      // TextToSpeech.SUCCESS

    static QuestSpeech _instance;

    readonly Queue<string> _pending = new Queue<string>();
    AndroidJavaObject _tts;
    volatile bool _ready;
    volatile bool _failed;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void AutoCreate()
    {
        if (FindAnyObjectByType<QuestSpeech>() == null)
            new GameObject("QuestSpeech").AddComponent<QuestSpeech>();
    }

    /// <summary>Speak a reply, or queue it while the engine starts. Main thread.</summary>
    public static void Speak(string text)
    {
        if (_instance == null || string.IsNullOrEmpty(text))
            return;
        _instance.Enqueue(text);
    }

    public static void Stop()
    {
        if (_instance == null) return;
        _instance._pending.Clear();
        if (_instance._tts == null) return;
        try { _instance._tts.Call<int>("stop"); }
        catch (Exception) { }
    }

    void Awake()
    {
        _instance = this;
        DontDestroyOnLoad(gameObject);
        StartEngine();
    }

    void StartEngine()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        try
        {
            using (var player = new AndroidJavaClass("com.unity3d.player.UnityPlayer"))
            using (var activity = player.GetStatic<AndroidJavaObject>("currentActivity"))
            {
                var listener = new InitListener(status =>
                {
                    // Java thread: only flip flags here.
                    if (status == Success)
                        _ready = true;
                    else
                        _failed = true;
                });
                _tts = new AndroidJavaObject("android.speech.tts.TextToSpeech", activity, listener);
            }
        }
        catch (Exception e)
        {
            _failed = true;
            Debug.LogWarning("QUEST_SPEAK text-to-speech unavailable (" + e.GetType().Name + ")");
        }
#else
        _failed = true;   // editor: nothing to speak with
#endif
    }

    void Enqueue(string text)
    {
        if (text.Length > MaxChars)
        {
            int cut = text.LastIndexOf(' ', MaxChars - 1);
            text = text.Substring(0, cut > 0 ? cut : MaxChars);
        }
        _pending.Enqueue(text);
    }

    void Update()
    {
        if (_pending.Count == 0)
            return;
        if (_failed)
        {
            // Never let replies pile up when we cannot speak them.
            Debug.Log("QUEST_SPEAK (no engine) " + _pending.Dequeue());
            return;
        }
        if (!_ready || _tts == null)
            return;
        if (!_languageSet)
        {
            SetLanguage();
            _languageSet = true;
        }
        while (_pending.Count > 0)
        {
            string text = _pending.Dequeue();
            try
            {
                // API 21+: speak(CharSequence, int queueMode, Bundle params, String utteranceId)
                int result = _tts.Call<int>("speak", text, QueueFlush, null, "omni");
                Debug.Log("QUEST_SPEAK " + (result == Success ? "" : "(failed) ") + text);
            }
            catch (Exception e)
            {
                Debug.LogWarning("QUEST_SPEAK failed (" + e.GetType().Name + ")");
                _failed = true;
                return;
            }
        }
    }

    bool _languageSet;

    void SetLanguage()
    {
        try
        {
            using (var locale = new AndroidJavaObject("java.util.Locale", "en", "US"))
                _tts.Call<int>("setLanguage", locale);
        }
        catch (Exception)
        {
            // Keep the engine default; not worth failing a reply over.
        }
    }

    void OnDestroy()
    {
        if (_instance == this)
            _instance = null;
        if (_tts == null)
            return;
        try
        {
            _tts.Call("stop");
            _tts.Call("shutdown");
        }
        catch (Exception)
        {
        }
        _tts.Dispose();
        _tts = null;
    }

#if UNITY_ANDROID && !UNITY_EDITOR
    /// <summary>Bridges TextToSpeech.OnInitListener back into C#.</summary>
    class InitListener : AndroidJavaProxy
    {
        readonly Action<int> _onInit;

        public InitListener(Action<int> onInit)
            : base("android.speech.tts.TextToSpeech$OnInitListener")
        {
            _onInit = onInit;
        }

        void onInit(int status)
        {
            _onInit(status);
        }
    }
#endif
}
