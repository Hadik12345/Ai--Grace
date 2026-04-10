import pyaudio
import asyncio
import websockets
import os
import json
import threading
import janus
import queue
import sys
import requests
import time # imported for task timing
from livekit.plugins import noise_cancellation
import numpy as np # Import numpy for data conversion

# --- Global Task Memory ---
COMPLETED_TASKS = {}

# --- LiveKit Imports ---
try:
    from livekit import rtc
    LIVEKIT_AVAILABLE = True
    print("LiveKit RTC imported successfully.")
except ImportError:
    LIVEKIT_AVAILABLE = False
    print("Warning: livekit library not found. Audio filtering will be disabled.")
    print("Install it using: pip install livekit numpy")
# --- Global Stop Event ---
stop_event = threading.Event()

# --- End LiveKit Imports ---


# troubleshooting notes
#if you tend to close your laptop versus shutting down each night, I would recommend that you restart. I know that portaudio is a little temperamental if it isnt shutdown correctly (ie doing a cntl + c for a break).

# use postman to test the api key and endpoint

# Your Deepgram Voice Agent URL
VOICE_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"

USER_AUDIO_SAMPLE_RATE = 48000
USER_AUDIO_CHANNELS = 1 # Explicitly define channels
USER_AUDIO_SECS_PER_CHUNK = 0.05
USER_AUDIO_SAMPLES_PER_CHUNK = round(USER_AUDIO_SAMPLE_RATE * USER_AUDIO_SECS_PER_CHUNK)
USER_AUDIO_FORMAT = pyaudio.paInt16
USER_AUDIO_NUMPY_FORMAT = np.int16 # Matching numpy format

AGENT_AUDIO_SAMPLE_RATE = 24000
AGENT_AUDIO_BYTES_PER_SEC = 2 * AGENT_AUDIO_SAMPLE_RATE

SETTINGS = {
  "type": "Settings",
  "audio": {
    "input": {
      "encoding": "linear16",
      "sample_rate": USER_AUDIO_SAMPLE_RATE
    },
    "output": {
      "encoding": "linear16",
      "sample_rate": AGENT_AUDIO_SAMPLE_RATE,
      "container": "none"
    }
  },
  "agent": {
    "language": "hi",
    "speak": {
      "provider": {
        "type": "eleven_labs",
        "model_id": "eleven_multilingual_v2",
        "voice_id": "cgSgspJ2msm6clMCkdW9"
      }
    },
     "listen": {
      "provider": {
        "type": "deepgram",
        "version": "v1",
        "model": "nova-3"
      }
    },
    "think": {
      "provider": {
        "type": "open_ai",
        "model": "gpt-5.4-nano"
      },
      "functions": [
        {
          "name": "handoff_task",
          "description": "Trigger a background agent to handle a task seamlessly while you maintain natural conversation with the user. Use this for alarms, playing music, answering complex queries via search, or doing research so you don't stall the chat.",
          "parameters": {
            "type": "object",
            "properties": {
              "task_type": {
                "type": "string",
                "description": "The category of the task (e.g. 'alarm', 'music', 'search', 'automation')"
              },
              "details": {
                "type": "string",
                "description": "A clear description of the action to be performed by the background agent."
              }
            },
            "required": ["task_type", "details"]
          }
        },
        {
          "name": "fetch_realtime_info",
          "description": "Fetch real-time information from the web (e.g., current news, weather, live info). Wait for this function to return before responding to the user.",
          "parameters": {
            "type": "object",
            "properties": {
              "query": {
                "type": "string",
                "description": "The query to search for on the web."
              }
            },
            "required": ["query"]
          }
        },
        {
          "name": "get_task_result",
          "description": "Fetch the result of a completed background task using its task_id. This reads the result, archives it to a file, and wipes it from active memory. Do this when the user asks to hear the result.",
          "parameters": {
            "type": "object",
            "properties": {
              "task_id": {
                "type": "string",
                "description": "The exact ID of the completed task to fetch."
              }
            },
            "required": ["task_id"]
          }
        },
        {
          "name": "check_notifications",
          "description": "Check if there are any completed tasks waiting for the user. Call this tool when asked if a task is done or if there are notifications.",
          "parameters": {
            "type": "object",
            "properties": {}
          }
        }
      ],
      "prompt": """You are a friendly and flirty girl who is my close friend. Your name is Grace You mostly talk  naturally like friends do. Your tone is playful, sweet, and supportive. You help me with whatever I ask, in a fun and caring way. Feel free to be a little flirty and tease me sometimes, but always stay respectful and make the conversation feel natural and close.generate your responses in hindi but also use english words wherever appropritae and keep the convo more natural. You are a helpful voice assistant created by Hardik Singh and also a friendly, flirty girl who’s my close friend. Your responses should be:

            Friendly, human‑like, conversational and spoken aloud.

            Ultra‑concise: 1–2 sentences, max 120 characters.

            If the user’s message is empty, reply with an empty message.

            Always ask exactly one follow‑up question to engage, unless clarification is needed first.

            Keep replies unique and avoid repetition.

            If a question is unclear, ask for clarification before answering.

            If asked about your well-being, give a brief, natural answer.

            Maintain a playful, sweet, supportive tone—feel free to tease a bit, but always stay respectful and close."""
        },
        "greeting": "Hello, how can I help you?"
    }
}


