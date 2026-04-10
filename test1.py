# ============================================================
#  stt.py — Real-Time Voice Assistant using Deepgram Flux
#  Model   : flux-general-en  (v2 endpoint)
#  Features: EagerEndOfTurn · EndOfTurn · TurnResumed
#            Pause / Resume mic · Graceful terminate · Reconnect
# ============================================================

import asyncio
import os
import threading
import pyaudio
from dotenv import load_dotenv

# ----- Local imports --  # Your LLM brain module — replace mock calls below with real ones

from deepgram import AsyncDeepgramClient, DeepgramClientEnvironment
from deepgram.core.events import EventType

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

# Flux end-of-turn detection parameters (tweak to suit your agent)
FLUX_CONFIG = {
    "model":               "flux-general-en",
    "encoding":            "linear16",
    "sample_rate":         "16000",
    # Higher = more reliable EOT, slightly more latency (range 0.5-0.9)
    "eot_threshold":       "0.7",
    # Enable EagerEndOfTurn by setting a value (range 0.3-0.9)
    # Comment out this line entirely to disable EagerEndOfTurn
    "eager_eot_threshold": "0.5",
    # Max silence (ms) before forcing an EndOfTurn regardless of confidence
    "eot_timeout_ms":      "5000",
}

# Microphone config — 80 ms chunks @ 16 kHz linear16 (Deepgram recommended)
MIC_SAMPLE_RATE  = 16000
MIC_CHANNELS     = 1
MIC_FORMAT       = pyaudio.paInt16  # linear16
MIC_CHUNK_FRAMES = 1280             # 80 ms of frames: 16000 * 0.08


# ============================================================
# SHARED STATE
# ============================================================

is_sending:       bool      = True   # Whether mic audio is forwarded to Deepgram
llm_processing:   bool      = False  # Whether the LLM is currently running
transcript_parts: list      = []     # Accumulates final transcript pieces per turn

sending_lock = threading.Lock()   # Guards is_sending across threads
thread_stop  = threading.Event()  # Signals the keyboard input thread to exit

# Assigned in _async_main() so background threads can safely post coroutines
_loop       = None   # asyncio.AbstractEventLoop
_stop_event = None   # asyncio.Event — stops the asyncio session loop

# Thread-safe bridge: mic thread enqueues, asyncio send loop dequeues
_audio_queue = asyncio.Queue()


# ============================================================
# MICROPHONE  (PyAudio, runs on a daemon thread)
# ============================================================

_mic_thread_stop = threading.Event()


def _mic_thread_fn():
    """
    Captures audio from the default microphone in 80 ms chunks via PyAudio
    and enqueues them into _audio_queue for the asyncio send loop.
    Chunks are silently dropped while is_sending is False (paused).
    """
    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format=MIC_FORMAT,
        channels=MIC_CHANNELS,
        rate=MIC_SAMPLE_RATE,
        input=True,
        frames_per_buffer=MIC_CHUNK_FRAMES,
    )
    print("[MIC] PyAudio stream opened.")
    try:
        while not _mic_thread_stop.is_set():
            chunk = stream.read(MIC_CHUNK_FRAMES, exception_on_overflow=False)
            with sending_lock:
                if is_sending and _loop:
                    asyncio.run_coroutine_threadsafe(_audio_queue.put(chunk), _loop)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        print("[MIC] PyAudio stream closed.")


def start_microphone():
    """Start the mic capture thread and return it."""
    _mic_thread_stop.clear()
    t = threading.Thread(target=_mic_thread_fn, daemon=True, name="mic-thread")
    t.start()
    return t


def stop_microphone():
    """Signal the mic capture thread to stop."""
    _mic_thread_stop.set()


# ============================================================
# CONTROL FUNCTIONS  (thread-safe, callable from anywhere)
# ============================================================

def pause_sending():
    """Stop forwarding mic audio to Deepgram. Keepalive keeps the socket alive."""
    global is_sending
    with sending_lock:
        if is_sending:
            print("\n[MIC] Audio transmission paused.")
            is_sending = False


def resume_sending():
    """Resume forwarding mic audio to Deepgram."""
    global is_sending
    with sending_lock:
        if not is_sending:
            print("\n[MIC] Audio transmission resumed.")
            is_sending = True


