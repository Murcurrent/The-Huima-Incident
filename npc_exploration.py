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

        # ---- 步骤 4：生成可观测动态描述（供前端 sighting feed）----
        npc_name = npc["name"]
        npc_loc = d_state["npc_locations"].get(npc_id, "某处")
        _IDLE_SIGHTINGS = [
            f"匆匆从{npc_loc}方向走来，神色慌张",
            f"在{npc_loc}附近来回踱步，欲言又止",
            f"低着头快步经过，似乎不想被人注意",
            f"站在{npc_loc}门口张望了一会儿，又缩了回去",
        ]
        # 如果有 last_action 就用，否则随机生成一个idle描述
        if not activities[npc_id].get("last_action"):
            if random.random() < 0.3:
                activities[npc_id]["last_action"] = random.choice(_IDLE_SIGHTINGS)

        # ---- 步骤 5：低信任 NPC 散布谣言 ----
        trust_val = d_state.get("npc_trust", {}).get(npc_id, 50)
        if trust_val < 25:
            low_trust_rumors = config.get("low_trust_rumors", [])
            if low_trust_rumors:
                activities[npc_id]["last_action"] = random.choice(low_trust_rumors)
            elif random.random() < 0.3:
                _RUMOR_TEMPLATES = [
                    f"在角落和别人窃窃私语，似乎在说你的坏话",
                    f"冷笑着看你一眼，故意挡住了某个方向",
                    f"和旁人嘀咕了几句，对方看向你的眼神变了",
                ]
                activities[npc_id]["last_action"] = random.choice(_RUMOR_TEMPLATES)


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