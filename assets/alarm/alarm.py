import threading
import time
from datetime import datetime, timedelta, time as dt_time
from typing import Callable, Optional, Dict, List, Tuple
import os
import queue # For thread-safe communication between threads
import tkinter as tk
from tkinter import messagebox
import json
import logging

#---------- Configure Logging ----------
logging.basicConfig(filename='alarm_errors.log', level=logging.ERROR,
                    format='%(asctime)s - %(levelname)s - %(message)s')
#---------------------------------------

#---------- Pygame imort ----------
try:
    import pygame
    PYGAME_AVAILABLE=True

except ImportError:
    print("Warning: Pygame is not available. Alarm will not be able to play sounds.")
    print("Please install pygame to enable sound functionality.")
    PYGAME_AVAILABLE=False
#---------- End of pygame import-----------


#------------Global variables -------------
alarms: Dict[int, datetime]={}
next_alarm_id: int=1
lock = threading.Lock()
message_queue = queue.Queue() # For communication between threads

#------------End of global variables --------


#-------------- Tkinter Global References -------------
tk_root: Optional[tk.Tk]=None
stop_dialog: Optional[tk.Toplevel]=None
is_sound_playing: bool=False
current_ringing_alarm_id: Optional[int]=None

#-------------- End of Tkinter Global References -------------




#-------------- constants -------------
ALARM_SOUND_FILE = "alarm_sound.mp3"  # Path to your alarm sound file
ALARM_CHECK_INTERVAL = 1000  # How often the main thread checks the queue (milliseconds)
CHECK_INTERVAL_MS = ALARM_CHECK_INTERVAL
ALARMS_FILE = "alarms_data.json"

#-------------- End of constants -------------


