"""LLM 路由器测试：mock llm_client，验证意图映射和字段提取。"""
from unittest.mock import patch, MagicMock
import pytest

import app.services.router as router_module
from app.services.router import route


def _mock_route(intent: str, data: dict = None):
    """辅助：让 llm_client.chat_json 返回指定意图。"""
    return {"intent": intent, "data": data or {}}


class TestRouterIntents:
    def test_add_memo_intent(self):
        mock_result = _mock_route("add_memo", {
            "content": "明天买牛奶",
            "priority": "low",
            "due_time": None,
        })
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("明天买牛奶")
        assert result["intent"] == "add_memo"
        assert result["data"]["content"] == "明天买牛奶"

    def test_add_long_term_task_intent(self):
        mock_result = _mock_route("add_long_term_task", {"content": "每周英语5小时"})
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("把每周英语5小时作为长期任务")
        assert result["intent"] == "add_long_term_task"
        assert result["data"]["content"] == "每周英语5小时"

    def test_generate_weekly_plan_intent(self):
        mock_result = _mock_route("generate_weekly_plan")
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("生成下周计划")
        assert result["intent"] == "generate_weekly_plan"

    def test_generate_day_plan_intent(self):
        mock_result = _mock_route("generate_day_plan")
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("生成今日计划")
        assert result["intent"] == "generate_day_plan"

    def test_greet_intent(self):
        mock_result = _mock_route("greet")
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("你好")
        assert result["intent"] == "greet"

    def test_unknown_intent(self):
        mock_result = _mock_route("unknown")
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("asdfghjkl")
        assert result["intent"] == "unknown"

    def test_missing_intent_defaults_to_unknown(self):
        with patch.object(router_module.llm_client, "chat_json", return_value={}):
            result = route("乱码输入")
        assert result["intent"] == "unknown"

    def test_missing_data_defaults_to_empty_dict(self):
        with patch.object(router_module.llm_client, "chat_json", return_value={"intent": "greet"}):
            result = route("你好")
        assert result["data"] == {}

    def test_add_memo_with_due_time(self):
        mock_result = _mock_route("add_memo", {
            "content": "周五前交报告",
            "priority": "high",
            "due_time": "2026-06-28T23:59:00",
        })
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("周五前交报告，很重要")
        assert result["intent"] == "add_memo"
        assert result["data"]["priority"] == "high"
        assert result["data"]["due_time"] == "2026-06-28T23:59:00"

    def test_query_memo_intent(self):
        mock_result = _mock_route("query_memo", {"keyword": None, "status": "pending"})
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("我有什么未完成的备忘")
        assert result["intent"] == "query_memo"

    def test_query_task_intent(self):
        mock_result = _mock_route("query_task", {"keyword": None, "status": None})
        with patch.object(router_module.llm_client, "chat_json", return_value=mock_result):
            result = route("我有哪些长期任务")
        assert result["intent"] == "query_task"
