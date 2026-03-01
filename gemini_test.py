from google import genai

client = genai.Client(api_key="REDACTED")

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Reply with exactly this sentence and nothing else: Google Gemini connection successful."
)

print(response.text)