"""
recall_system.py
玩家回想系统

功能：
  CMD_SHOW_RECALL_MENU  — 显示回想主菜单（线索档案 / 推断记录 / 时间线）
  CMD_RECALL_CLUES      — 列出所有已收集线索，按地点分组
  CMD_RECALL_INFERENCES — 列出所有已解锁推断（按推断链分组）
  CMD_RECALL_TIMELINE   — 已知的当晚时间线（随线索解锁逐步填充）

设计原则：
  - 纯只读，不消耗行动点，不推进时间
  - 不给答案，只展示玩家已知信息的整理
  - 时间线条目由线索解锁，未解锁的时段显示「？」
"""

from typing import Dict, List, Tuple

# ─────────────────────────────────────────────
#  时间线条目库
#  每条 entry 需要 required_clues 全部收集才显示
# ─────────────────────────────────────────────
TIMELINE_ENTRIES = [
    {
        "time": "酉时前",
        "required_clues": {"clue_019", "clue_004"},
        "text": "张三请清虚子在后院佛龛前做法算命。清虚子做法后离去，拂尘遗落在佛龛旁。",
        "secret": True,
    },
    {
        "time": "戌时",
        "required_clues": set(),          # 游戏开始即知
        "text": "玩家一行抵达回马驿，张三上茶招待众人。",
        "secret": False,
    },
    {
        "time": "戌时",
        "required_clues": {"clue_015"},
        "text": "张三送茶时，李德福房间出现「覆托立盏」的异样——这是某种暗号。",
        "secret": True,
    },
    {
        "time": "戌时",
        "required_clues": set(),
        "text": "李德福部署守夜：玩家守上半夜（戌时至子时），赵虎守下半夜（子时以后）。",
        "secret": False,
    },
    {
        "time": "亥时",
        "required_clues": {"clue_018"},
        "text": "韩子敬在房中焚烧反诗稿，指尖沾了黑灰。",
        "secret": True,
    },
    {
        "time": "子时",
        "required_clues": set(),
        "text": "赵虎准时来到走廊接班，衣衫整齐、神色平静。玩家交出岗位，回房歇息。",
        "secret": False,
    },
    {
        "time": "丑时初",
        "required_clues": {"clue_005", "clue_006"},
        "text": "宽大脚印从后门进入后院，直通佛龛处，无折返痕迹。赵虎房间有金疮药味——凶手有伤在身。",
        "secret": True,
    },
    {
        "time": "丑时",
        "required_clues": {"clue_002", "clue_003"},
        "text": "张三在佛龛前遇害，颈部被X形勒痕勒毙。临死前右手抓伤凶手，左手攥紧某物。",
        "secret": True,
    },
    {
        "time": "丑时",
        "required_clues": {"clue_005", "clue_new_wall"},
        "text": "凶手从后院翻墙攀爬外墙返回二楼，绕开楼梯，脚踝受伤。",
        "secret": True,
    },
    {
        "time": "丑时末",
        "required_clues": {"clue_005", "clue_014"},
        "text": "细窄脚印在尸体旁短暂停留后慌乱折返。灶房炉膛随后出现燃烧中的靴子。",
        "secret": True,
    },
    {
        "time": "寅时前",
        "required_clues": {"clue_017", "clue_018"},
        "text": "韩子敬深夜离房去后院埋诗稿，折扇遗落在草丛，看到尸体后仓皇逃回。",
        "secret": True,
    },
    {
        "time": "寅时",
        "required_clues": set(),
        "text": "清虚子起夜路过后院，发现尸体和自己遗落的拂尘，惨叫声惊醒众人。玩家闻声冲出——调查开始。",
        "secret": False,
    },
]

# ─────────────────────────────────────────────
#  线索地点分组（展示用）
# ─────────────────────────────────────────────
CLUE_LOCATION_ORDER = [
    "后院", "大堂", "大堂侧屋", "灶房",
    "赵虎房", "顾琼房", "韩子敬房",
    "清虚子房", "李德福房",
]

# ─────────────────────────────────────────────
#  CMD 处理函数（由 game_handlers.py 调用）
# ─────────────────────────────────────────────

