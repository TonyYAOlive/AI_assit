"""Deepseek API 实现（预留，未来从 Claude 切换到 Deepseek 时启用）。

Deepseek 完全兼容 OpenAI 的 SDK，所以只需 pip install openai 然后改 base_url。

启用方式：
1. pip install openai
2. 在 .env 加 DEEPSEEK_API_KEY
3. 在 app/llm/claude.py 把 llm_client 改成 DeepseekClient()
"""
import json
import re

# 暂未启用，import 留到真正切换时
# from openai import OpenAI
from app.llm.base import LLMClient


class DeepseekClient(LLMClient):
    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        # self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        # self.model = model
        raise NotImplementedError("启用时取消 OpenAI 相关注释")

    def chat_json(self, system_prompt: str, user_input: str) -> dict:
        # response = self.client.chat.completions.create(
        #     model=self.model,
        #     messages=[
        #         {"role": "system", "content": system_prompt},
        #         {"role": "user", "content": user_input},
        #     ],
        #     response_format={"type": "json_object"},  # Deepseek 支持 JSON mode
        # )
        # text = response.choices[0].message.content
        # text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        # text = re.sub(r"\s*```$", "", text)
        # return json.loads(text)
        raise NotImplementedError
