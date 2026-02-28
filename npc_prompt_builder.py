# ==========================================
# 🎭 NPC Prompt 构建器 (数据驱动版)
# ==========================================
# 所有NPC的角色指令、对质反应、未知信息均从JSON读取
# 新增剧本只需修改JSON，无需改动代码
# ==========================================

import json
from typing import Dict, List, Optional

# ------------------------------------------
# 线索简要描述（供NPC判断玩家手中的牌）
# ------------------------------------------
def generate_clue_briefs(clues_db: dict) -> dict:
    """从线索库自动生成简要描述"""
    briefs = {}
    for cid, clue in clues_db.items():
        loc = clue.get('location', '')
        briefs[cid] = f"{clue['name']}（{loc}）" if loc else clue['name']
    return briefs

def build_player_clue_summary(player_clues: list, clues_db: dict) -> str:
    briefs = generate_clue_briefs(clues_db)
    if not player_clues:
        return "玩家目前没有发现任何线索。"
    lines = [f"- {briefs.get(cid, cid)}" for cid in player_clues]
    return "\n".join(lines)


def build_confrontation_section(triggers: Dict, player_clues: List[str]) -> str:
    """
    根据玩家已有线索，生成当前激活的对质反应指令。
    只有玩家手中有某条线索时，NPC才会收到对应的对质反应指令。
    这样可以避免NPC"未卜先知"地提前准备应对策略。
    """
    if not triggers:
        return ""

    active_lines = []
    collected_set = set(player_clues)

    for trigger_key, trigger_data in triggers.items():
        # 处理组合线索 key（如 "combined_clue_012_005_006"）
        if trigger_key.startswith("combined_"):
            clue_ids = trigger_key.replace("combined_", "").split("_")
            clue_ids = [f"clue_{cid}" for cid in clue_ids]
            if all(cid in collected_set for cid in clue_ids):
                active_lines.append(format_trigger(trigger_key, trigger_data))
        # 处理单条线索 key（如 "clue_006"）
        elif trigger_key.startswith("clue_"):
            if trigger_key in collected_set:
                active_lines.append(format_trigger(trigger_key, trigger_data))

    if not active_lines:
        return ""

    header = "【对质反应指令 - 玩家可能用以下证据质问你】\n"
    header += "当玩家向你出示或提及以下证据时，请按照对应的反应和台词提示来回应：\n\n"
    return header + "\n\n".join(active_lines)


def format_trigger(key: str, data) -> str:
    """将单条 confrontation_trigger 格式化为prompt文本。"""
    if isinstance(data, str):
        return f"▸ {key}：{data}"

    lines = [f"▸ 证据 [{key}]："]
    if isinstance(data, dict):
        if "reaction" in data:
            lines.append(f"  肢体反应：{data['reaction']}")
        if "dialogue_hint" in data:
            lines.append(f"  台词提示：{data['dialogue_hint']}")
        # 处理分阶段反应 (stage_1, stage_2, stage_3)
        for i in range(1, 4):
            stage_key = f"stage_{i}"
            if stage_key in data:
                lines.append(f"  第{i}阶段：{data[stage_key]}")
    return "\n".join(lines)

def build_unknown_facts_section(unknown_facts: List[str]) -> str:
    """将 unknown_facts 列表格式化为prompt段落。"""
    if not unknown_facts:
        return ""
    lines = ["【你不知道的事 - 被问到这些话题时请如实说不知道，不要编造】"]
    for fact in unknown_facts:
        lines.append(f"- {fact}")
    return "\n".join(lines)

def build_exploration_section(npc_id: str, npc_activities: dict) -> str:
    """将 NPC 自己的探索结果注入 prompt"""
    if not npc_activities:
        return ""
    my_activity = npc_activities.get(npc_id)
    if not my_activity:
        return ""

    lines = []
    if my_activity.get("theory"):
        lines.append(f"【你自己的调查结论】")
        lines.append(f"你通过自己的观察，目前的判断是：{my_activity['theory']}")
        lines.append(f"（你可以在对话中自然地提及这个想法，但不要像念台词一样生硬。")
        lines.append(f"  可以在合适时机说'我倒是注意到了一些事情...'之类的引入方式）")
    if my_activity.get("last_action"):
        lines.append(f"你最近的行动：{my_activity['last_action']}")

    return "\n".join(lines) if lines else ""

