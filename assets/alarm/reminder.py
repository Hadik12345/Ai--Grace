import time
import datetime
import threading
import os
import tts # <-- Add this import

# Import winsound only if on Windows and needed
if os.name == 'nt':
    try:
        import winsound
        WINSOUND_AVAILABLE = True
    except ImportError:
        print("Warning: winsound module not found. Sound notifications on Windows will be disabled.")
        WINSOUND_AVAILABLE = False
else:
    WINSOUND_AVAILABLE = False # Not on Windows

# --- Helper Function (from original script, slightly modified for clarity) ---
def _play_reminder_notification(message: str):
    """Internal function to display the reminder and play sound."""
    now = datetime.datetime.now()
    print(f"\n--- REMINDER ---")
    print(f"Time: {now.strftime('%I:%M %p')}")
    print(f"Message: {message}")
    # Ensure TTS is initialized before speaking.
    # Depending on your tts module structure, you might need initialization here
    # or ensure it's initialized globally before any reminder thread starts.
    # Assuming tts is initialized elsewhere (like in brain.py) and is accessible.
    try:
        tts.speak(message)
    except Exception as e:
        print(f"Error during TTS playback in reminder: {e}")
    print(f"----------------")

    # Play sound notification if possible
    if WINSOUND_AVAILABLE:
        try:
            # Play a simple system sound asynchronously
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            print("(Playing notification sound...)")
        except Exception as e:
            print(f"Error playing sound: {e}")
            # Fallback if winsound fails
            print('\a', end='', flush=True) # Try to ring the terminal bell
    else:
        # Fallback for non-Windows
        print('\a', end='', flush=True) # Try to ring the terminal bell

# --- Reminder Check Logic (from original script) ---
def _wait_for_reminder_time(reminder_time_obj: datetime.time, message: str):
    """Waits until the specified time and then triggers the notification."""
    print(f"Reminder check thread started for {reminder_time_obj.strftime('%I:%M %p')} ({reminder_time_obj.strftime('%H:%M')}). Waiting...")
    while True:
        now = datetime.datetime.now()
        # Compare only the time part (hour and minute)
        if now.hour == reminder_time_obj.hour and now.minute == reminder_time_obj.minute:
            _play_reminder_notification(message)
            break # Reminder triggered, exit the loop

        # Sleep until the start of the next minute for efficiency.
        # Add a small buffer (e.g., 0.1 seconds) to avoid potential race conditions
        # where the loop might wake up slightly before the minute changes.
        seconds_to_next_minute = 60 - now.second + 0.1
        time.sleep(seconds_to_next_minute)
    print(f"Reminder check thread finished for {reminder_time_obj.strftime('%I:%M %p')}.")


# --- LLM Callable Function ---
def schedule_reminder(reminder_time_str: str, message: str) -> str:
    """
    Sets a reminder for a specific time, callable by an LLM.

    Parses the time string (supports 'HH:MM AM/PM' or 'HH:MM' formats)
    and schedules a background thread to trigger the reminder.

    Args:
        reminder_time_str: The time for the reminder (e.g., "04:30 PM", "16:30").
        message: The message for the reminder.

    Returns:
        A string indicating success or failure (with reason).
    """
    reminder_time_obj = None
    parsed_format = ""

    # --- Time Parsing Logic (adapted from original main) ---
    try:
        # Try parsing 12-hour format first
        reminder_time_obj = datetime.datetime.strptime(reminder_time_str, "%I:%M %p").time()
        parsed_format = "%I:%M %p"
    except ValueError:
        try:
            # If 12-hour fails, try 24-hour format
            reminder_time_obj = datetime.datetime.strptime(reminder_time_str, "%H:%M").time()
            parsed_format = "%H:%M"
        except ValueError:
            # If both fail, return an error message
            return f"Error: Invalid time format '{reminder_time_str}'. Please use 'HH:MM AM/PM' or 'HH:MM'."

    if reminder_time_obj:
        # --- Start Reminder Thread ---
        try:
            reminder_thread = threading.Thread(
                target=_wait_for_reminder_time,
                args=(reminder_time_obj, message),
                daemon=True # Allows the main program to exit even if thread is running
            )
            reminder_thread.start()

            # Return a success message
            formatted_time = reminder_time_obj.strftime(parsed_format)
            # Also include 24hr format for clarity if parsed as 12hr
            alt_format = reminder_time_obj.strftime('%H:%M') if parsed_format == "%I:%M %p" else ""
            alt_format_str = f" ({alt_format})" if alt_format else ""

            success_msg = f"Success: Reminder set for {formatted_time}{alt_format_str} with message: '{message}'."
            print(success_msg) # Also print to console for visibility
            return success_msg
        except Exception as e:
            error_msg = f"Error: Failed to start reminder thread: {e}"
            print(error_msg)
            return error_msg
    else:
        # This case should theoretically not be reached due to prior checks, but included for safety
        return f"Error: Could not parse time '{reminder_time_str}'."

# --- Example Usage (for testing) ---
# (Keep the __main__ block as it was for testing)
if __name__ == "__main__":
    # Ensure TTS is initialized if running this file directly for testing
    try:
        tts.initialize_tts() # Add initialization here for standalone testing
        print("TTS initialized for testing.")
    except Exception as e:
        print(f"Could not initialize TTS for testing: {e}")


    print("Testing the schedule_reminder function...")

    # Example 1: Valid 12-hour format
    result1 = schedule_reminder("05:15 PM", "Check on the build process.")
    print(f"Result 1: {result1}")

    # Example 2: Valid 24-hour format
    result2 = schedule_reminder("09:00", "Morning team sync.")
    print(f"Result 2: {result2}")

    # Example 3: Invalid format
    result3 = schedule_reminder("9 PM", "Dinner time.")
    print(f"Result 3: {result3}")

    # Example 4: Another valid format
    result4 = schedule_reminder("11:59 AM", "Almost lunch!")
    print(f"Result 4: {result4}")


    print("\nReminder threads started (if successful). The script will wait...")
    print("NOTE: Since threads are daemons, the main script might exit before reminders trigger")
    print("unless you add logic to keep it alive (like waiting for input or a long sleep).")
    print("Press Ctrl+C to exit.")

    # Keep the main thread alive briefly for demonstration if needed,
    # otherwise in a real LLM integration, the host application would likely keep running.
    try:
        # Wait for a few seconds or indefinitely
        # time.sleep(300) # Example: wait 5 minutes
        input("Press Enter to exit the test script...\n") # Keeps script alive until Enter
    except KeyboardInterrupt:
        print("\nExiting test script.")
