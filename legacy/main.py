import speech_recognition as sr
import webbrowser
import os
import platform
import re
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def say(text):
    system_name = platform.system()

    if system_name == "Darwin":  # macOS
        os.system(f"say {text}")
    elif system_name == "Windows":  # Windows
        command = f'''powershell -Command "Add-Type -AssemblyName System.Speech; ''' \
                  f'''(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{text}');"'''
        os.system(command)
    elif system_name == "Linux":  # Linux
        os.system(f"espeak '{text}'")
    else:
        print("Text-to-speech is not supported on this operating system.")


def takecommand():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.pause_threshold = 1
        try:
            print("Listening...")
            audio = r.listen(source)
            query = r.recognize_google(audio, language="en-in")
            print(f"User said: {query}")
            return query.lower()  # Convert to lowercase for consistency
        except sr.UnknownValueError:
            print("Sorry, I could not understand. Please try again.")
            return None
        except sr.RequestError:
            print("Network error. Check your internet connection.")
            return None


KNOWN_SITES = ["google", "youtube", "facebook", "twitter", "amazon", "wikipedia"]


def open_random_website(user_input):
    user_input = user_input.lower().strip()

    match = re.search(r"(open|visit|go to)\s+(.*)", user_input)
    website = match.group(2).strip() if match else user_input

    if website in KNOWN_SITES:
        website = f"https://www.{website}.com"
    elif not re.search(r"https?://|www\.|\.\w{2,}", website):
        website = f"https://{website}"

    print(f"🌍 Opening: {website}")
    webbrowser.open(website)


def say_date_time(format_string):
    now = datetime.now()
    formatted_date_time = now.strftime(format_string)
    say(f"The current date and time is: {formatted_date_time}")


def detect_platform_and_play(command):
    command = command.lower().strip()

    platform_match = re.search(r" on (\w+)$| in (\w+)$", command)
    platform = platform_match.group(1) if platform_match else "unknown"

    if platform_match:
        platform = platform_match.group(1) if platform_match.group(1) else platform_match.group(2)
    else:
        platform = "unknown"

    search_query = re.sub(r"\b(play|search|find|show me|look up|open)\b", "", command).strip()
    if platform != "unknown":
        search_query = search_query.replace(f"on {platform}", "").replace(f"in {platform}", "").strip()

    print(f"🎯 Platform: {platform.capitalize()}, 🔍 Query: {search_query}")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))

    try:
        if platform in ["youtube", "yt"]:
            driver.get(f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}")
            print(f"🎥 Searching YouTube: {search_query}")

            first_video = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "ytd-thumbnail a#thumbnail"))
            )
            driver.execute_script("arguments[0].scrollIntoView();", first_video)
            time.sleep(1)
            first_video.click()
            print("Playing first video on YouTube")

        elif platform in ["spotify", "sp"]:
            driver.get(f"https://open.spotify.com/search/{search_query.replace(' ', '%20')}")
            print(f"🎵 Searching Spotify: {search_query}")

            first_song = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "(//div[@role='row'])[2]"))
            )
            driver.execute_script("arguments[0].scrollIntoView();", first_song)
            time.sleep(1)
            first_song.click()
            print(" Playing first song on Spotify")

        elif platform in ["netflix", "nf"]:
            driver.get(f"https://www.netflix.com/search?q={search_query.replace(' ', '%20')}")
            print(f"🎬 Searching Netflix: {search_query}")

            first_show = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".title-card-container a"))
            )
            driver.execute_script("arguments[0].scrollIntoView();", first_show)
            time.sleep(1)
            first_show.click()
            print("Playing first result on Netflix")

        else:
            print("Platform not supported!")

    except Exception as e:
        print(f" Error: {e}")
    finally:
        input("Press Enter to close the browser...")
        driver.quit()


if __name__ == '__main__':
    say("Hello, I am Niassist!")
    say("Let me know what you want me to do for you!")

    Text = takecommand()
    if Text:
        if "open " in Text:
            open_random_website(Text)
        elif "play " in Text:
            detect_platform_and_play(Text)
        elif "time" in Text:
            date_time_format = "%A, %d %B %Y, %I:%M %p"
            say_date_time(date_time_format)
        elif "picture of tree" in Text:
            webbrowser.open("https://in.pinterest.com/pstautihar/trees-beautiful-trees/")
        elif "picture of doraemon" in Text:
            webbrowser.open("https://static.vecteezy.com/system/resources/previews/020/934/647/original/doraemon-cute-illustration-pro-vector.jpg")