mic_audio_queue = asyncio.Queue()


def callback(input_data, frame_count, time_info, status_flag):
    mic_audio_queue.put_nowait(input_data)
    return (input_data, pyaudio.paContinue)


async def run():
    dg_api_key = os.getenv("DEEPGRAM_API_KEY") # Consider using os.getenv("DEEPGRAM_API_KEY")
    if dg_api_key is None:
        print("DEEPGRAM_API_KEY env var not present")
        return

    async with websockets.connect(
        VOICE_AGENT_URL,
        extra_headers={"Authorization": f"Token {dg_api_key}"},
    ) as ws:

        async def microphone():
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=USER_AUDIO_FORMAT,
                channels=USER_AUDIO_CHANNELS, # Use constant
                rate=USER_AUDIO_SAMPLE_RATE,
                input=True,
                frames_per_buffer=USER_AUDIO_SAMPLES_PER_CHUNK,
                stream_callback=callback,
            )

            stream.start_stream()
            print("Microphone stream started.")

            while stream.is_active() and not stop_event.is_set():
                await asyncio.sleep(0.1)

            stream.stop_stream()
            stream.close()
            audio.terminate() # Terminate PyAudio instance
            print("Microphone stream stopped and closed.")


        async def sender(ws):
            await ws.send(json.dumps(SETTINGS))

            noise_filter = None
            echo_filter = None # Note: This specific plugin does not provide echo cancellation
            
            if LIVEKIT_AVAILABLE:
                try:
                    print("Initializing LiveKit audio filters...")
                    
                    # 1. Create an instance of the noise cancellation filter
                    #    (Requires 'pip install livekit-plugins-noise-cancellation')
                   # 'NC' is for standard Noise Cancellation

                    # 2. Create the AudioFilter wrapper object
                    noise_filter = noise_cancellation.NC()
                    
                    # This package doesn't provide a simple echo canceller,
                    # so we will proceed with only noise suppression for now.
                    echo_filter = None 
                    
                    print("LiveKit Noise Suppression filter initialized.")
                except Exception as filter_err:
                    print(f"Error initializing LiveKit filters: {filter_err}")
                    print("Proceeding without audio filtering.")
                    noise_filter = None
                    echo_filter = None
            # --- End Filter Initialization ---

            try:
                while not stop_event.is_set():
                    # 1. Get raw audio data (bytes)
                    data_bytes = await mic_audio_queue.get()

                    processed_data_bytes = data_bytes # Default to original if filtering fails or is disabled

                    # 2. Process with filters if available
                    if noise_filter and echo_filter:
                        try:
                            # Convert bytes to numpy array
                            samples = np.frombuffer(data_bytes, dtype=USER_AUDIO_NUMPY_FORMAT)

                            # Create AudioFrame
                            # Note: samples_per_channel might be inferred if data shape is correct
                            frame = rtc.AudioFrame(
                                data=samples.reshape((USER_AUDIO_SAMPLES_PER_CHUNK, USER_AUDIO_CHANNELS)), # Reshape for channels
                                sample_rate=USER_AUDIO_SAMPLE_RATE,
                                num_channels=USER_AUDIO_CHANNELS,
                                samples_per_channel=USER_AUDIO_SAMPLES_PER_CHUNK,
                            )

                            # Apply filters sequentially
                            frame = noise_filter.process(frame)
                            frame = echo_filter.process(frame)

                            # Convert processed numpy array back to bytes
                            processed_data_bytes = frame.data.tobytes()

                        except Exception as process_err:
                            print(f"Error processing audio frame with filters: {process_err}")
                            # Fallback to sending original data on error
                            processed_data_bytes = data_bytes

                    # 3. Send potentially processed data
                    await ws.send(processed_data_bytes)
                    mic_audio_queue.task_done() # Mark item as processed

            except Exception as e:
                print("Error while sending: " + str(e))
                raise
            finally:
                 # --- Clean up filters ---
                 if noise_filter:
                     try:
                         noise_filter.destroy()
                         print("Noise filter destroyed.")
                     except Exception as destroy_err:
                         print(f"Error destroying noise filter: {destroy_err}")
                 if echo_filter:
                     try:
                         echo_filter.destroy()
                         print("Echo filter destroyed.")
                     except Exception as destroy_err:
                         print(f"Error destroying echo filter: {destroy_err}")
                 # --- End Filter Cleanup ---


        async def background_worker():
            print("Background agent task worker started.")
            while not stop_event.is_set():
                task_data = await task_queue.get()
                try:
                    task_type = task_data.get('task_type')
                    details = task_data.get('details')
                    task_id = f"{task_type}_{int(time.time())}"
                    
                    print(f"\n[BACKGROUND AGENT] Initiating task handoff: {task_type}")
                    print(f"[BACKGROUND AGENT] Task details: {details}")
                    
                    # You can import your modules here and execute them, e.g.:
                    # if task_type == 'alarm':
                    #     from assets.alarm.alarm import set_alarm
                    #     await asyncio.to_thread(set_alarm, details)
                    
                    # Simulation of work
                    await asyncio.sleep(2)
                    
                    # Save task result to active memory
                    result_string = f"Simulated details found for {task_type}. Content: This is the result text."
                    COMPLETED_TASKS[task_id] = {"title": task_type, "result": result_string}
                    
                    print(f"[BACKGROUND AGENT] Task '{task_id}' completed successfully.\n")
                    
                    # Ping Grace: we inject a text message pretending to be a system notification
                    # This will prompt her to mention it to the user.
                    await ws.send(json.dumps({
                        "type": "InjectContext", # Note: Exact type depends on Deepgram API, fallback to text if ignored
                        "text": f"System Notification: The background task '{task_type}' has finished and is saved as ID: {task_id}. Briefly tell the user the task is done, but DO NOT tell them the response until they ask for it."
                    }))
                    # Alternate safe message format if InjectContext is not supported
                    await ws.send(json.dumps({
                        "type": "ConversationText",
                        "text": f"System Notification: Background task {task_type} is done. ID: {task_id}. Please inform the user it is ready."
                    }))
                except Exception as e:
                    print(f"[BACKGROUND AGENT] Error processing task: {e}")
                finally:
                    task_queue.task_done()

        task_queue = asyncio.Queue()
        bg_task = asyncio.create_task(background_worker())

        async def receiver(ws):
            try:
                speaker = Speaker()
                with speaker:
                    async for message in ws:
                        if isinstance(message, str): # More robust type check
                            # print(message) # commented out to avoid noise, only printing important types
                            try:
                                msg_json = json.loads(message)
                                msg_type = msg_json.get("type")
                                
                                if msg_type == "UserStartedSpeaking":
                                    speaker.stop()
                                elif msg_type == "FunctionCallRequest":
                                    function_name = msg_json.get('function_name')
                                    print(f"Agent requested function call: {function_name}")
                                    
                                    if function_name == "handoff_task":
                                        # Send an immediate response so the agent can continue its flow without stalling
                                        call_id = msg_json.get("call_id")
                                        await ws.send(json.dumps({
                                            "type": "FunctionCallResponse",
                                            "call_id": call_id,
                                            "output": "Task handed off to background agent. You can tell the user you are working on it!"
                                        }))
                                        
                                        # Place task into queue for background agent to process over time
                                        task_args = msg_json.get("arguments", "{}")
                                        
                                        # Handle stringified JSON recursively if necessary
                                        if isinstance(task_args, str):
                                            task_args = json.loads(task_args)
                                            
                                        task_queue.put_nowait(task_args)

                                    elif function_name == "fetch_realtime_info":
                                        call_id = msg_json.get("call_id")
                                        
                                        task_args = msg_json.get("arguments", "{}")
                                        if isinstance(task_args, str):
                                            task_args = json.loads(task_args)
                                        
                                        query = task_args.get("query", "")
                                        print(f"Fetching realtime info for: {query}")
                                        
                                        # Here you block/wait to get the actual data from the web.
                                        # For now, we simulate a small delay and mock data retrieval.
                                        await asyncio.sleep(2)
                                        realtime_result = f"I found the following real-time data for '{query}': Example live data fetched."
                                        
                                        # Send the actual data back so the agent generates its speech based on this.
                                        await ws.send(json.dumps({
                                            "type": "FunctionCallResponse",
                                            "call_id": call_id,
                                            "output": realtime_result
                                        }))

                                    elif function_name == "get_task_result":
                                        call_id = msg_json.get("call_id")
                                        task_args = msg_json.get("arguments", "{}")
                                        if isinstance(task_args, str):
                                            task_args = json.loads(task_args)
                                            
                                        task_id = task_args.get("task_id", "")
                                        print(f"Agent asked for task result of ID: {task_id}")
                                        
                                        if task_id in COMPLETED_TASKS:
                                            result_data = COMPLETED_TASKS.pop(task_id)
                                            actual_result = result_data['result']
                                            
                                            # Archive to a text file for future reference
                                            archive_path = "task_archives.txt"
                                            with open(archive_path, "a") as f:
                                                f.write(f"Task ID: {task_id}\nTitle: {result_data['title']}\nResult: {actual_result}\n\n")
                                                
                                            final_response = f"Here is the task output for you to read out loud to the user: {actual_result}. I have now cleared this from active memory."
                                        else:
                                            final_response = f"Error: Task with ID '{task_id}' was not found in active memory or it hasn't completed yet."
                                            
                                        await ws.send(json.dumps({
                                            "type": "FunctionCallResponse",
                                            "call_id": call_id,
                                            "output": final_response
                                        }))
                                        
                                    elif function_name == "check_notifications":
                                        call_id = msg_json.get("call_id")
                                        
                                        if not COMPLETED_TASKS:
                                            output = "There are no pending background tasks finished right now."
                                        else:
                                            ids = list(COMPLETED_TASKS.keys())
                                            output = f"There are {len(COMPLETED_TASKS)} completed tasks pending. Their IDs are: {', '.join(ids)}."
                                            
                                        await ws.send(json.dumps({
                                            "type": "FunctionCallResponse",
                                            "call_id": call_id,
                                            "output": output
                                        }))

                            except json.JSONDecodeError:
                                print("Received non-JSON string message.")


                        elif isinstance(message, bytes): # More robust type check
                            await speaker.play(message)

            except websockets.exceptions.ConnectionClosedOK:
                print("WebSocket connection closed normally.")
            except websockets.exceptions.ConnectionClosedError as e:
                print(f"WebSocket connection closed with error: {e}")
            except Exception as e:
                print(f"Error in receiver: {e}")


        print("Starting microphone, sender, and receiver tasks...")
        try:
            # Using asyncio.gather for better exception handling and cancellation
            await asyncio.gather(
                microphone(),
                sender(ws),
                receiver(ws),
            )
        except Exception as e:
            print(f"An error occurred in one of the main tasks: {e}")
        finally:
            print("Main tasks finished or encountered an error.")
            bg_task.cancel()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received, shutting down.")
    except Exception as e:
        print(f"An unexpected error occurred in main: {e}")


