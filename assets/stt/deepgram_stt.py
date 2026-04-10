
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT
import os
from dotenv import load_dotenv
from time import sleep
import logging
import threading # Import threading for the input loop

#-----local imports-----


from deepgram.utils import verboselogs

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
)

load_dotenv()

# --- State Variables ---
# We will collect the is_final=true messages here
is_finals = []
# Control flag for sending audio data
is_sending = True
# Lock for thread-safe access to is_sending
sending_lock = threading.Lock()
# Flag to signal the input loop to stop
stop_event = threading.Event()
# --- /State Variables ---
llm_processing = False # Flag to indicate if LLM processing is in progress

# --- Microphone Data Handling ---
# This wrapper function checks the is_sending flag before forwarding data
def handle_microphone_data(chunk, dg_connection):
    global is_sending
    with sending_lock:
        if is_sending:
            dg_connection.send(chunk)
        # else:
            # Optional: Log or print that data is being withheld
            # print(" Mic Paused - Data withheld")
            # pass


# --- Control Functions ---
def pause_sending():
    """Stops sending audio data to Deepgram."""
    global is_sending
    with sending_lock:
        if is_sending:
            print("\n--- Pausing audio transmission ---")
            is_sending = False

def resume_sending():
    """Resumes sending audio data to Deepgram."""
    global is_sending
    with sending_lock:
        if not is_sending:
            print("\n--- Resuming audio transmission ---")
            is_sending = True

def stop_application():
    """Signals the application to stop."""
    print("\n--- Stopping application ---")
    stop_event.set() # Signal the input loop to exit
# --- /Control Functions ---


# --- Input Handling Thread ---
def handle_input(microphone, dg_connection):
    """Handles user input in a separate thread."""
    print("\n\n--- Controls ---")
    print("Press 'p' then Enter to pause sending audio.")
    print("Press 'r' then Enter to resume sending audio.")
    print("Press 'q' then Enter to quit.")
    print("----------------\n")
    while not stop_event.is_set():
        try:
            command = input("Enter command (p/r/q): ").strip().lower()
            if command == 'p':
                pause_sending()
            elif command == 'r':
                resume_sending()
            elif command == 'q':
                stop_application()
                break # Exit loop immediately on quit command
            else:
                print("Unknown command.")
        except EOFError: # Handle case where input stream is closed
            stop_application()
            break
        except Exception as e:
            print(f"Error in input thread: {e}")
            stop_application()
            break

    # Ensure microphone and connection are cleaned up when loop exits
    print("Input loop finished. Cleaning up...")
    if microphone:
        microphone.finish()
    if dg_connection:
        dg_connection.finish()

# --- /Input Handling Thread ---


