# Member C: Voice Input and Spoken Response Pipeline

This folder contains the voice interaction tests for the OMNI Live Quest project.

## Deliverable

The `test_voice_pipeline.py` script demonstrates a complete audio -> model -> spoken answer pipeline:
1. Records/synthesizes a `.wav` file saying "Mark the laptop and explain what it does."
2. Sends the raw audio to the yibuapi `qwen3.8-omni-flash` model.
3. Validates that the request contains audio bytes.
4. Receives the text answer from the model and measures the latency.
5. Uses a separate local TTS stage (Windows PowerShell SpeechSynthesizer) to speak the response aloud.

## Exact Model Used

- **Model:** `qwen3.8-omni-flash` (via HTTP chat completions)
- **Reason:** As documented, this model accepts multimodal input (audio/video/text) but produces text only. The script uses a separate TTS stage for voice output.

## Command Used

To test the pipeline, run:

```bash
python test_voice_pipeline.py
```

The script will automatically detect if `input.wav` exists. If not, it generates the test phrase using Windows Speech API. It communicates with the upstream model and speaks the result aloud.
