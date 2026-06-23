# llm.py - DeepSeek 对话
from openai import OpenAI
from config import DEEPSEEK_API_KEY, SYSTEM_PROMPT

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 对话历史（短期记忆，保留最近10轮）
conversation_history = []

def chat(user_text: str) -> str:
    """
    输入：用户说的话（文字）
    输出：AI回复（文字）
    """
    global conversation_history

    conversation_history.append({
        "role": "user",
        "content": user_text
    })

    # 只保留最近10轮对话
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *conversation_history
        ],
        max_tokens=200,
        temperature=0.8,
    )

    reply = response.choices[0].message.content

    conversation_history.append({
        "role": "assistant",
        "content": reply
    })

    return reply

def clear_history():
    """清空对话历史"""
    global conversation_history
    conversation_history = []