#-------------- Data Persistence -------------
def save_alarms():
    """Saves the current alarms to a file."""
    try:
        with lock:
            data = {str(k): v.isoformat() for k, v in alarms.items()}
        with open(ALARMS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Error saving alarms: {e}", exc_info=True)
        print(f"Error saving alarms: {e}")

def load_alarms():
    """Loads alarms from file, discarding expired ones."""
    global next_alarm_id
    try:
        if os.path.exists(ALARMS_FILE):
            with open(ALARMS_FILE, "r") as f:
                data = json.load(f)
            now = datetime.now()
            with lock:
                for k, v in data.items():
                    alarm_time = datetime.fromisoformat(v)
                    if alarm_time > now:
                        alarm_id = int(k)
                        alarms[alarm_id] = alarm_time
                        if alarm_id >= next_alarm_id:
                            next_alarm_id = alarm_id + 1
            print(f"Loaded {len(alarms)} active alarms from storage.")
            save_alarms() # Resave to discard expired ones from the file
    except Exception as e:
        logging.error(f"Error loading alarms: {e}", exc_info=True)
        print(f"Error loading alarms: {e}")
#-------------- End of Data Persistence ------


#-------------- Sound Functions -------------
def intialize_sound():
    """Initializes the pygame mixer if available for sound playback."""
    if PYGAME_AVAILABLE:
        try:
            pygame.init()
            pygame.mixer.init()
            print("pygame mixer intialized successfully.")
            if not os.path.isfile(ALARM_SOUND_FILE):
                print(f"Warning : Alarm sound file '{ALARM_SOUND_FILE}' not found in the current directory.")
                return False
            return True

        except Exception as e:
            logging.error(f"Error intializing pygame mixer: {e}", exc_info=True)
            print(f"Error intializing pygame mixer: {e}")
            return False
        return False

def play_alarm_sound(alarm_id: int):
    """Plays the alarm sound on a loop if pygame is available."""
    global is_sound_playing, current_ringing_alarm_id
    if SOUND_INITIALIZED and not is_sound_playing:
        try:
            pygame.mixer.music.load(ALARM_SOUND_FILE)
            pygame.mixer.music.play(loops=-1) # Play indefinitely
            is_sound_playing = True
            current_ringing_alarm_id = alarm_id
            print(f"Playing sound for alarm #{alarm_id}...")
            # Schedule showing the dialog from the main thread
            message_queue.put({"type": "show_dialog", "id": alarm_id})
        except Exception as e:
            logging.error(f"Error playing sound: {e}", exc_info=True)
            print(f"Error playing sound: {e}")
    elif is_sound_playing:
        print(f"Note: Alarm #{alarm_id} triggered, but another sound is already playing.")
    elif not SOUND_INITIALIZED:
        print(f"Cannot play sound for alarm #{alarm_id} because sound system is not initialized.")
        print(f"⏰ Alarm #{alarm_id}! (Sound disabled)")
        print('\a', end='', flush=True) # Fallback bell

def stop_alarm_sound():
    """Stops the currently playing alarm sound."""
    global is_sound_playing, current_ringing_alarm_id, stop_dialog
    if is_sound_playing:
        try:
            pygame.mixer.music.stop()
            print(f"Stopped sound for alarm #{current_ringing_alarm_id}.")
        except Exception as e:
            logging.error(f"Error stopping sound: {e}", exc_info=True)
            print(f"Error stopping sound: {e}")
    
    is_sound_playing = False
    current_ringing_alarm_id = None
    if stop_dialog:
        stop_dialog.destroy()
        stop_dialog = None

def set_alarm_volume(volume_level: float):
    """Sets the playback volume for the alarm sound."""
    if SOUND_INITIALIZED:
        try:
            # Pygame volume is a float between 0.0 and 1.0
            pygame.mixer.music.set_volume(volume_level)
            print(f"Volume set to {volume_level * 100:.0f}%")
        except Exception as e:
            logging.error(f"Error setting volume: {e}", exc_info=True)
            print(f"Error setting volume: {e}")
    else:
        print("Sound system not initialized. Cannot set volume.")

#-------------- End of Sound Functions -------------



#-------------- Initialize Sound System -------------

SOUND_INITIALIZED = intialize_sound()

#-------------- End of Sound System Initialization -------------


# --- Core Alarm Functions (Used internally by command handler) ---
def set_alarm(time_str: str):
    """Sets an alarm (logic unchanged, adjusted print)."""
    global next_alarm_id
    try:
        # Validate format first before proceeding
        alarm_time_obj: dt_time = datetime.strptime(time_str, "%H:%M").time()
        now: datetime = datetime.now()
        alarm_datetime: datetime = datetime.combine(now.date(), alarm_time_obj)

        if alarm_datetime <= now:
            print(f"Time {time_str} has already passed today. Setting for tomorrow.")
            alarm_datetime += timedelta(days=1)

        with lock:
            alarm_id = next_alarm_id
            alarms[alarm_id] = alarm_datetime
            next_alarm_id += 1

        save_alarms() # Update persistent storage

        print(f"Alarm scheduled internally for {alarm_datetime.strftime('%Y-%m-%d %I:%M %p')}")
        return alarm_id

    except ValueError:
        # Re-raise the specific error for the command handler
        raise ValueError(f"Invalid time format: '{time_str}'. Please use HH:MM (e.g., 07:30 or 19:45).")
    except Exception as e:
        # Catch other potential errors during datetime combination etc.
        logging.error(f"Error setting alarm for {time_str}: {e}", exc_info=True)
        print(f"Error setting alarm for {time_str}: {e}")
        # Raise a generic exception or return a specific code if needed
        raise RuntimeError(f"An unexpected error occurred while setting the alarm: {e}")

def list_alarms() -> List[Tuple[int, datetime]]:
    """Retrieves alarms (unchanged)."""
    with lock:
        # Return a copy to avoid modification issues if the caller iterates while alarms change
        return list(alarms.items())

def delete_alarm(alarm_id: int) -> bool:
    """Deletes an alarm (unchanged)."""
    with lock:
        if alarm_id in alarms:
            del alarms[alarm_id]
            save_alarms() # Update persistent storage
            return True
        else:
            return False

# --- End of Core Alarm Functions ---



# --- Functions to be called externally (e.g., from functions.py) ---
def request_add_alarm(time_str: str) -> bool:
    """
    Puts an 'add' command onto the message queue for the alarm manager to process.

    Args:
        time_str: The desired alarm time in HH:MM format (e.g., "14:30").

    Returns:
        True if the request was successfully queued, False otherwise (though unlikely).
    """
    try:
        # Basic format check before queuing (optional but helpful)
        datetime.strptime(time_str, "%H:%M")
        command_line = f"add {time_str}"
        message_queue.put({"type": "command", "line": command_line})
        print(f"[Alarm Request] Queued command: {command_line}")
        # Note: This only confirms queuing, not successful setting.
        # The result will be printed by handle_command later.
        return True
    except ValueError:
         print(f"[Alarm Request] Invalid time format for queuing: '{time_str}'. Request not sent.")
         return False # Indicate failure due to format before queuing
    except Exception as e:
        logging.error(f"[Alarm Request] Error queuing 'add' command: {e}", exc_info=True)
        print(f"[Alarm Request] Error queuing 'add' command: {e}")
        return False

def request_delete_alarm(alarm_id: int) -> bool:
    """
    Puts a 'delete' command onto the message queue for the alarm manager to process.

    Args:
        alarm_id: The ID of the alarm to delete.

    Returns:
        True if the request was successfully queued, False otherwise.
    """
    try:
        # Ensure alarm_id is an integer before creating the command string
        if not isinstance(alarm_id, int):
             raise TypeError("alarm_id must be an integer.")
        command_line = f"delete {alarm_id}"
        message_queue.put({"type": "command", "line": command_line})
        print(f"[Alarm Request] Queued command: {command_line}")
        # Note: This only confirms queuing, not successful deletion.
        # The result will be printed by handle_command later.
        return True
    except Exception as e:
        logging.error(f"[Alarm Request] Error queuing 'delete' command: {e}", exc_info=True)
        print(f"[Alarm Request] Error queuing 'delete' command: {e}")
        return False

def request_stop_alarm_sound() -> bool:
    """
    Puts a 'stop_sound' command onto the message queue to remotely stop the ringing alarm.

    Returns:
        True if the request was successfully queued, False otherwise.
    """
    try:
        command_line = "stop_sound"
        message_queue.put({"type": "command", "line": command_line})
        print(f"[Alarm Request] Queued command: {command_line}")
        return True
    except Exception as e:
        logging.error(f"[Alarm Request] Error queuing 'stop_sound' command: {e}", exc_info=True)
        print(f"[Alarm Request] Error queuing 'stop_sound' command: {e}")
        return False

def request_set_volume(volume_level: float) -> bool:
    """
    Puts a 'volume' command onto the message queue to remotely control sound volume.

    Args:
        volume_level: The desired volume level (0.0 to 1.0).

    Returns:
        True if the request was successfully queued, False otherwise.
    """
    try:
        if not isinstance(volume_level, (int, float)) or not (0.0 <= volume_level <= 1.0):
             print("[Alarm Request] Invalid volume level. Must be between 0.0 and 1.0.")
             return False
        command_line = f"volume {float(volume_level)}"
        message_queue.put({"type": "command", "line": command_line})
        return True
    except Exception as e:
        logging.error(f"[Alarm Request] Error queuing 'volume' command: {e}", exc_info=True)
        print(f"[Alarm Request] Error queuing 'volume' command: {e}")
        return False

# --- Background Threads ---

def alarm_checker():
    """
    Checks for due alarms and puts 'trigger' messages on the queue.
    """
    print("[Alarm Checker] Background alarm checker started.")
    while True:
        now = datetime.now()
        # Check alarms based on the exact time they are set for
        # now_minute = now.replace(second=0, microsecond=0) # Previous logic checked per minute
        triggered_ids_to_remove = []

        with lock:
            # Create a copy of items to iterate over, avoiding dictionary size change errors
            items_to_check = list(alarms.items())
            for alarm_id, alarm_time in items_to_check:
                # Trigger if the current time is at or past the alarm time
                if alarm_time <= now:
                    # Put message on queue for main thread to handle sound/dialog
                    message_queue.put({"type": "trigger", "id": alarm_id, "time_str": alarm_time.strftime('%I:%M %p')})
                    triggered_ids_to_remove.append(alarm_id) # Mark for removal after triggering

            if triggered_ids_to_remove:
                # Remove triggered alarms outside the iteration loop
                for alarm_id in triggered_ids_to_remove:
                    if alarm_id in alarms:
                       print(f"[Alarm Checker] Removing triggered alarm ID: {alarm_id}")
                       del alarms[alarm_id] # Use the internal delete logic directly
                save_alarms() # Save after removing triggered alarms

        # Sleep for a short interval before checking again
        # Shorter sleep leads to more responsive triggering but higher CPU usage.
        # Longer sleep (like sleeping until the next minute) is less resource-intensive
        # but might delay triggering slightly if an alarm is set for e.g., HH:MM:30.
        # Let's stick to checking roughly every ten seconds for better responsiveness and resource conservation.
        time.sleep(10) # Check every ten seconds


def cli_input_handler():
    """
    Handles command-line input in a separate thread and puts commands on the queue.
    (Can be disabled if only external control is needed)
    """
    print("\n--- Alarm Manager CLI (Runs in Background) ---")
    print("Commands: add HH:MM | list | delete <ID> | volume <0.0-1.0> | stop_sound | exit")
    print("----------------------------------------------\n")

    while True:
        try:
            # Use a timeout to prevent blocking indefinitely if stdin is closed unexpectedly
            # This requires a different approach than simple input(), maybe select module?
            # For simplicity, keeping input() but adding error handling.
            command_line = input("> ").strip().lower()
            if command_line:
                # Put the command on the queue for the main thread to process
                message_queue.put({"type": "command", "line": command_line})
                if command_line == "exit":
                    print("[CLI Thread] Exit command received, stopping input handler.")
                    break # Exit this thread if 'exit' command is given
            else:
                # Handle empty input if needed, or just loop again
                pass
        except EOFError:
            print("[CLI Thread] EOF detected, stopping input handler.")
            # Signal main thread to exit if CLI is the primary control method
            message_queue.put({"type": "command", "line": "exit"})
            break
        except KeyboardInterrupt:
             print("[CLI Thread] KeyboardInterrupt detected, stopping input handler.")
             message_queue.put({"type": "command", "line": "exit"})
             break
        except Exception as e:
             logging.error(f"[CLI Thread] Error reading input: {e}", exc_info=True)
             print(f"[CLI Thread] Error reading input: {e}. Stopping input handler.")
             # Consider signaling exit on unexpected errors too
             message_queue.put({"type": "command", "line": "exit"})
             break

    print("[CLI Thread] CLI input thread finished.")


# --- Main Thread Functions (Tkinter & Queue Processing) ---

def process_queue():
    """
    Processes messages from the queue in the main Tkinter thread.
    """
    global tk_root, stop_dialog # Allow modification
    try:
        while not message_queue.empty():
            msg = message_queue.get_nowait()
            msg_type = msg.get("type")

            if msg_type == "trigger":
                alarm_id = msg.get("id")
                time_str = msg.get("time_str", "Unknown time") # Default if time_str missing
                print(f"⏰ Alarm #{alarm_id}! It’s {time_str}.") # Print notification immediately
                play_alarm_sound(alarm_id) # Attempt to play sound

            elif msg_type == "show_dialog":
                 # This is triggered *after* play_alarm_sound starts
                 alarm_id = msg.get("id")
                 if tk_root and not stop_dialog: # Only show if root exists and no other dialog is up
                    # Ensure we run GUI updates in the main thread
                    tk_root.after(0, lambda: show_stop_dialog(alarm_id))

            elif msg_type == "command":
                handle_command(msg.get("line"))

            message_queue.task_done() # Mark message as processed

    except queue.Empty:
        pass # No messages currently
    except Exception as e:
        logging.error(f"Error processing queue: {e}", exc_info=True)
        print(f"Error processing queue: {e}")
        # Consider logging traceback here for debugging
        # import traceback
        # traceback.print_exc()
    finally:
        # Reschedule the check only if tk_root still exists (i.e., not shutting down)
        if tk_root:
            tk_root.after(CHECK_INTERVAL_MS, process_queue)

def show_stop_dialog(alarm_id: int):
    """Creates and shows the Tkinter stop dialog. Must be called from main thread."""
    global stop_dialog, tk_root
    if not tk_root or stop_dialog: # Check again in case state changed
        return

    stop_dialog = tk.Toplevel(tk_root)
    stop_dialog.title("Alarm!")
    # Center the dialog (simple centering)
    stop_dialog.update_idletasks() # Ensure window dimensions are calculated
    x = tk_root.winfo_screenwidth() // 2 - stop_dialog.winfo_width() // 2
    y = tk_root.winfo_screenheight() // 2 - stop_dialog.winfo_height() // 2
    stop_dialog.geometry(f"+{x}+{y}")
    stop_dialog.resizable(False, False)
    label = tk.Label(stop_dialog, text=f"Alarm #{alarm_id} is ringing!", padx=20, pady=10)
    label.pack()
    stop_button = tk.Button(stop_dialog, text="Stop Sound", command=stop_alarm_sound, padx=10, pady=5)
    stop_button.pack(pady=10)
    stop_dialog.protocol("WM_DELETE_WINDOW", stop_alarm_sound) # Stop sound if user closes window
    stop_dialog.lift() # Bring to front
    stop_dialog.attributes('-topmost', True) # Keep on top
    stop_dialog.after(100, stop_dialog.focus_force) # Try to force focus


def handle_command(command_line: str):
    """Processes CLI commands received via the queue."""
    global tk_root
    if not command_line:
        return # Ignore empty commands

    parts = command_line.lower().split() # Ensure lowercase processing
    command = parts[0]
    print(f"[Command Handler] Processing: {command_line}") # Log received command

    if command == "add" and len(parts) == 2:
        time_str = parts[1]
        try:
            alarm_id = set_alarm(time_str) # Use the internal function
            print(f"✅ Alarm set successfully via command! ID: {alarm_id}")
        except (ValueError, RuntimeError) as e: # Catch specific errors from set_alarm
            print(f"Error processing 'add' command: {e}")
        except Exception as e: # Catch unexpected errors
             logging.error(f"Unexpected error processing 'add {time_str}': {e}", exc_info=True)
             print(f"Unexpected error processing 'add {time_str}': {e}")

    elif command == "list" and len(parts) == 1:
        active_alarms = list_alarms()
        if active_alarms:
            print("\n--- Active Alarms ---")
            # Sort by time for readability
            active_alarms.sort(key=lambda item: item[1])
            for alarm_id, alarm_time in active_alarms:
                print(f"  ID {alarm_id}: {alarm_time.strftime('%Y-%m-%d %I:%M %p')}")
            print("---------------------\n")
        else:
            print("No active alarms.")

    elif command == "delete" and len(parts) == 2:
        try:
            alarm_id_to_delete = int(parts[1])
            if delete_alarm(alarm_id_to_delete): # Use the internal function
                print(f"✅ Alarm #{alarm_id_to_delete} deleted via command.")
            else:
                # This case means the ID wasn't in the dictionary
                print(f"Error: No alarm found with ID {alarm_id_to_delete}.")
        except ValueError:
            # This case means parts[1] wasn't a valid integer
            print(f"Error: Invalid ID format '{parts[1]}'. Please provide a numeric ID.")
        except Exception as e: # Catch unexpected errors
             logging.error(f"Unexpected error processing 'delete {parts[1]}': {e}", exc_info=True)
             print(f"Unexpected error processing 'delete {parts[1]}': {e}")

    elif command == "stop_sound" and len(parts) == 1:
        stop_alarm_sound()
        print("✅ Alarm sound stopped via command.")

    elif command == "volume" and len(parts) == 2:
        try:
            vol = float(parts[1])
            if 0.0 <= vol <= 1.0:
                set_alarm_volume(vol)
            else:
                print("Error: Volume must be between 0.0 and 1.0.")
        except ValueError:
            print("Error: Invalid volume format. Please provide a number between 0.0 and 1.0.")

    elif command == "exit" and len(parts) == 1:
        print("Exit command received. Shutting down Alarm Manager...")
        stop_alarm_sound() # Stop sound if playing
        if SOUND_INITIALIZED:
            try:
                pygame.quit() # Clean up pygame
                print("Pygame quit.")
            except Exception as e:
                logging.error(f"Error quitting pygame: {e}", exc_info=True)
                print(f"Error quitting pygame: {e}")

        if tk_root:
            try:
                # Stop the queue processing loop first
                # tk_root = None # Signal process_queue to stop rescheduling (alternative)
                # Or destroy the window which stops mainloop
                tk_root.destroy() # Destroy the hidden root window, stops mainloop
                print("Tkinter root window destroyed.")
            except Exception as e:
                logging.error(f"Error destroying Tkinter root: {e}", exc_info=True)
                print(f"Error destroying Tkinter root: {e}")
        tk_root = None # Ensure it's marked as None

    else:
        print(f"Unknown command received: '{command_line}'. Use 'add HH:MM', 'list', 'delete <ID>', 'volume <0.0-1.0>', 'stop_sound', or 'exit'.")


# --- Main Application Entry Point ---

def start_alarm_manager(enable_cli=True, enable_gui=True):
    """
    Initializes threads, Tkinter (optional), and starts the main loop.

    Args:
        enable_cli (bool): If True, starts the background CLI input thread.
        enable_gui (bool): If True, initializes Tkinter for alarm dialogs.
                           Requires a display environment.
    """
    global tk_root

    print("Initializing Alarm Manager...")
    load_alarms() # Retrieve saved alarms before initializing background tasks
    if enable_gui:
        try:
            # Initialize Tkinter (create hidden root window)
            tk_root = tk.Tk()
            tk_root.withdraw() # Hide the main window
            print("Tkinter initialized (hidden root window).")
        except tk.TclError as e:
            print(f"Warning: Could not initialize Tkinter (maybe no display?): {e}")
            print("GUI dialogs for alarms will be disabled.")
            tk_root = None # Ensure tk_root is None if init fails
            enable_gui = False # Force GUI off
    else:
        tk_root = None # Explicitly set to None if GUI is disabled
        print("Tkinter GUI disabled by configuration.")


    # Start background alarm checker thread (essential)
    checker = threading.Thread(target=alarm_checker, daemon=True, name="AlarmCheckerThread")
    checker.start()
    print("Alarm checker thread started.")

    # Start optional CLI input thread
    if enable_cli:
        input_thread = threading.Thread(target=cli_input_handler, daemon=True, name="AlarmCLIThread")
        input_thread.start()
        print("CLI input thread started.")
    else:
        print("CLI input thread disabled.")

    # Start the main loop (either Tkinter's or a simple sleep loop if no GUI)
    if enable_gui and tk_root:
        print("Starting Tkinter main loop for queue processing and dialogs...")
        # Schedule the first queue check
        tk_root.after(CHECK_INTERVAL_MS, process_queue)
        try:
            tk_root.mainloop() # Blocks until tk_root is destroyed (e.g., by 'exit' command)
        except KeyboardInterrupt:
            print("\nCtrl+C detected in main loop. Initiating shutdown...")
            handle_command("exit") # Graceful shutdown via command handler
        finally:
             print("Tkinter main loop finished.")
    elif not enable_gui:
        # If no GUI, we still need a loop to process the queue
        print("Starting simple loop for queue processing (no GUI)...")
        try:
            while True: # Loop indefinitely until an exit condition (e.g., external signal)
                # Process queue manually since there's no tk_root.after()
                process_queue_no_gui()
                # Check for an exit signal if needed (e.g., global flag set by 'exit' command)
                # For now, relies on KeyboardInterrupt or process termination
                time.sleep(CHECK_INTERVAL_MS / 1000.0) # Sleep for the check interval
        except KeyboardInterrupt:
            print("\nCtrl+C detected in simple loop. Shutting down...")
            # Need a way to trigger cleanup if handle_command isn't called
            stop_alarm_sound()
            if SOUND_INITIALIZED: pygame.quit()
        finally:
            print("Simple processing loop finished.")

    print("Alarm Manager stopped.")


def process_queue_no_gui():
    """Processes queue messages when Tkinter is not used."""
    try:
        while not message_queue.empty():
            msg = message_queue.get_nowait()
            msg_type = msg.get("type")

            if msg_type == "trigger":
                alarm_id = msg.get("id")
                time_str = msg.get("time_str", "Unknown time")
                print(f"⏰ Alarm #{alarm_id}! It’s {time_str}.")
                play_alarm_sound(alarm_id) # Still try to play sound

            # No "show_dialog" handling needed without GUI

            elif msg_type == "command":
                # Handle commands, especially 'exit'
                handle_command(msg.get("line"))
                # If handle_command was 'exit', it might have cleaned up pygame etc.
                # We might need a flag to break the loop in start_alarm_manager
                if msg.get("line") == "exit":
                    # This part is tricky without Tkinter's mainloop exit.
                    # The loop in start_alarm_manager needs to terminate.
                    # Raising an exception or setting a global flag are options.
                    # For now, rely on KeyboardInterrupt or process kill.
                    print("[Queue No GUI] Exit command processed.")


            message_queue.task_done()

    except queue.Empty:
        pass
    except Exception as e:
        logging.error(f"Error processing queue (no GUI): {e}", exc_info=True)
        print(f"Error processing queue (no GUI): {e}")


if __name__ == "__main__":
    # Example: Start with both CLI and GUI enabled
    start_alarm_manager(enable_cli=True, enable_gui=True)

    # Example: Start without CLI, but with GUI dialogs
    # start_alarm_manager(enable_cli=False, enable_gui=True)

    # Example: Start headless (no CLI, no GUI), useful for background service
    # start_alarm_manager(enable_cli=False, enable_gui=False)