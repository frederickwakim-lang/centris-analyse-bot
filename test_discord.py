import os
import requests
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("DISCORD_WEBHOOK_URL")
print("Webhook =", url)

resp = requests.post(url, json={"content": "✅ Test depuis Centris-bot"})
print("Status:", resp.status_code, resp.text)