def stop_application():
    """Gracefully shut down the entire application."""
    print("\n[APP] Shutdown requested...")
    thread_stop.set()
    if _loop and _stop_event:
        _loop.call_soon_threadsafe(_stop_event.set)


# ============================================================
# MOCK TURN-EVENT HANDLERS
# Replace the bodies of these three functions with your
# real LLM / TTS pipeline logic when ready.
# ============================================================

def on_end_of_turn(transcript):
    """
    MOCK — Fired when Flux emits a confirmed EndOfTurn event.

    The user has definitively finished their turn.
    This is the right place to send the completed utterance to your LLM.

    Args:
        transcript: Full turn text assembled from streamed finals.
    """
    print(f"\n[EOT] End of Turn -> \"{transcript}\"")
    print("[EOT] TODO: call brain.main(transcript) here.")
    # Uncomment when ready:
    # pause_sending()
    # brain.main(transcript)
    # resume_sending()


def on_eager_end_of_turn(transcript):
    """
    MOCK — Fired when Flux emits an EagerEndOfTurn event.

    The model is fairly confident the user is done, but not certain yet.
    Start your LLM call speculatively to reduce end-to-end latency.
    If on_turn_resumed() fires next, cancel that speculative response.

    NOTE: EagerEndOfTurn can increase LLM API calls by 50-70% due to
    speculative generation. Always pair with on_turn_resumed() to cancel.

    Args:
        transcript: Transcript text collected so far in this turn.
    """
    print(f"\n[EAGER EOT] Speculative End of Turn -> \"{transcript}\"")
    print("[EAGER EOT] TODO: Start speculative LLM call (ready to cancel).")
    # Uncomment when ready:
    # asyncio.create_task(brain.speculative_response(transcript))


def on_turn_resumed(partial_transcript):
    """
    MOCK — Fired when Flux emits a TurnResumed event.

    The user continued speaking after an EagerEndOfTurn was fired.
    Cancel any in-flight speculative LLM response immediately.

    Args:
        partial_transcript: Text transcribed before the user resumed speaking.
    """
    print(f"\n[TURN RESUMED] User kept speaking - cancelling draft response.")
    print("[TURN RESUMED] TODO: Cancel speculative LLM call.")
    # Uncomment when ready:
    # brain.cancel_speculative_response()


# ============================================================
# FLUX EVENT DISPATCHER
# All v2 messages arrive via EventType.MESSAGE; we route by message.type
# ============================================================

def _dispatch_message(message):
    """Route every incoming Flux WebSocket message to the right handler."""
    global transcript_parts

    msg_type = getattr(message, "type", "Unknown")

    # Live transcript (interim + final pieces)
    if msg_type == "Results":
        if not (hasattr(message, "channel") and message.channel.alternatives):
            return
        text = message.channel.alternatives[0].transcript
        if not text:
            return
        if getattr(message, "is_final", False):
            transcript_parts.append(text)
            print(f"\n[TRANSCRIPT] {text}")
        else:
            print(f"\r[INTERIM]    {text:<60}", end="", flush=True)

    # Confirmed end of turn
    elif msg_type == "EndOfTurn":
        full_turn = " ".join(transcript_parts).strip()
        transcript_parts = []
        if full_turn:
            on_end_of_turn(full_turn)

    # Speculative end of turn — start LLM early
    elif msg_type == "EagerEndOfTurn":
        speculative = " ".join(transcript_parts).strip()
        on_eager_end_of_turn(speculative)

    # User resumed speaking — cancel speculative response
    elif msg_type == "TurnResumed":
        partial = " ".join(transcript_parts).strip()
        on_turn_resumed(partial)

    # Connection confirmed
    elif msg_type == "Connected":
        print("[DG] Connected to Deepgram Flux.")

    # Metadata
    elif msg_type == "Metadata":
        print(f"[DG] Metadata: {message}")

    # TurnInfo (internal state/heartbeat, safe to ignore)
    elif msg_type == "TurnInfo":
        pass

    # Unknown
    else:
        print(f"[DG] Unhandled message type: {msg_type}")


# ============================================================
# AUDIO SEND LOOP
# Drains _audio_queue and sends each chunk to Deepgram over WebSocket
# ============================================================

