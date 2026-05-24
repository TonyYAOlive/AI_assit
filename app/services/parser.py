"""自然语言 → 结构化日程。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.llm.claude import llm_client
from app.config import TIMEZONE

PARSE_PROMPT_TEMPLATE = """你是一个日程解析助手，任务是把用户的中文自然语言转成 JSON 格式的日程。

当前时间：{now}
时区：{tz}

输出 JSON 字段：
- title (string): 简短标题，不超过 20 字
- description (string): 详细描述，可为空字符串
- start_time (string): ISO 8601 格式，例如 "2026-05-24T15:00:00"
- duration_minutes (int): 持续分钟数，默认 60
- location (string): 地点，可为空字符串
- participants (array of string): 参与人列表，可为空数组
- category (string): 类别，从 ["会议", "工作", "生活", "学习", "运动", "其他"] 选一个
- priority (string): 优先级，从 ["高", "中", "低"] 选一个，默认 "中"
- reminder_minutes_before (int): 提前提醒分钟数，默认 15

时间解析规则：
- "今天"=当前日期；"明天"=+1天；"后天"=+2天；"下周一"等按自然周计算
- "上午"默认 09:00；"中午"默认 12:00；"下午"默认 14:00；"晚上"默认 19:00
- 没说时长就用默认 60 分钟；说"半小时"就是 30，"一个半小时"就是 90
- 含有"重要""紧急""务必"等词时 priority 设为"高"

严格规则：
- 只输出 JSON，不要任何额外文字、不要 markdown 代码块包裹
- 所有字段都必须存在，没有的用空字符串、空数组或默认值

用户输入：{user_input}
"""


def parse_schedule(user_input: str) -> dict:
    """调用 LLM 把一句话解析成日程 dict。"""
    now = datetime.now(ZoneInfo(TIMEZONE))
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    now_str = now.strftime(f"%Y-%m-%d %H:%M ({weekday_zh})")

    system_prompt = PARSE_PROMPT_TEMPLATE.format(
        now=now_str,
        tz=TIMEZONE,
        user_input="{user_input}",  # 占位，实际拼到 user message
    )

    parsed = llm_client.chat_json(
        system_prompt=system_prompt,
        user_input=user_input,
    )

    # 补全字段（防止模型漏字段）
    parsed.setdefault("description", "")
    parsed.setdefault("duration_minutes", 60)
    parsed.setdefault("location", "")
    parsed.setdefault("participants", [])
    parsed.setdefault("category", "其他")
    parsed.setdefault("priority", "中")
    parsed.setdefault("reminder_minutes_before", 15)

    # 计算 end_time
    start_dt = datetime.fromisoformat(parsed["start_time"])
    parsed["end_time"] = (start_dt + timedelta(minutes=parsed["duration_minutes"])).isoformat()

    return parsed
