"""LLM 意图路由器：单次调用同时完成意图识别和数据提取。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.llm.claude import llm_client
from app.config import TIMEZONE

ROUTER_PROMPT_TEMPLATE = """你是个人助手的意图识别器。根据用户消息，判断用户想做什么，并提取相关数据。

当前时间：{now}
时区：{tz}

支持的意图列表：
- add_memo：添加备忘录（临时提醒，可有 deadline，可无）
- add_long_term_task：添加长期任务（如每周英语5小时、每天跑步等）
- generate_weekly_plan：生成下周计划任务列表
- generate_day_plan：生成今日具体时间计划
- query_memo：查询备忘录
- query_plan：查询计划任务
- query_task：查询长期任务
- query_today：查询今日计划
- update_status：更新某项状态
- greet：打招呼、问好
- unknown：无法识别意图

输出 JSON 字段：
- intent (string)：上述意图之一
- data (object)：提取到的数据，具体字段根据意图：

  add_memo:
    - content (string)：备忘内容
    - priority (string)：low/normal/high/urgent，默认 low
    - due_time (string|null)：截止时间 ISO 8601，无则 null

  add_long_term_task:
    - content (string)：任务描述

  generate_weekly_plan / generate_day_plan:
    - （空对象即可）

  query_memo / query_plan / query_task / query_today:
    - keyword (string|null)：关键词，无则 null
    - status (string|null)：状态过滤，无则 null

  update_status:
    - target_type (string)：memo/task/plan/day_plan
    - keyword (string|null)：用于定位目标的关键词
    - status (string)：新状态值

  greet / unknown:
    - （空对象即可）

严格规则：
- 只输出 JSON，不要任何额外文字或 markdown
- data 字段永远是对象，不要省略
"""


def route(user_input: str) -> dict:
    """识别用户意图并提取结构化数据。返回 {"intent": ..., "data": {...}}"""
    now = datetime.now(ZoneInfo(TIMEZONE))
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    now_str = now.strftime(f"%Y-%m-%d %H:%M ({weekday_zh})")

    system_prompt = ROUTER_PROMPT_TEMPLATE.format(now=now_str, tz=TIMEZONE)

    result = llm_client.chat_json(system_prompt=system_prompt, user_input=user_input)

    result.setdefault("intent", "unknown")
    result.setdefault("data", {})
    return result
