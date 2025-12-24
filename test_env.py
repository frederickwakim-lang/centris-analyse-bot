import os
from dotenv import load_dotenv

load_dotenv()

print("DISCORD_WEBHOOK_URL =", os.getenv("DISCORD_WEBHOOK_URL"))
print("CENTRIS_SEARCH_URL  =", os.getenv("CENTRIS_SEARCH_URL"))
