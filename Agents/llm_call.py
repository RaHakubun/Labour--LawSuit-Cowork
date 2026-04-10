from typing import Any, Optional


def get_client(base_url="https://ai.ttk.homes/v1",api_key = "sk-Q81YmkgtZRfeXZp6yUFR7xFdauFStKn8WqlUWyTPH4L7EvAi"):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ImportError("Please install the `openai` package before calling llm_call.") from exc

    # 按你的要求：URL 和 Token 直接封装在函数内部，不通过外部变量传入
    # base_url = "https://ai.ttk.homes/v1"
    # api_key = "sk-Q81YmkgtZRfeXZp6yUFR7xFdauFStKn8WqlUWyTPH4L7EvAi"
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
    )


def _merge_prompt_text(user_prompt: str, system_prompt: Optional[str] = None) -> str:
    if system_prompt and system_prompt.strip():
        return f"{system_prompt}\n\n{user_prompt}"
    return user_prompt


def chat_completion(
    user_prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs: Any,
) -> str:
    client = get_client("https://xiaoai.plus/v1","sk-983aTNpcRnDN28ndyMilcFC1cqJkwWFpspGEmtlKykTHYKC4")
    merged_prompt = _merge_prompt_text(user_prompt=user_prompt, system_prompt=system_prompt)
    try:
        completion = client.chat.completions.create(
            model=model or "gpt-5.1",
            messages=[
                {"role": "user", "content": merged_prompt},
            ],
            **kwargs,
        )
    except Exception as exc:
        error_text = str(exc)
        is_auth_error = exc.__class__.__name__ == "AuthenticationError" or "401" in error_text or "无效的令牌" in error_text
        if is_auth_error:
            raise RuntimeError(
                "Authentication failed. 请检查 llm_call.py 中 get_client() 里的 URL/Token 或 model 是否正确。"
            ) from exc
        raise
    return completion.choices[0].message.content or ""


def gemini_3_1_pro(prompt: str) -> str:
    return chat_completion(user_prompt=prompt)


if __name__ == "__main__":
    user_input = input()
    result = gemini_3_1_pro(user_input)
    print(result)
