@"
# AGENTS.md

## Project rules

This is a Windows-based Python SRTP voice interaction project.

Please follow these rules:

1. Keep the project runnable on Windows PowerShell.
2. Do not commit .env, API keys, venv folders, audio cache files, or large model weights.
3. Prefer minimal, high-confidence changes.
4. Do not rewrite the whole project unless explicitly asked.
5. Keep module boundaries clear:
   - microphone input
   - speech detection
   - ASR placeholder
   - LLM response
   - TTS playback
   - serial communication
6. When modifying audio code, always check:
   - file format
   - sample rate
   - channel count
   - dtype
   - whether mp3/wav/pcm are being mixed incorrectly
7. When adding dependencies, update requirements.txt.
8. Give Windows PowerShell commands for testing.
"@ | Set-Content AGENTS.md -Encoding UTF8