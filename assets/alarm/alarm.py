import threading
import time
from datetime import datetime, timedelta, time as dt_time
from typing import callable, optional
import os
import Queue # For thread-safe communication between threads
import tkinter as tk
from tkinter import messagebox

#---------- Pygame imort ----------
try:
    import pygame
    PYGAME_AVAILABE=True

except ImportError:
    print("Warning: Pygame is not available. Alarm will not be able to play sounds.")
    print("Please install pygame to enable sound functionality.")
    PYGAME_AVAILABLE=False
#---------- End of pygame import-----------


#------------Global variables -------------
alarms: Dict[int, datetime]={}
next_alarm_id: int=1
lock = threading.Lock()
message_queue = Queue.Queue() # For communication between threads

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

#-------------- End of constants -------------


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
            print(f"Error playing sound: {e}")
    elif is_sound_playing:
        print(f"Note: Alarm #{alarm_id} triggered, but another sound is already playing.")
    elif not SOUND_INTIALIZED:
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
            print(f"Error stopping sound: {e}")
    
    is_sound_playing = False
    current_ringing_alarm_id = None
    if stop_dialog:
        stop_dialog.destroy()
        stop_dialog = None

#-------------- End of Sound Functions -------------



#-------------- Initialize Sound System -------------

SOUND_INITIALIZED = intialize_sound()

#-------------- End of Sound System Initialization -------------


# --- Core Alarm Functions (Used internally by command handler) ---
def set_alarm(time_str: str):
    """Sets an alarm for the specified time string (HH:MM or HH:MM:SS)."""
    global next_alarm_id
    try:
        alarm_time = datetime.strptime(time_str, "%H:%M:%S").time()
    except ValueError:
        try:
            alarm_time = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            print("Invalid time format. Please use HH:MM or HH:MM:SS.")
            return
    
    now = datetime.now()
    alarm_datetime = datetime.combine(now.date(), alarm_time)
    
    if alarm_datetime < now:
        alarm_datetime += timedelta(days=1) # Schedule for next day if time has already passed
    
    with lock:
        alarm_id = next_alarm_id
        alarms[alarm_id] = alarm_datetime
        next_alarm_id += 1
    
    print(f"Alarm #{alarm_id} set for {alarm_datetime.strftime('%Y-%m-%d %H:%M:%S')}.")