async def _audio_send_loop(connection):
    """Forward audio chunks from the queue to the Flux WebSocket connection."""
    while not _stop_event.is_set():
        try:
            chunk = await asyncio.wait_for(_audio_queue.get(), timeout=0.1)
            await connection._send(chunk)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[SEND LOOP] Error: {e}")
            break


# ============================================================
# FLUX SESSION
# One complete connect -> stream -> disconnect lifecycle
# ============================================================

async def _run_flux_session():
    """
    Open a Deepgram Flux v2 WebSocket session and stream until
    _stop_event is set or the connection closes.
    Call this again (or use reconnect()) to open a fresh session.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("[ERROR] DEEPGRAM_API_KEY not set. Add it to your .env file.")
        _stop_event.set()
        return

    config = {"keepalive": "true"}
    client = AsyncDeepgramClient(api_key=api_key)

    print("[DG] Connecting to Deepgram Flux (v2 endpoint)...")

    async with client.listen.v2.connect(**FLUX_CONFIG) as connection:

        # Register event handlers
        connection.on(EventType.OPEN,
                      lambda _: print("[DG] WebSocket open."))
        connection.on(EventType.MESSAGE,
                      lambda msg: _dispatch_message(msg))
        connection.on(EventType.CLOSE,
                      lambda ev: (print(f"[DG] Connection closed: {ev}"),
                                  _loop.call_soon_threadsafe(_stop_event.set)))
        connection.on(EventType.ERROR,
                      lambda err: (print(f"[DG] Connection error: {err}"),
                                   _loop.call_soon_threadsafe(_stop_event.set)))

        # Start background WebSocket listener and audio forwarding
        listener_task = asyncio.create_task(connection.start_listening())
        send_task     = asyncio.create_task(_audio_send_loop(connection))

        # Block here until shutdown is requested
        await _stop_event.wait()

        print("[DG] Closing session...")
        send_task.cancel()
        listener_task.cancel()
        await asyncio.gather(send_task, listener_task, return_exceptions=True)
        print("[DG] Session closed.")


# ============================================================
# RECONNECT HELPER
# ============================================================

async def reconnect():
    """
    Gracefully close the current Flux session and immediately open a fresh one.
    Safe to call mid-stream. The microphone stays running throughout.
    """
    global transcript_parts
    print("[APP] Reconnecting...")
    transcript_parts = []

    _stop_event.set()          # Tear down current session
    await asyncio.sleep(0.5)   # Brief wait for teardown to complete
    _stop_event.clear()        # Re-arm for the new session

    await _run_flux_session()


# ============================================================
# KEYBOARD INPUT THREAD
# ============================================================

def _input_thread():
    """Reads keyboard commands on a daemon thread alongside the asyncio loop."""
    print("\n" + "=" * 44)
    print("  CONTROLS")
    print("  p  ->  Pause  microphone")
    print("  r  ->  Resume microphone")
    print("  x  ->  Reconnect to Deepgram")
    print("  q  ->  Quit")
    print("=" * 44 + "\n")

    while not thread_stop.is_set():
        try:
            cmd = input("cmd> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            stop_application()
            break

        if cmd == "p":
            pause_sending()
        elif cmd == "r":
            resume_sending()
        elif cmd == "x":
            print("[APP] Scheduling reconnect...")
            if _loop:
                asyncio.run_coroutine_threadsafe(reconnect(), _loop)
        elif cmd == "q":
            stop_application()
            break
        else:
            print("Unknown command. Use  p / r / x / q")


# ============================================================
# ENTRY POINT
# ============================================================

async def _async_main():
    global _loop, _stop_event

    _loop       = asyncio.get_running_loop()
    _stop_event = asyncio.Event()

    # Start PyAudio mic on a background daemon thread
    mic_thread = start_microphone()
    print("[MIC] Microphone started.")

    # Start paused — press 'r' to begin streaming to Deepgram
    pause_sending()

    try:
        await _run_flux_session()
    finally:
        print("[MIC] Stopping microphone...")
        stop_microphone()
        mic_thread.join(timeout=2.0)
        print("[APP] Done.")


if __name__ == "__main__":
    # Keyboard input runs on a daemon thread alongside the event loop
    t = threading.Thread(target=_input_thread, daemon=True, name="input-thread")
    t.start()

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        print("\n[APP] Interrupted by user.")