def build_trust_section(npc_id: str, npc_trust: dict) -> str:
    trust = npc_trust.get(npc_id, 50)
    
    if trust >= 75:
        return ("【对调查者的态度：信任】\n"
                "你比较信任这个调查者，愿意多说一些真话。"
                "如果他问到关键问题，你可以给出更多暗示（但仍不能直接暴露秘密）。")
    elif trust >= 50:
        return ("【对调查者的态度：中立】\n"
                "你对调查者没有特别的好感或恶感，正常回答问题。")
    elif trust >= 25:
        return ("【对调查者的态度：警惕】\n"
                "你不太信任这个调查者。回答尽量简短，能不说的就不说。"
                "对于敏感问题，你会故意含糊或转移话题。")
    else:
        return ("【对调查者的态度：敌对】\n"
                "你非常不信任甚至敌视调查者。你会撒谎、拒绝回答、甚至故意误导。"
                "除非有铁证指着你，否则一问三不知。")

def build_npc_system_prompt(
    npc_id: str,
    npc_profile: Dict,
    current_time: str,
    npc_location: str,
    player_clues: List[str],
    clues_db,
    npc_activities=None,
    npc_trust = None
) -> str:
    """
    为指定NPC构建专属的System Prompt（纯数据驱动）。

    所有角色指令、对质反应、未知信息均从npc_profile（JSON）中读取，
    代码本身不包含任何剧本内容。

    参数:
        npc_id:        NPC的ID (如 "npc_lidefu")
        npc_profile:   从JSON文件加载的NPC完整profile
        current_time:  当前游戏时间 (如 "辰时")
        npc_location:  NPC当前所在位置
        player_clues:  玩家已收集的线索ID列表

    返回:
        完整的system prompt字符串
    """

    static_profile = npc_profile.get("static_profile", {})
    dynamic_state = npc_profile.get("dynamic_state_template", {})
    sender = static_profile.get("name", "神秘人")

    # 从JSON读取三个新字段
    role_directive = npc_profile.get("role_directive", "")
    confrontation_triggers = npc_profile.get("confrontation_triggers", {})
    unknown_facts = npc_profile.get("unknown_facts", [])

    # 构建各部分
    clue_summary = build_player_clue_summary(player_clues,clues_db)
    confrontation_section = build_confrontation_section(confrontation_triggers, player_clues)
    unknown_section = build_unknown_facts_section(unknown_facts)
    exploration_section = build_exploration_section(npc_id, npc_activities)
    trust_section = build_trust_section(npc_id, npc_trust)

    # 组装完整prompt
    system_prompt = f"""你正在扮演剧本杀中的角色【{sender}】。

【场景信息】
当前时间：{current_time}
你当前所在位置：{npc_location}

【你的身份与性格】
{json.dumps(static_profile, ensure_ascii=False, indent=2)}

【你的当前状态与物品】
{json.dumps(dynamic_state, ensure_ascii=False, indent=2)}

{role_directive}

{confrontation_section}

{unknown_section}

{exploration_section}

{trust_section}

【玩家当前掌握的线索】
以下是玩家目前已经发现的证据，你需要据此判断自己的防线和态度：
{clue_summary}

【通用扮演规则】
1. 始终保持角色性格，用符合身份的语气和措辞说话。
2. 你的背包中如果有 'hidden'（隐藏）物品，在被玩家发现之前，绝不能在对话中直接提及。描述相关动作时必须模糊化。
3. 严格按照你的 'case_knowledge' 回答关于案发当晚的问题，不要编造你不知道的事情。
4. 如果玩家问你不知道的事（参见【你不知道的事】），就说你不知道，不要猜测或编造。
5. 你可以有情绪反应（愤怒、恐惧、厌恶等），让对话更生动自然。
6. 回复长度控制在50-150字之间，像真人对话一样自然。

【回复格式】
请仅以 JSON 格式回复，格式为：{{"reply": "你的回复内容"}}。"""

    return system_prompt



