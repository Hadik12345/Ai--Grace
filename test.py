"""
Deepgram Flux — Real-Time Voice Assistant
==========================================
Features:
  - Real-time microphone streaming to Deepgram Flux via WebSocket
  - Pause / Resume microphone (stops sending audio, keeps WebSocket alive)
  - Graceful connection termination
  - Reconnection (reinitiate) without restarting the script
  - EndOfTurn, EagerEndOfTurn, TurnResumed mock handlers (ready to extend)
  - Interim (partial) + Final transcript display

Requirements:
  pip install deepgram-sdk python-dotenv pyaudio

.env file:
  DEEPGRAM_API_KEY="your_key_here"
"""

import asyncio
import threading
import pyaudio
from dotenv import load_dotenv
from deepgram import AsyncDeepgramClient, LiveOptions
import os

load_dotenv()

# Fallback for API key if not in .env
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    DEEPGRAM_API_KEY = "63a9ae3445f36f599a6c93a1184eec692b7d18d4"

# ──────────────────────────────────────────────
# Audio Configuration

# ──────────────────────────────────────────────
AUDIO_FORMAT     = pyaudio.paInt16   # linear16
CHANNELS         = 1                 # mono
SAMPLE_RATE      = 16000             # Hz
CHUNK_MS         = 80                # recommended by Deepgram for Flux
CHUNK_SIZE       = int(SAMPLE_RATE * CHANNELS * 2 * CHUNK_MS / 1000)
                                     # bytes = sample_rate × channels × 2 bytes × seconds

# ──────────────────────────────────────────────
# Flux / Deepgram Configuration
# ──────────────────────────────────────────────
FLUX_OPTIONS = LiveOptions(
    model              = "flux-general-en",
    encoding           = "linear16",
    sample_rate        = SAMPLE_RATE,
    interim_results    = True,         # stream partial transcripts
)

ADDONS = {
    # End-of-turn detection (adjust to taste)
    "eot_threshold": 0.7,          # 0.5–0.9  | higher = more reliable but slower
    "eager_eot_threshold": 0.5,          # 0.3–0.9  | enables EagerEndOfTurn events
    "eot_timeout_ms": 5000,         # 500–10000 | silence before forcing turn end
}


# ══════════════════════════════════════════════════════════════════════════════
# MOCK TURN-EVENT HANDLERS
# Replace the bodies of these functions with your real LLM / TTS logic later.
# ══════════════════════════════════════════════════════════════════════════════

async def on_end_of_turn(transcript: str) -> None:
    """
    Called when Flux is confident the user has finished speaking.
    This is the primary trigger for sending a complete utterance to your LLM.

    Args:
        transcript: Full text of the completed turn.

    TODO: Replace mock body with real LLM call, e.g.:
        response = await llm.complete(transcript)
        await tts.speak(response)
    """
    print(f"\n[EndOfTurn] ✅ Final turn transcript: '{transcript}'")
    print("  → (mock) Sending to LLM and waiting for response...\n")


async def on_eager_end_of_turn(transcript: str) -> None:
    """
    Called when Flux predicts the user is *likely* finishing (earlier signal).
    Use this to speculatively start your LLM request before the turn fully ends,
    reducing end-to-end latency by hundreds of milliseconds.

    IMPORTANT: The user may still be speaking. If TurnResumed fires afterward,
    cancel the speculative LLM request.

    Args:
        transcript: Partial/predicted final transcript.

    TODO: Replace mock body with speculative LLM prefill logic.
    """
    print(f"\n[EagerEndOfTurn] ⚡ Predicted turn end: '{transcript}'")
    print("  → (mock) Speculatively prefilling LLM prompt...\n")


async def on_turn_resumed(transcript: str) -> None:
    """
    Called when the user continues speaking after an EagerEndOfTurn was fired.
    Cancel any speculative LLM requests triggered by on_eager_end_of_turn.

    Args:
        transcript: Accumulated transcript so far in this resumed turn.

    TODO: Cancel your speculative LLM request here.
    """
    print(f"\n[TurnResumed] 🔄 User continued speaking: '{transcript}'")
    print("  → (mock) Cancelling speculative LLM request...\n")


# ══════════════════════════════════════════════════════════════════════════════
# VOICE ASSISTANT CORE
# ══════════════════════════════════════════════════════════════════════════════

