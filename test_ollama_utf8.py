import requests

payload = {
    "model": "qwen3:4b-instruct",
    "messages": [
        {
            "role": "system",
            "content": "回答当前问题，不要复述题目，只输出最终答案。",
        },
        {
            "role": "user",
            "content": "计算37乘29，只输出数字。",
        },
    ],
    "stream": False,
    "options": {
        "temperature": 0,
        "num_predict": 64,
    },
}

response = requests.post(
    "http://localhost:11434/api/chat",
    json=payload,
    timeout=180,
)
response.raise_for_status()

data = response.json()

print("model:", data.get("model"))
print("done_reason:", data.get("done_reason"))
print("prompt_eval_count:", data.get("prompt_eval_count"))
print("eval_count:", data.get("eval_count"))
print("content:", data["message"]["content"])