def main():
    global is_finals, is_sending,llm_processing
    dg_connection = None
    microphone = None
    input_thread = None

    try:
        # --- Configure Deepgram Client with Keepalive ---
        # Load API Key from environment variable
        DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
        if not DEEPGRAM_API_KEY:
             # Fallback to the hardcoded key if the environment variable isn't set
             # WARNING: Hardcoding keys is not recommended for production.
             print("Warning: DEEPGRAM_API_KEY environment variable not set. Using hardcoded key.")
             DEEPGRAM_API_KEY = "63a9ae3445f36f599a6c93a1184eec692b7d18d4" # Replace or remove if using env var

        if not DEEPGRAM_API_KEY:
             print("Error: Deepgram API Key not found. Set DEEPGRAM_API_KEY environment variable or hardcode it.")
             return

        # Set up client configuration with keepalive enabled
        # The keepalive option sends pings internally to prevent timeouts during pauses.
        config: DeepgramClientOptions = DeepgramClientOptions(
            options={"keepalive": "true"}
        )

        # Initialize the Deepgram client with the API key and config
        deepgram: DeepgramClient = DeepgramClient(DEEPGRAM_API_KEY, config)
        # --- /Configure Deepgram Client ---


        dg_connection = deepgram.listen.websocket.v("2")

        # --- Event Handlers (on_open, on_message, etc.) ---
        # (Your existing event handlers remain the same)
        def on_open(self, open, **kwargs):
            print("\n--- Connection Open ---")
            # Add a note about keepalive being active
            print("Keepalive enabled: Connection should stay open during pauses.")

        def on_message(self, result, **kwargs):
            global is_finals
            # Check if the result has the expected structure
            if not hasattr(result, 'channel') or not hasattr(result.channel, 'alternatives') or not result.channel.alternatives:
                # Check for KeepAlive message (optional, for debugging)
                if hasattr(result, 'type') and result.type == 'KeepAlive':
                     print("(KeepAlive message received from Deepgram)")
                     return
                # Also ignore EagerEndOfTurn, TurnResumed, EndOfTurn explicitly to avoid spam
                if hasattr(result, 'type') and result.type in ['EagerEndOfTurn', 'TurnResumed', 'EndOfTurn', 'SpeechStarted']:
                     return
                print(f"Warning: Received unexpected message format: {result.to_json()}")
                return

            sentence = result.channel.alternatives[0].transcript
            if len(sentence) == 0:
                return

            if result.is_final:
                is_finals.append(sentence)
                if result.speech_final:
                    utterance = " ".join(is_finals)
                    print(f"Speech Final: {utterance}")
                    is_finals = []
                    pause_sending()  # Pause sending audio after final result
                    llm_processing = True # Set flag to indicate LLM processing is in progress
                    brain.main(utterance)  # Call the main function from brain.py with the final result
                    llm_processing = False
                else:
                    print(f"Is Final: {sentence}")
            else:
                print(f"Interim Results: {sentence}")

        def on_metadata(self, metadata, **kwargs):
            print(f"Metadata: {metadata}")

        def on_speech_started(self, speech_started, **kwargs):
            print("--- Speech Started ---")

        def on_eager_eot(self, eager_eot, **kwargs):
            print("--- Eager End of Turn Detected ---")
            # We could start preprocessing / prompting the LLM early here 

        def on_turn_resumed(self, turn_resumed, **kwargs):
            print("--- Turn Resumed (user continued speaking) ---")
            # We would cancel our early LLM request here

        def on_end_of_turn(self, eot, **kwargs):
            print("--- End Of Turn ---")
            global is_finals
            if len(is_finals) > 0:
                utterance = " ".join(is_finals)
                print(f"End of Turn Transcript: {utterance}")
                is_finals = []
                pause_sending()
                llm_processing = True # Set flag to indicate LLM processing is in progress
                # Note: Assuming brain is imported somewhere 
                brain.main(utterance)
                llm_processing = False # Reset flag after processing
            # Pause sending audio after end of turn

        def on_close(self, close, **kwargs):
            print(f"\n--- Connection Closed (Code: {close.code}, Reason: {close.reason}) ---")
            stop_event.set()

        def on_error(self, error, **kwargs):
            print(f"Handled Error: {error}")
            stop_event.set()

        def on_unhandled(self, unhandled, **kwargs):
            print(f"Unhandled Websocket Message: {unhandled}")

        dg_connection.on(LiveTranscriptionEvents.Open, on_open)
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
        
        # New Events for Flux Turn-taking
        dg_connection.on(LiveTranscriptionEvents.EagerEndOfTurn, on_eager_eot)
        dg_connection.on(LiveTranscriptionEvents.TurnResumed, on_turn_resumed)
        dg_connection.on(LiveTranscriptionEvents.EndOfTurn, on_end_of_turn)

        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)
        # --- /Event Handlers ---


        # --- Live Transcription Options ---
        options: LiveOptions = LiveOptions(
            model="flux-general-en",
            smart_format=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
        )

        addons = {
            "eot_threshold": 0.7,
            "eager_eot_threshold": 0.5,
            "eot_timeout_ms": 5000,
        }
        # --- /Live Transcription Options ---


        # --- Start Connection and Microphone ---
        print("Attempting to connect to Deepgram...")
        if dg_connection.start(options, addons=addons) is False:
            print("Failed to connect to Deepgram")
            return
        print("Connection successful.")

        microphone = Microphone(lambda chunk: handle_microphone_data(chunk, dg_connection))
        microphone.start()
        print("Microphone started.")
        pause_sending()  # Start with sending paused
        while not stop_event.is_set():
            sleep(0.1)

        # --- /Start Connection and Microphone ---


        # --- Wait for Stop Signal ---

    except Exception as e:
        print(f"An unexpected error occurred in main: {e}")
        import traceback
        traceback.print_exc() # Print detailed traceback for debugging
        stop_event.set() # Ensure stop event is set in case of exception

    finally:
        # --- Cleanup ---
        print("Cleaning up resources...")

        # Stop the input thread first if it's still running
        if input_thread and input_thread.is_alive():
             print("Signaling input thread to stop...")
             # No explicit stop needed for daemon thread if stop_event is set,
             # but joining is good practice.
             input_thread.join(timeout=1.0) # Wait briefly for it
             if input_thread.is_alive():
                  print("Warning: Input thread did not exit cleanly.")

        # Finish microphone
        if microphone:
            print("Stopping microphone...")
            microphone.finish()
            print("Microphone finished.")
        else:
            print("Microphone object not found or already cleaned up.")

        # Finish Deepgram connection
        if dg_connection:
            print("Closing Deepgram connection...")
            dg_connection.finish()
            print("Deepgram connection finished.")
        else:
            print("Deepgram connection object not found or already cleaned up.")

        print("Cleanup complete. Exiting.")
        # --- /Cleanup ---


if __name__ == "__main__":
    microphone = None
    dg_connection = None
    input_thread = threading.Thread(target=handle_input, args=(microphone, dg_connection))
    input_thread.daemon = True # Make input thread a daemon so it exits if main thread exits
    input_thread.start()
    main()
