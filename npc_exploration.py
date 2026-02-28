"""
NPC 自主探索引擎
每次 advance_time 时调用，更新 NPC 的位置、发现和推断
"""
import random
from typing import Dict, List


def run_npc_exploration(global_state: Dict, npc_list: List, time_cycles: List,
                        all_locations: List, load_npc_profile_func) -> None:
    """
    在每个时间推进时为所有 NPC 执行探索逻辑。
    直接修改 global_state（原地更新）。
    """
    d_state = global_state["dynamic_state"]
    current_time = time_cycles[d_state["time_idx"]]
    activities = d_state.setdefault("npc_activities", {})

    for npc in npc_list:
        npc_id = npc["id"]

        # 初始化该 NPC 的 activities（如果不存在）
        if npc_id not in activities:
            activities[npc_id] = {"discovered": [], "theory": "", "last_action": ""}

        # 加载探索配置
        profile = load_npc_profile_func(npc_id)
        if not profile:
            continue
        config = profile.get("exploration_config")
        if not config:
            # 没有探索配置的 NPC，保持旧的随机移动
            if random.random() < 0.4:
                d_state["npc_locations"][npc_id] = random.choice(all_locations)
            continue

        # ---- 步骤 1：智能移动 ----
        prefs = config.get("location_preferences", {})
        move_prob = config.get("move_probability", 0.3)
        preferred_loc = prefs.get(current_time, prefs.get("default"))

        if preferred_loc and random.random() > move_prob:
            # 大概率去偏好位置
            d_state["npc_locations"][npc_id] = preferred_loc
        else:
            # 小概率随机移动
            d_state["npc_locations"][npc_id] = random.choice(all_locations)

        # ---- 步骤 2：概率性搜证 ----
        discover_prob = config.get("discover_probability", 0.25)
        can_discover = config.get("can_discover", [])
        already_found = activities[npc_id]["discovered"]

        # 过滤掉已经发现的
        discoverable = [c for c in can_discover if c not in already_found]

        if discoverable and random.random() < discover_prob:
            new_clue = random.choice(discoverable)
            already_found.append(new_clue)

            # ---- 步骤 3：更新推断 ----
            theories = config.get("theories", {})
            best_theory = _match_best_theory(already_found, theories)
            if best_theory:
                activities[npc_id]["theory"] = best_theory["theory"]
                activities[npc_id]["last_action"] = best_theory["action"]
            else:
                # 没有匹配的推断模板，用通用描述
                activities[npc_id]["last_action"] = f"似乎在四处查看"


def _match_best_theory(discovered: List[str], theories: Dict) -> Dict:
    """
    从推断模板中匹配最佳的一条。
    优先匹配组合线索（key 含 '+'），其次单条线索。
    返回匹配的 theory dict，或 None。
    """
    best = None
    best_count = 0  # 匹配的线索数量，越多越优先

    discovered_set = set(discovered)

    for key, theory_data in theories.items():
        if "+" in key:
            # 组合线索
            required = set(key.split("+"))
            if required.issubset(discovered_set):
                if len(required) > best_count:
                    best = theory_data
                    best_count = len(required)
        else:
            # 单条线索
            if key in discovered_set:
                if best_count < 1:
                    best = theory_data
                    best_count = 1
                # 单条不覆盖已有的组合匹配

    return best