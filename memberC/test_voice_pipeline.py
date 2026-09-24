import sys
import os
import time
import subprocess
from pathlib import Path

# Add the provider folder to the Python path to import yibu_http
provider_path = Path(__file__).parent.parent / "provider"
sys.path.append(str(provider_path.absolute()))

try:
    from yibu_http import chat_completion, build_omni_messages, require_api_key
except ImportError as e:
    print(f"Error importing yibu_http: {e}")
    print(f"Make sure {provider_path} exists and contains yibu_http.py")
    sys.exit(1)

def synthesize_text_to_wav_powershell(text: str, output_path: str):
    """Generates a WAV file from text using Windows PowerShell."""
    script = f"""
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SetOutputToWaveFile('{output_path}')
    $synth.Speak('{text}')
    $synth.Dispose()
    """
    subprocess.run(["powershell", "-Command", script], check=True)

def speak_text_powershell(text: str):
    """Speaks text out loud using Windows PowerShell."""
    # Clean up quotes for powershell execution
    clean_text = text.replace("'", "''").replace('"', '""').replace('`', '``')
    script = f"""
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.Speak('{clean_text}')
    $synth.Dispose()
    """
    subprocess.run(["powershell", "-Command", script], check=True)

def main():
    try:
        api_key = require_api_key()
    except Exception as e:
        print(f"Error getting API key: {e}")
        print("Please ensure YIBU_API_KEY environment variable is set.")
        return

    # Documented in AGENTS.md / omni-live-research.md as multimodal text-output model
    model = "qwen3.8-omni-flash"
    purpose = "member-c-voice-test"
    
    input_wav = Path(__file__).parent / "input.wav"
    
    # 1. Prepare the audio request
    if not input_wav.exists():
        print(f"Generating input audio: {input_wav.absolute()}")
        try:
            synthesize_text_to_wav_powershell("Mark the laptop and explain what it does.", str(input_wav.absolute()))
        except Exception as e:
            print(f"Failed to generate wav file: {e}")
            return
            
    print(f"File {input_wav} exists, size: {input_wav.stat().st_size} bytes")
    
    # 2. Feed the actual recorded audio
    # Build messages using the upstream implementation which guarantees correct shape
    messages = build_omni_messages(
        prompt="Please fulfill the spoken request in the audio. DO NOT use any markdown formatting (like asterisks, hashes, or bullet points) in your response. Output plain text only so it can be read cleanly by a text-to-speech engine.",
        audio=input_wav
    )
    
    # 3. Confirm that the request contains audio bytes
    has_audio = any('input_audio' in block for block in messages[0]['content'])
    print(f"Verified request contains audio bytes: {has_audio}")
    
    print(f"Sending request to {model}...")
    start_time = time.time()
    
    # 4. Test obtaining a short answer
    try:
        text, response_json, record = chat_completion(
            api_key=api_key,
            model=model,
            messages=messages,
            purpose=purpose,
            max_tokens=100
        )
    except Exception as e:
        print(f"API request failed: {e}")
        return
        
    # 5. Measure latency
    latency = time.time() - start_time
    print(f"Response received in {latency:.2f} seconds.")
    print(f"Tokens: In={record.get('input_tokens')}, Out={record.get('output_tokens')}")
    print(f"Model Answer: {text}")
    
    # 6. Convert to audible speech using an available TTS path
    print("Synthesizing audible response...")
    try:
        speak_text_powershell(text)
    except Exception as e:
        print(f"Failed to play text: {e}")
    
    print("Pipeline complete.")

if __name__ == "__main__":
    main()