def handle_recall(user_input: str, d_state: Dict, objective_clues_db: Dict) -> Dict:
    """
    处理所有 CMD_RECALL_* 指令。
    返回 {"reply": str, "ui_type": str, "ui_options": list}
    不修改 d_state（纯只读）。
    """
    result = {
        "reply": "",
        "ui_type": "text",
        "ui_options": [],
        "consumes_ap": False,   # 不消耗行动点
    }

    if user_input == "CMD_SHOW_RECALL_MENU":
        result["reply"] = _menu_text(d_state, objective_clues_db)
        result["ui_type"] = "recall_menu"
        result["ui_options"] = [
            {"label": "▸ 线索档案",   "action_type": "RECALL_CLUES",      "payload": "clues"},
            {"label": "▸ 推断记录",   "action_type": "RECALL_INFERENCES", "payload": "inf"},
            {"label": "▸ 当晚时间线", "action_type": "RECALL_TIMELINE",   "payload": "timeline"},
            {"label": "‹ 返回",        "action_type": "CANCEL",            "payload": "MAIN"},
        ]

    elif user_input == "CMD_RECALL_CLUES":
        result["reply"] = _format_clues(d_state, objective_clues_db)
        result["ui_type"] = "recall_menu"
        result["ui_options"] = [
            {"label": "‹ 返回回想", "action_type": "RECALL_BACK", "payload": "BACK"},
        ]

    elif user_input == "CMD_RECALL_INFERENCES":
        result["reply"] = _format_inferences(d_state)
        result["ui_type"] = "recall_menu"
        result["ui_options"] = [
            {"label": "‹ 返回回想", "action_type": "RECALL_BACK", "payload": "BACK"},
        ]

    elif user_input == "CMD_RECALL_TIMELINE":
        result["reply"] = _format_timeline(d_state)
        result["ui_type"] = "recall_menu"
        result["ui_options"] = [
            {"label": "‹ 返回回想", "action_type": "RECALL_BACK", "payload": "BACK"},
        ]

    return result


# ─────────────────────────────────────────────
#  格式化函数
# ─────────────────────────────────────────────

def _menu_text(d_state: Dict, objective_clues_db: Dict) -> str:
    collected = d_state["inventory"]["clues_collected"]
    inf_count  = len(d_state.get("inferences_unlocked", []))
    tl_count   = _count_visible_timeline(d_state)
    return (
        f"**〔回想〕**\n\n"
        f"你闭上眼睛，整理迄今为止的所有发现。\n\n"
        f"  线索档案　{len(collected)} 条已收集\n"
        f"  推断记录　{inf_count} 条已解锁\n"
        f"  时间线　　{tl_count} 个时段已还原\n\n"
        f"想要回顾哪一部分？"
    )


def _format_clues(d_state: Dict, objective_clues_db: Dict) -> str:
    collected = set(d_state["inventory"]["clues_collected"])
    if not collected:
        return "**〔线索档案〕**\n\n尚未收集到任何线索。"

    # 按地点分组
    groups: Dict[str, List] = {}
    for cid in collected:
        clue = objective_clues_db.get(cid)
        if not clue:
            continue
        loc = clue.get("location", "其他")
        groups.setdefault(loc, []).append(clue)

    lines = ["**〔线索档案〕**\n"]
    for loc in CLUE_LOCATION_ORDER:
        if loc not in groups:
            continue
        lines.append(f"── {loc} ──")
        for c in groups[loc]:
            lines.append(f"▪ **{c['name']}**")
            lines.append(f"  {c['description']}")
        lines.append("")

    # 其他地点兜底
    other_locs = [l for l in groups if l not in CLUE_LOCATION_ORDER]
    for loc in other_locs:
        lines.append(f"── {loc} ──")
        for c in groups[loc]:
            lines.append(f"▪ **{c['name']}**")
            lines.append(f"  {c['description']}")
        lines.append("")

    return "\n".join(lines)


def _format_inferences(d_state: Dict) -> str:
    from inference_engine import get_all_unlocked
    infs = get_all_unlocked(d_state)
    if not infs:
        return (
            "**〔推断记录〕**\n\n"
            "尚未形成任何推断。\n"
            "继续收集线索，多条线索之间的关联将自动触发推断。"
        )

    lines = ["**〔推断记录〕**\n"]
    for inf in infs:
        lines.append(f"◆ **{inf['title']}**")
        lines.append(f"  {inf['text']}")
        if inf.get("hint_for_search"):
            lines.append(f"  → {inf['hint_for_search']}")
        lines.append("")
    return "\n".join(lines)


def _format_timeline(d_state: Dict) -> str:
    collected = set(d_state["inventory"]["clues_collected"])

    lines = ["**〔当晚时间线〕**\n"]
    current_time = None
    has_any = False

    for entry in TIMELINE_ENTRIES:
        visible = entry["required_clues"].issubset(collected)
        time_label = entry["time"]

        if time_label != current_time:
            current_time = time_label
            lines.append(f"── {time_label} ──")

        if visible:
            prefix = "  ◈ " if entry["secret"] else "  ▸ "
            lines.append(f"{prefix}{entry['text']}")
            has_any = True
        else:
            lines.append("  ？（尚有情节未查明）")

    if not has_any:
        return "**〔当晚时间线〕**\n\n收集更多线索后，时间线将逐步还原。"

    lines.append("\n※ ◈ 为通过调查推断的情节，▸ 为已知事实")
    return "\n".join(lines)


def _count_visible_timeline(d_state: Dict) -> int:
    collected = set(d_state["inventory"]["clues_collected"])
    return sum(
        1 for e in TIMELINE_ENTRIES
        if e["required_clues"].issubset(collected) and e["secret"]
    )
