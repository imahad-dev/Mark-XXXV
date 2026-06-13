# actions/weather_report.py

import requests
import urllib.parse
from core.config import config

def get_location():
    try:
        r = requests.get("http://ip-api.com/json/", timeout=5)
        if r.status_code == 200:
            return r.json().get("city", "Unknown")
    except:
        pass
    return "Unknown"

def weather_action(parameters: dict, player=None, session_memory=None):
    """
    Weather report action.
    Fetches real-time weather and auto-detects city if missing.
    """
    city = parameters.get("city", "").strip()

    # If LLM didn't provide a precise city, auto-detect via IP
    if not city or city.lower() in ["my city", "current location", "here", "unknown", "new york"]:
        detected = get_location()
        if detected != "Unknown":
            city = detected
        else:
            city = "London"

    try:
        encoded_city = urllib.parse.quote(city)
        if getattr(config, "OPENWEATHER_API_KEY", None):
            api_key = config.OPENWEATHER_API_KEY
            url = f"http://api.openweathermap.org/data/2.5/weather?q={encoded_city}&appid={api_key}&units=metric"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                desc = data["weather"][0]["description"]
                temp = round(data["main"]["temp"])
                msg = f"The weather in {city} is currently {desc} at {temp}°C."
            else:
                msg = f"Sir, I couldn't fetch the OpenWeather report for {city}."
        else:
            # Fallback to wttr.in
            url = f"https://wttr.in/{encoded_city}?format=%C+%t"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                condition_and_temp = resp.text.strip()
                msg = f"The current weather in {city} is {condition_and_temp}."
            else:
                msg = f"Sir, I couldn't fetch the weather for {city} right now."
    except Exception as e:
        msg = f"Sir, I encountered a network error while fetching weather for {city}."

    if player:
        try:
            player.write_log(f"[Weather] {city}: {msg}")
        except:
            pass

    if session_memory:
        try:
            session_memory.set_last_search(
                query=f"weather in {city}",
                response=msg
            )
        except Exception:
            pass  

    return msg