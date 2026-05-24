"""LLM 抽象层 —— 未来换 Deepseek 时新增子类即可，业务代码不动。"""
from abc import ABC, abstractmethod


class LLMClient(ABC):
    @abstractmethod
    def chat_json(self, system_prompt: str, user_input: str) -> dict:
        """输入 system prompt 和用户输入，返回解析好的 JSON dict。"""
        pass
