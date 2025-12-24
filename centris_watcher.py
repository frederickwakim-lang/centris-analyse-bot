import os
import json
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "8000"))

def fetch_html(url: str):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text

def clean_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]

def analyze_with_openai(text: str):
    system = "Tu es un expert immobilier québécois. Retourne un JSON structuré."
    user = f"Analyse cette annonce et retourne un JSON: ```{text}```"

    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        response_format={"type": "json_object"}
    )

    return json.loads(resp.output[0].content[0].text)

def analyser_centris(input_content):
    if input_content.startswith("http"):
        html = fetch_html(input_content)
    else:
        html = input_content
    cleaned = clean_html(html)
    return analyze_with_openai(cleaned)