class FluxVoiceAssistant:
    """
    Real-time voice assistant backed by Deepgram Flux.

    Lifecycle:
        assistant = FluxVoiceAssistant()
        await assistant.connect()        # open WebSocket
        await assistant.start_mic()      # start streaming mic audio
        ...
        await assistant.pause_mic()      # stop sending audio (socket stays open)
        await assistant.resume_mic()     # resume sending audio
        ...
        await assistant.stop()           # graceful full shutdown
        await assistant.reconnect()      # tear down and re-establish everything
    """

    def __init__(self):
        self._client        = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)
        self._connection    = None          # Deepgram WebSocket connection
        self._listen_task   = None          # asyncio task running start_listening()

        self._audio         = pyaudio.PyAudio()
        self._stream        = None          # PyAudio input stream
        self._mic_active    = False         # True while mic is open and sending
        self._mic_paused    = False         # True when mic is temporarily paused
        self._mic_thread    = None          # thread running _mic_loop()
        self._stop_event    = threading.Event()   # signals mic thread to exit

        self._current_transcript = ""       # accumulates interim results in a turn

    # ──────────────────────────────────────────
    # Connection Management
    # ──────────────────────────────────────────

    async def connect(self) -> None:
        """Open a new WebSocket connection to Deepgram Flux."""
        if self._connection is not None:
            print("[Assistant] Already connected. Call reconnect() to reset.")
            return

        print("[Assistant] Connecting to Deepgram Flux...")
        self._connection = await self._client.listen.asyncwebsocket.v("2")
        await self._connection.start(FLUX_OPTIONS, addons=ADDONS)

        # Register event callbacks
        self._connection.on(EventType.OPEN,    self._handle_open)
        self._connection.on(EventType.MESSAGE, self._handle_message)
        self._connection.on(EventType.CLOSE,   self._handle_close)
        self._connection.on(EventType.ERROR,   self._handle_error)

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket connection."""
        if self._connection is None:
            return
        print("[Assistant] Disconnecting from Deepgram Flux...")
        try:
            await self._connection.finish()
        except Exception as e:
            print(f"[Assistant] Error during disconnect: {e}")
        finally:
            self._connection  = None
            self._listen_task = None
            print("[Assistant] Disconnected.")

    async def reconnect(self) -> None:
        """
        Tear down the current connection (and mic if running) then re-establish.
        Useful after errors or when you want a clean slate.
        """
        print("[Assistant] Reconnecting...")
        was_mic_active = self._mic_active
        await self.stop_mic()
        await self.disconnect()
        await self.connect()
        if was_mic_active:
            await self.start_mic()

    # ──────────────────────────────────────────
    # Microphone Control
    # ──────────────────────────────────────────

    async def start_mic(self) -> None:
        """Open the microphone and begin streaming audio to Deepgram."""
        if self._mic_active:
            print("[Mic] Already active.")
            return
        if self._connection is None:
            print("[Mic] No active connection. Call connect() first.")
            return

        print("[Mic] 🎙️  Microphone started — speak now...")
        self._stop_event.clear()
        self._mic_paused  = False
        self._mic_active  = True

        # Open PyAudio stream
        self._stream = self._audio.open(
            format            = AUDIO_FORMAT,
            channels          = CHANNELS,
            rate              = SAMPLE_RATE,
            input             = True,
            frames_per_buffer = CHUNK_SIZE,
        )

        # Run the blocking mic read loop in a thread so it doesn't block asyncio
        loop = asyncio.get_event_loop()
        self._mic_thread = threading.Thread(
            target  = self._mic_loop,
            args    = (loop,),
            daemon  = True,
            name    = "mic-thread"
        )
        self._mic_thread.start()

    async def pause_mic(self) -> None:
        """
        Pause sending audio to Deepgram (mic stays open in OS but data is dropped).
        The WebSocket connection remains alive — no billing for silence.
        Call resume_mic() to start sending again.
        """
        if not self._mic_active:
            print("[Mic] Mic is not active.")
            return
        if self._mic_paused:
            print("[Mic] Already paused.")
            return
        self._mic_paused = True
        print("[Mic] ⏸️  Microphone paused — audio not being sent to Deepgram.")

    async def resume_mic(self) -> None:
        """Resume sending microphone audio after a pause."""
        if not self._mic_active:
            print("[Mic] Mic is not active. Call start_mic() first.")
            return
        if not self._mic_paused:
            print("[Mic] Mic is not paused.")
            return
        self._mic_paused = False
        print("[Mic] ▶️  Microphone resumed — streaming to Deepgram.")

    async def stop_mic(self) -> None:
        """Stop the microphone and close the PyAudio stream."""
        if not self._mic_active:
            return
        print("[Mic] Stopping microphone...")
        self._stop_event.set()
        self._mic_active  = False
        self._mic_paused  = False

        if self._mic_thread and self._mic_thread.is_alive():
            self._mic_thread.join(timeout=2)

        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        print("[Mic] 🔇 Microphone stopped.")

    async def stop(self) -> None:
        """Full graceful shutdown: stop mic, close WebSocket, release audio."""
        print("[Assistant] Shutting down...")
        await self.stop_mic()
        await self.disconnect()
        self._audio.terminate()
        print("[Assistant] Shutdown complete.")

    # ──────────────────────────────────────────
    # Internal: Mic Thread
    # ──────────────────────────────────────────

    def _mic_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Blocking loop that reads mic chunks and schedules async sends.
        Runs in a dedicated thread to avoid blocking asyncio.
        """
        while not self._stop_event.is_set():
            try:
                data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception as e:
                print(f"[Mic] Read error: {e}")
                break

            if self._mic_paused or self._connection is None:
                continue   # drop this chunk but keep reading (stays warm)

            # Schedule the coroutine send on the asyncio event loop
            future = asyncio.run_coroutine_threadsafe(
                self._send_audio(data), loop
            )
            try:
                future.result(timeout=1)   # wait briefly to apply back-pressure
            except Exception as e:
                print(f"[Mic] Send error: {e}")

    async def _send_audio(self, data: bytes) -> None:
        """Send a raw PCM chunk to Deepgram over the WebSocket."""
        if self._connection:
            await self._connection.send(data)

    # ──────────────────────────────────────────
    # Internal: Deepgram Event Handlers
    # ──────────────────────────────────────────

    def _handle_open(self, _) -> None:
        print("[Deepgram] ✅ WebSocket connection opened.")

    def _handle_close(self, _) -> None:
        print("[Deepgram] 🔌 WebSocket connection closed.")

    def _handle_error(self, error) -> None:
        print(f"[Deepgram] ❌ Error: {error}")

    def _handle_message(self, message) -> None:
        """
        Route incoming Deepgram messages to the appropriate handler.
        Message types from Flux: Transcript, EndOfTurn, EagerEndOfTurn, TurnResumed
        """
        msg_type = getattr(message, "type", None)

        if msg_type == "Transcript":
            self._process_transcript(message)

        elif msg_type == "EndOfTurn":
            transcript = getattr(message, "transcript", self._current_transcript)
            self._current_transcript = ""   # reset accumulator
            asyncio.run_coroutine_threadsafe(
                on_end_of_turn(transcript),
                asyncio.get_event_loop()
            )

        elif msg_type == "EagerEndOfTurn":
            transcript = getattr(message, "transcript", self._current_transcript)
            asyncio.run_coroutine_threadsafe(
                on_eager_end_of_turn(transcript),
                asyncio.get_event_loop()
            )

        elif msg_type == "TurnResumed":
            transcript = getattr(message, "transcript", self._current_transcript)
            asyncio.run_coroutine_threadsafe(
                on_turn_resumed(transcript),
                asyncio.get_event_loop()
            )

        elif msg_type == "Connected":
            print("[Deepgram] 🤝 Handshake complete — Flux model ready.")

    def _process_transcript(self, message) -> None:
        """Handle a real-time transcript chunk (interim or final)."""
        transcript = getattr(message, "transcript", "")
        is_final   = getattr(message, "is_final", False)

        if not transcript:
            return

        if is_final:
            self._current_transcript += (" " + transcript) if self._current_transcript else transcript
            print(f"\r[Transcript] ✔ {self._current_transcript}          ", flush=True)
        else:
            # Interim: overwrite the current line for a streaming feel
            preview = (self._current_transcript + " " + transcript).strip()
            print(f"\r[Transcript] … {preview}", end="", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# INTERACTIVE CLI (demo / testing harness)
# ══════════════════════════════════════════════════════════════════════════════

async def cli_loop(assistant: FluxVoiceAssistant) -> None:
    """Simple command-line interface to exercise all assistant controls."""
    print("\nCommands: start | pause | resume | stop_mic | reconnect | quit\n")

    loop = asyncio.get_event_loop()

    def read_input():
        return input(">>> ").strip().lower()

    while True:
        # Read input in a thread-pool to avoid blocking the event loop
        cmd = await loop.run_in_executor(None, read_input)

        if cmd == "start":
            await assistant.start_mic()

        elif cmd == "pause":
            await assistant.pause_mic()

        elif cmd == "resume":
            await assistant.resume_mic()

        elif cmd == "stop_mic":
            await assistant.stop_mic()

        elif cmd == "reconnect":
            await assistant.reconnect()

        elif cmd in ("quit", "exit", "q"):
            await assistant.stop()
            break

        else:
            print("Unknown command. Try: start | pause | resume | stop_mic | reconnect | quit")


async def main() -> None:
    assistant = FluxVoiceAssistant()
    await assistant.connect()
    await cli_loop(assistant)


if __name__ == "__main__":
    asyncio.run(main())