"""Quick smoke test for Gemini search grounding."""
from google import genai
from core.config import config

client = genai.Client(api_key=config.GEMINI_API_KEY)
response = client.models.generate_content(
    model=config.MODEL_ROUTING,
    contents="What is the weather in London right now?",
    config={"tools": [{"google_search": {}}]},
)
print(response.text)
