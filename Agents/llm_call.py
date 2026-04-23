from typing import Any, Callable, Generator, Optional


def get_client(base_url="https://ai.ttk.homes/v1",api_key = "sk-mCQGWjeiw8pe9zCdbpNZkuyV8kwGelZSZY29h62KHIwL0Gn2"):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ImportError("Please install the `openai` package before calling llm_call.") from exc

    # 按你的要求：URL 和 Token 直接封装在函数内部，不通过外部变量传入
    # base_url = "https://ai.ttk.homes/v1"
    # api_key = "sk-mCQGWjeiw8pe9zCdbpNZkuyV8kwGelZSZY29h62KHIwL0Gn2"
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
    client = get_client("https://ai.ttk.homes/v1","sk-mCQGWjeiw8pe9zCdbpNZkuyV8kwGelZSZY29h62KHIwL0Gn2")
    merged_prompt = _merge_prompt_text(user_prompt=user_prompt, system_prompt=system_prompt)
    try:
        completion = client.chat.completions.create(
            model=model or "gemini-3-flash-preview-cli",
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


def chat_completion_with_callback(
    user_prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    on_token: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> str:
    """Like chat_completion but streams tokens and calls on_token for each chunk."""
    if on_token is None:
        return chat_completion(user_prompt=user_prompt, system_prompt=system_prompt, model=model, **kwargs)

    client = get_client("https://ai.ttk.homes/v1", "sk-mCQGWjeiw8pe9zCdbpNZkuyV8kwGelZSZY29h62KHIwL0Gn2")
    merged_prompt = _merge_prompt_text(user_prompt=user_prompt, system_prompt=system_prompt)
    try:
        stream = client.chat.completions.create(
            model=model or "gemini-3-flash-preview-cli",
            messages=[{"role": "user", "content": merged_prompt}],
            stream=True,
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

    collected: list[str] = []
    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            collected.append(delta.content)
            on_token(delta.content)
    return "".join(collected)


def invoke_llm(
    llm_callable: Callable[..., str],
    *,
    user_prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    on_token: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> str:
    if on_token is None:
        return llm_callable(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            **kwargs,
        )

    if llm_callable is chat_completion:
        return chat_completion_with_callback(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            on_token=on_token,
            **kwargs,
        )

    return llm_callable(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        model=model,
        on_token=on_token,
        **kwargs,
    )


def gemini_3_1_pro(prompt: str) -> str:
    return chat_completion(user_prompt=prompt)


if __name__ == "__main__":
    user_input = input()
    result = gemini_3_1_pro(user_input)
    print(result)


##
#
# def gemini_3_1_pro(prompt):
#     client = OpenAI(
#         base_url="https://ai.ttk.homes/v1",
#         # sk-xxx替换为自己的key
#         api_key="sk-mCQGWjeiw8pe9zCdbpNZkuyV8kwGelZSZY29h62KHIwL0Gn2",
#     )
#     completion = client.chat.completions.create(
#         model="gemini-3.1-pro-preview-cli-联网搜索",
#         messages=[
#             {"role": "system", "content": "You are a helpful assistant."},
#             {"role": "user", "content": f"{prompt}"},
#         ],
#     )
#     return completion.choices[0].message.content
#
#
# #
