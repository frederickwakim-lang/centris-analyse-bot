import requests
import json

API_URL = "https://centris-analyse-bot.onrender.com/analyze"

data = {
    "url": "https://www.centris.ca/fr/triplex~a-vendre~montreal-lachine/16107555"
}

print(f"POST vers {API_URL} ...")
resp = requests.post(API_URL, json=data, timeout=60)

print("Status code :", resp.status_code)
print("Texte brut :")
print(resp.text)

try:
    j = resp.json()
    print("\nJSON parsé :")
    print(json.dumps(j, indent=2, ensure_ascii=False))
except Exception as e:
    print("\nImpossible de parser en JSON :", e)
