import os

import requests


SILICONFLOW_CHAT_URL = "https://api.siliconflow.cn/v1/chat/completions"


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    response_format_type: str = "json_object",
) -> str:
    api_key = os.environ["SILICONFLOW_API_KEY"]
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.7,
        "top_k": 50,
        "frequency_penalty": 0.5,
        "n": 1,
        "response_format": {"type": response_format_type},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(SILICONFLOW_CHAT_URL, json=payload, headers=headers, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"siliconflow request failed: {response.status_code} {response.text}")
    body = response.json()
    return body["choices"][0]["message"]["content"]
