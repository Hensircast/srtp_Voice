from __future__ import annotations


def generate_reply(user_text: str) -> str:
    """Return a local placeholder reply without calling any external LLM API."""
    text = user_text.strip()
    if not text:
        return "我没有听清你的输入，请再说一遍。"

    return f"这是占位回复。我收到了你的文本：{text}"
