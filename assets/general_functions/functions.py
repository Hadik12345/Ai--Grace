import base64
import ctypes
import os
import threading
import webbrowser
from io import BytesIO
import asyncio
import traceback
import time
from datetime import datetime, timedelta

import pyautogui
import pyperclip
import pywhatkit
from comtypes import CLSCTX_ALL, cast, POINTER
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as chromeservice