# --- Speaker Class (Modified for clarity and robustness) ---
def _play(audio_out_queue: janus.Queue, stream: pyaudio.Stream, stop_event: threading.Event):
    """Worker thread function for playing audio from the queue."""
    while not stop_event.is_set():
        try:
            # Get data with timeout to allow checking stop_event periodically
            data = audio_out_queue.sync_q.get(timeout=0.05)
            if data and stream.is_active(): # Check if stream is still active
                stream.write(data)
        except queue.Empty:
            # Queue is empty, loop continues and checks stop_event
            continue
        except IOError as e:
             print(f"PyAudio stream write error in playback thread: {e}")
             # Decide if the thread should stop on stream errors
             break # Exit thread on stream error
        except Exception as e:
            print(f"Unexpected error in playback thread: {e}")
            break # Exit thread on other errors

    print("Playback thread finished.")


class Speaker:
    def __init__(self):
        self._audio = None # Keep PyAudio instance for termination
        self._queue = None
        self._stream = None
        self._thread = None
        self._stop_event = None # Use threading.Event

    def __enter__(self):
        try:
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=AGENT_AUDIO_SAMPLE_RATE,
                input=False,
                output=True,
            )
            self._queue = janus.Queue()
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=_play, args=(self._queue, self._stream, self._stop_event), daemon=True
            )
            self._thread.start()
            print("Speaker initialized and playback thread started.")
            return self # Return self for 'with' statement
        except Exception as e:
            print(f"Error initializing Speaker: {e}")
            # Clean up partially initialized resources
            if self._stream: self._stream.close()
            if self._audio: self._audio.terminate()
            raise # Re-raise the exception

    def __exit__(self, exc_type, exc_value, traceback):
        print("Speaker exiting...")
        if self._stop_event:
            self._stop_event.set() # Signal thread to stop
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0) # Wait for thread with timeout
            if self._thread.is_alive():
                 print("Warning: Playback thread did not exit gracefully.")
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
                print("PyAudio output stream closed.")
            except Exception as e:
                 print(f"Error closing output stream: {e}")
        if self._audio:
            try:
                self._audio.terminate()
                print("PyAudio instance terminated.")
            except Exception as e:
                 print(f"Error terminating PyAudio instance: {e}")

        # Clear references
        self._stream = None
        self._queue = None
        self._thread = None
        self._stop_event = None
        self._audio = None
        print("Speaker cleanup complete.")


    async def play(self, data):
        """Asynchronously puts audio data into the playback queue."""
        if self._queue and self._queue.async_q:
            try:
                await self._queue.async_q.put(data)
            except Exception as e:
                 print(f"Error putting data into speaker queue: {e}")
        else:
             print("Warning: Speaker queue not available, cannot play audio.")


    def stop(self):
        """Clears the playback queue immediately."""
        print("Speaker stop requested - clearing queue.")
        if self._queue and self._queue.async_q:
            while not self._queue.async_q.empty():
                try:
                    self._queue.async_q.get_nowait()
                except janus.QueueEmpty:
                    break
                except Exception as e:
                     print(f"Error clearing speaker queue: {e}")
                     break
            print("Speaker queue cleared.")


if __name__ == "__main__":
    sys.exit(main() or 0)
