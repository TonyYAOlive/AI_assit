"""Claude API 实现。"""
import json
import re
import anthropic

from app.llm.base import LLMClient
from app.config import ANTHROPIC_API_KEY, LLM_MODEL


class ClaudeClient(LLMClient):
    def __init__(self, api_key: str = ANTHROPIC_API_KEY, model: str = LLM_MODEL):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def chat_json(self, system_prompt: str, user_input: str, max_tokens: int = 1024) -> dict:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_input}],
        )
        text = response.content[0].text.strip()

        # 兜底处理：模型偶尔会用 ```json ... ``` 包裹
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        return json.loads(text)


# 全局单例
llm_client: LLMClient = ClaudeClient()
