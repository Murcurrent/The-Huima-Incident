import json
import random
from typing import Dict, List

# import from main
from npc_prompt_builder import build_npc_system_prompt

_ctx = {}  # shared info container

def init(context: dict):
    """main.py 启动时调用，把共享数据传进来"""
    _ctx.update(context)

def _get(key):
    return _ctx[key]

#trust rules system
TRUST_RULES = {
    # 正面行为（加信任）
    "talked_nicely": +5,           # 友好对话一轮
    "showed_relevant_clue": +8,    # 出示与该 NPC 无关的线索（表示坦诚）
    "tribunal_vindicated": +15,    # 公堂对质中该 NPC 被证明清白
    
    # 负面行为（减信任）
    "accused_wrongly": -20,        # 对该 NPC 错误指控
    "confronted_aggressively": -5, # 出示该 NPC 的隐藏证据（翻他房间）
    "tribunal_accused": -10,       # 在公堂上作为被指控方
    "searched_their_room": -8,     # 搜查了该 NPC 的房间
}

def adjust_trust(d_state, npc_id, rule_key):
    """根据规则调整信任度，限制在 0-100"""
    delta = TRUST_RULES.get(rule_key, 0)
    trust = d_state.setdefault("npc_trust", {})
    current = trust.get(npc_id, 50)
    trust[npc_id] = max(0, min(100, current + delta))

#new handlers must go after talk handler

# ------------------------------------------
# ◈ 游戏统计辅助函数
# ------------------------------------------
def _compute_game_stats(current_state):
    """从 dynamic_state 中提取各种统计数据，供结局和报告使用"""
    d = current_state["dynamic_state"]
    objective_clues_db = _get("objective_clues_db")
    NPC_LIST = _get("NPC_LIST")

    collected = d["inventory"]["clues_collected"]
    total_clues = len(objective_clues_db)
    found_clues = len(collected)

    # 关键线索检测
    key_clues = {"clue_012", "clue_005", "clue_006", "clue_007", "clue_015"}
    found_key = key_clues & set(collected)

    # 信任度
    trust = d.get("npc_trust", {})
    ally_npc = max(trust, key=trust.get, default=None)  # 最信任的NPC
    enemy_npc = min(trust, key=trust.get, default=None)  # 最敌对的NPC
    ally_name = next((n["name"] for n in NPC_LIST if n["id"] == ally_npc), "无") if ally_npc else "无"
    enemy_name = next((n["name"] for n in NPC_LIST if n["id"] == enemy_npc), "无") if enemy_npc else "无"

    # 对质历史
    confrontations = d.get("confrontation_used", {})
    total_confrontations = sum(len(v) for v in confrontations.values())

    # 公堂次数
    tribunal_count = d.get("tribunal_count", 0)

    # 用时
    day = d.get("day", 1)
    time_idx = d.get("time_idx", 0)

    # 评级
    score = 0
    score += min(found_clues * 5, 50)          # 线索收集（最多50分）
    score += len(found_key) * 8                 # 关键线索加分（最多40分）
    score += min(total_confrontations * 3, 15)  # 对质加分（最多15分）
    if tribunal_count > 0:
        score += 5                              # 使用了公堂
    # 效率加分：第一天完成比第二天高
    if day == 1:
        score += 15
    elif day == 2 and time_idx <= 6:
        score += 8

    if score >= 90:
        rank, rank_title = "S", "神断青天"
    elif score >= 75:
        rank, rank_title = "A", "明镜高悬"
    elif score >= 55:
        rank, rank_title = "B", "初窥端倪"
    elif score >= 35:
        rank, rank_title = "C", "雾里看花"
    else:
        rank, rank_title = "D", "两眼一抹黑"

    return {
        "found_clues": found_clues,
        "total_clues": total_clues,
        "found_key_count": len(found_key),
        "total_key_count": len(key_clues),
        "ally_name": ally_name,
        "ally_trust": trust.get(ally_npc, 0) if ally_npc else 0,
        "enemy_name": enemy_name,
        "enemy_trust": trust.get(enemy_npc, 0) if enemy_npc else 0,
        "total_confrontations": total_confrontations,
        "tribunal_count": tribunal_count,
        "day": day,
        "time_idx": time_idx,
        "score": score,
        "rank": rank,
        "rank_title": rank_title,
        "trust_data": trust,
        "collected_ids": collected,
        "confrontations": confrontations,
    }


# ------------------------------------------
# ◈ 结局叙事模板（LLM 生成用）
# ------------------------------------------
_ENDING_TEMPLATES = {
    "TRUE_END": {
        "title": "血染回马驿",
        "base_narration": (
            "你深吸一口气，将那柄断了机关的金镶玉拂尘高高举起，大声喝道：\n\n"
            '"在场诸位听好了——杀害张三的凶手，是李德福指使其护卫赵虎所为！'
            '凶器就是这柄拂尘中暗藏的乌金丝！"\n\n'
        ),
    },
    "NORMAL_END": {
        "title": "不安的良心",
        "base_narration": (
            "你沉默了很久。窗外的雨不知何时停了，但你心里的雨才刚刚开始下。\n\n"
            "你转过身，手指颤抖着指向那个无辜的道士清虚子：\n"
            '"凶手……是道士。他的拂尘就在尸体旁边，谋财害命，证据确凿。"\n\n'
        ),
    },
    "BAD_END": {
        "title": "含冤而死",
        "base_narration": (
            "李德福看着你呈上的证据，脸上的表情从期待变成了失望，最后变成了冰冷的杀意。\n\n"
            '"就这？" 他的声音轻得像一缕烟，却让在场所有人的血液都凝固了。\n\n'
            '"赵虎。"\n\n'
            "只有两个字。赵虎已经懂了。\n\n"
        ),
    },
}


async def _generate_ending_narration(ending_type: str, current_state: dict, model_id: str) -> str:
    """用 LLM 生成个性化结局叙事"""
    call_llm = _get("call_llm")
    NPC_LIST = _get("NPC_LIST")
    stats = _compute_game_stats(current_state)
    d = current_state["dynamic_state"]
    trust = stats["trust_data"]

    template = _ENDING_TEMPLATES.get(ending_type, _ENDING_TEMPLATES["BAD_END"])

    # 构建NPC态度摘要
    npc_attitudes = []
    for npc in NPC_LIST:
        npc_id = npc["id"]
        t = trust.get(npc_id, 50)
        if t >= 70:
            attitude = "信任并感激调查者"
        elif t >= 40:
            attitude = "对调查者态度中立"
        elif t >= 20:
            attitude = "对调查者心存警惕"
        else:
            attitude = "敌视调查者"
        npc_attitudes.append(f"  {npc['name']}(信任度{t})：{attitude}")

    npc_attitude_text = "\n".join(npc_attitudes)

    # 构建对质摘要
    confrontation_summary = ""
    confrontations = stats["confrontations"]
    if confrontations:
        objective_clues_db = _get("objective_clues_db")
        parts = []
        for npc_id, clue_ids in confrontations.items():
            npc_name = next((n["name"] for n in NPC_LIST if n["id"] == npc_id), npc_id)
            clue_names = [objective_clues_db.get(c, {}).get("name", c) for c in clue_ids]
            parts.append(f"  对{npc_name}出示了：{', '.join(clue_names)}")
        confrontation_summary = "\n".join(parts)

    ending_prompt = f"""你是一个古风悬疑剧本的编剧。现在需要为玩家生成一段沉浸式的结局叙事。

【结局类型】{ending_type} - {template['title']}
【开场段落（已写好，不要重复）】
{template['base_narration']}

【角色设定（严格遵守，不得偏离）】
- 李德福：内廷总管太监，老谋深算，权倾朝野。手无缚鸡之力但一句话可定人生死。
- 赵虎：李德福的贴身护卫，身手高强，沉默寡言，忠犬型杀手。唯一的武力担当。佩刀。
- 顾琼：女扮男装的杨氏后人，身怀武艺（暗藏匕首），性格刚烈倔强。她来驿站是为了寻机刺杀李德福复仇。
- 韩子敬：胆小懦弱的落魄书生，手无寸铁，遇事只会发抖和逃跑。他唯一的"武器"是笔墨。绝对不可能有任何武力行为。
- 清虚子：油嘴滑舌的江湖骗子道士，贪财怕死，遇到危险只会求饶或耍嘴皮子。没有任何武力。

【严禁让韩子敬或清虚子做出任何武力行为（拔刀、格挡、打斗等）。韩子敬只能做：作证、喊话、挡在前面求情、瑟瑟发抖等文弱书生的行为。清虚子只能做：求饶、狡辩、逃跑等行为。只有顾琼和赵虎可以有武力动作。

【玩家的调查数据】
- 收集线索：{stats['found_clues']}/{stats['total_clues']}
- 关键线索：{stats['found_key_count']}/{stats['total_key_count']}
- 对质次数：{stats['total_confrontations']}
- 公堂次数：{stats['tribunal_count']}
- 用时：第{stats['day']}日
- 调查评级：{stats['rank']}({stats['rank_title']})
- 最信任的NPC：{stats['ally_name']}（信任度{stats['ally_trust']}）
- 最敌对的NPC：{stats['enemy_name']}（信任度{stats['enemy_trust']}）

【各NPC对调查者的态度】
{npc_attitude_text}

【玩家的对质记录】
{confrontation_summary if confrontation_summary else '无对质记录'}

【写作要求】
请紧接开场段落，续写结局的后续发展。要求：

1. **根据结局类型展开不同的故事线**：
   - TRUE_END（公布真相）：玩家当众揭露真相后，李德福和赵虎会如何反应？其他NPC（根据信任度）会如何应对？最终结果取决于玩家的人际关系。
     * 如果顾琼信任度>=70，她可能拔出暗藏的匕首出手相助
     * 如果韩子敬信任度>=70，他可能鼓起勇气大声作证、或挡在玩家身前（但他是文弱书生，不可能有武力行为）
     * 如果清虚子信任度>=70，他可能帮忙打掩护、制造混乱让玩家脱身
     * 如果所有NPC信任度都很低，则无人相助，结局更惨烈
   - NORMAL_END（替罪羊）：清虚子被冤枉后的场景，李德福的满意，玩家内心的挣扎。
     * 如果清虚子信任度高，他被拖走时的哀求会更让人心碎
     * 写到玩家回京后的内心独白，夜不能寐
   - BAD_END（判断错误）：赵虎动手前的紧张氛围，其他人的反应，最终的结局。
     * 如果有NPC信任度很高，可能有人试图求情但无济于事

2. **必须包含至少2个NPC的具体反应**（严格符合上述角色设定）

3. **最后一段写一句诗意的收束语**，概括整个故事

4. 字数控制在 **400-600字**

5. 文风要求：古风白话混合，画面感强，像一部武侠悬疑小说的结尾

请以 JSON 格式返回：{{"reply": "续写的结局内容"}}"""

    messages = [
        {"role": "system", "content": ending_prompt},
        {"role": "user", "content": f"请为 {ending_type} 结局生成叙事。"}
    ]

    narration = await call_llm(ending_prompt, messages, model_id)
    return template["base_narration"] + narration


# ------------------------------------------
# 指认系统 (accuse + endings)
# ------------------------------------------
async def handle_accuse(user_input, request, current_state, model_id):
    """处理: CMD_SHOW_ACCUSE_MENU, CMD_ACCUSE_TARGET, CMD_ACCUSE_EVIDENCE, CMD_ENDING_*, CMD_SHOW_REPORT"""

    UIAction = _get("UIAction")
    NPC_LIST = _get("NPC_LIST")
    SOLUTION = _get("SOLUTION")
    objective_clues_db = _get("objective_clues_db")
    encrypt_state = _get("encrypt_state")

    result = {"reply": "", "sender": "系统", "ui_type": "text", "ui_options": [], "bg_img": None, "done": False}

    # --- 指认菜单 ---
    if user_input == "CMD_SHOW_ACCUSE_MENU":
        result["reply"] = '你决定结束调查，向李德福指认凶手。\n\n李德福坐在太师椅上，冷冷地看着你："说吧，是谁杀了张三？"'
        result["sender"] = "李德福"
        result["ui_type"] = "select_npc"
        for npc in NPC_LIST:
            if npc["id"] != "npc_lidefu":
                result["ui_options"].append(UIAction(label=f"» 指认 {npc['name']}", action_type="ACCUSE_TARGET", payload=npc["id"]))
        result["done"] = True

    # --- 选凶手 → 选凶器 ---
    elif user_input.startswith("CMD_ACCUSE_TARGET"):
        target_id = user_input.split(":", 1)[1]
        current_state["dynamic_state"]["temp_accuse_target"] = target_id
        target_name = next((n["name"] for n in NPC_LIST if n["id"] == target_id), "未知")
        result["sender"] = "李德福"
        result["reply"] = f'"{target_name}？" 李德福眯起眼睛，"证据呢？他是用什么杀的人？"'
        result["ui_type"] = "select_clue"
        collected_ids = current_state["dynamic_state"]["inventory"]["clues_collected"]
        if not collected_ids:
            result["reply"] += "\n\n(你两手空空，没有任何证据...)"
            result["ui_options"].append(UIAction(label="… 哑口无言", action_type="ACCUSE_EVIDENCE", payload="none"))
        else:
            for cid in collected_ids:
                clue = objective_clues_db.get(cid)
                if clue:
                    result["ui_options"].append(UIAction(label=f"▪ {clue['name']}", action_type="ACCUSE_EVIDENCE", payload=cid))
        result["done"] = True

    # --- 判定结局（转场） ---
    elif user_input.startswith("CMD_ACCUSE_EVIDENCE"):
        evidence_id = user_input.split(":", 1)[1]
        target_id = current_state["dynamic_state"].get("temp_accuse_target")
        is_killer_correct = (target_id == SOLUTION["killer_id"])
        is_weapon_correct = (evidence_id == SOLUTION["weapon_id"])

        if is_killer_correct and is_weapon_correct:
            # 正确指认 → 进入道德抉择
            result["sender"] = "李德福"
            result["reply"] = (
                '李德福看着那柄损坏的金镶玉拂尘，沉默了许久。\n'
                '他枯瘦的手指摩挲着拂尘柄上刻着的那个"运"字，眼神深不见底。\n\n'
                '突然，他笑了。笑得阴森可怖，像深夜里坟场的野猫。\n\n'
                '"好啊……好啊！真是咱家的好密卫。"\n\n'
                '他缓缓站起身，凑到你耳边，声音轻得只有你能听见：\n'
                '"这拂尘确实是咱家的。人也是赵虎杀的。但那又如何？"\n'
                '"那个张三——哼，他的真名叫李福运，是当年马嵬坡之变的知情人。'
                '皇上要他死，懂吗？"\n\n'
                '他退后一步，整了整衣襟，恢复了那副居高临下的嘴脸：\n'
                '"现在，你有两条路。"\n\n'
                '"其一：你当众把这些公之于众。然后——你觉得皇上会让一个知道了宫闱秘辛的密卫活着回京？"\n\n'
                '"其二：随便找个替死鬼结案。回京后，咱家保你荣华富贵，前途无量。"\n\n'
                '他的目光如刀，死死钉在你脸上。\n'
                '"选吧。"'
            )
            result["ui_type"] = "chat_mode"
            result["ui_options"].append(UIAction(label="◆ 公布真相——纵万死不辞", action_type="ENDING_REVEAL", payload="TRUE"))
            result["ui_options"].append(UIAction(label="◇ 隐瞒真相——留得青山在", action_type="ENDING_SCAPEGOAT", payload="FALSE"))
        else:
            # 错误指认 → BAD END (LLM生成)
            current_state["dynamic_state"]["ending_type"] = "BAD_END"
            narration = await _generate_ending_narration("BAD_END", current_state, model_id)
            current_state["dynamic_state"]["game_over"] = True
            result["sender"] = "结局：含冤而死"
            result["reply"] = narration + '\n\n**【BAD END：含冤而死】**'
            result["ui_type"] = "text"
            result["ui_options"].append(UIAction(label="▸ 查看案件卷宗", action_type="SHOW_REPORT", payload="BAD_END"))
        result["done"] = True

    # --- 结局分支：公布真相 ---
    elif user_input == "CMD_ENDING_REVEAL":
        current_state["dynamic_state"]["ending_type"] = "TRUE_END"
        narration = await _generate_ending_narration("TRUE_END", current_state, model_id)
        current_state["dynamic_state"]["game_over"] = True
        result["sender"] = "结局：血染回马驿"
        result["reply"] = narration + '\n\n**【TRUE END：血染回马驿】**'
        result["ui_type"] = "text"
        result["ui_options"].append(UIAction(label="▸ 查看案件卷宗", action_type="SHOW_REPORT", payload="TRUE_END"))
        result["done"] = True

    # --- 结局分支：替罪羊 ---
    elif user_input == "CMD_ENDING_SCAPEGOAT":
        current_state["dynamic_state"]["ending_type"] = "NORMAL_END"
        narration = await _generate_ending_narration("NORMAL_END", current_state, model_id)
        current_state["dynamic_state"]["game_over"] = True
        result["sender"] = "结局：不安的良心"
        result["reply"] = narration + '\n\n**【NORMAL END：不安的良心】**'
        result["ui_type"] = "text"
        result["ui_options"].append(UIAction(label="▸ 查看案件卷宗", action_type="SHOW_REPORT", payload="NORMAL_END"))
        result["done"] = True

    # --- 案件卷宗报告 ---
    elif user_input.startswith("CMD_SHOW_REPORT"):
        ending_type = user_input.split(":", 1)[1] if ":" in user_input else d_state_ending(current_state)
        stats = _compute_game_stats(current_state)
        TIME_CYCLES = _get("TIME_CYCLES")

        # 构建报告 JSON，前端会解析渲染
        report_data = {
            "ending_type": ending_type,
            "ending_title": _ENDING_TEMPLATES.get(ending_type, {}).get("title", "未知结局"),
            "rank": stats["rank"],
            "rank_title": stats["rank_title"],
            "score": stats["score"],
            "found_clues": stats["found_clues"],
            "total_clues": stats["total_clues"],
            "found_key_count": stats["found_key_count"],
            "total_key_count": stats["total_key_count"],
            "total_confrontations": stats["total_confrontations"],
            "tribunal_count": stats["tribunal_count"],
            "day": stats["day"],
            "time_period": TIME_CYCLES[stats["time_idx"]],
            "ally_name": stats["ally_name"],
            "ally_trust": stats["ally_trust"],
            "enemy_name": stats["enemy_name"],
            "enemy_trust": stats["enemy_trust"],
        }

        result["reply"] = "REPORT_DATA:" + json.dumps(report_data, ensure_ascii=False)
        result["sender"] = "案件卷宗"
        result["ui_type"] = "ending_report"
        result["ui_options"].append(UIAction(label="▸ 重新开始", action_type="RELOAD", payload="RELOAD"))
        result["done"] = True

    return result


def d_state_ending(current_state):
    """从 state 中获取结局类型的辅助函数"""
    return current_state["dynamic_state"].get("ending_type", "BAD_END")


# ------------------------------------------
# 对质系统 (confront)
# ------------------------------------------
async def handle_confront(user_input, request, current_state, model_id):
    """处理: CMD_SHOW_CONFRONT_MENU, CMD_CONFRONT_SELECT_NPC, CMD_CONFRONT_WITH_CLUE"""
    
    UIAction = _get("UIAction")
    NPC_LIST = _get("NPC_LIST")
    objective_clues_db = _get("objective_clues_db")
    TIME_CYCLES = _get("TIME_CYCLES")
    load_npc_profile = _get("load_npc_profile")
    call_llm = _get("call_llm")
    get_npc_history = _get("get_npc_history")
    save_npc_history = _get("save_npc_history")
    build_llm_messages = _get("build_llm_messages")
    
    result = {"reply": "", "sender": "系统", "ui_type": "text", "ui_options": [], "bg_img": None, "done": False}
    
    # 对质A: 选择对质对象
    if user_input == "CMD_SHOW_CONFRONT_MENU":
        result["reply"] = "你决定亮出证据，逼问嫌疑人。请选择对质对象："
        result["ui_type"] = "select_npc"
        for npc in NPC_LIST:
            result["ui_options"].append(UIAction(label=f"» 对质 {npc['name']}", action_type="CONFRONT_SELECT_NPC", payload=npc["id"]))
        result["ui_options"].append(UIAction(label="‹ 取消", action_type="CANCEL", payload="MAIN"))
        result["done"] = True
    
    # 对质B: 选择出示的线索
    elif user_input.startswith("CMD_CONFRONT_SELECT_NPC"):
        target_npc_id = user_input.split(":", 1)[1]
        target_name = next((n["name"] for n in NPC_LIST if n["id"] == target_npc_id), "未知")
        collected_ids = current_state["dynamic_state"]["inventory"]["clues_collected"]
        if not collected_ids:
            result["reply"] = "你还没有收集到任何线索，无法进行对质。"
        else:
            result["reply"] = f"你要用什么证据质问【{target_name}】？"
            result["ui_type"] = "select_clue"
            for cid in collected_ids:
                clue = objective_clues_db.get(cid)
                if clue:
                    result["ui_options"].append(UIAction(label=f"▪ {clue['name']}", action_type="CONFRONT_WITH_CLUE", payload=f"{target_npc_id}:{cid}"))
            result["ui_options"].append(UIAction(label="‹ 返回选人", action_type="SHOW_CONFRONT_MENU", payload="BACK"))
        result["done"] = True
    
    # 对质C: 执行对质（含 LLM 调用）
    elif user_input.startswith("CMD_CONFRONT_WITH_CLUE"):
        parts = user_input.split(":", 2)
        target_npc_id = parts[1]
        confront_clue_id = parts[2]
        clue = objective_clues_db.get(confront_clue_id)
        clue_name = clue["name"] if clue else "未知证据"
        clue_desc = clue["description"] if clue else ""

        # 记录对质历史
        confront_history = current_state["dynamic_state"].setdefault("confrontation_used", {})
        npc_confront_list = confront_history.setdefault(target_npc_id, [])
        if confront_clue_id not in npc_confront_list:
            npc_confront_list.append(confront_clue_id)

        confront_user_message = (
            f"【对质】玩家将证物【{clue_name}】拍在桌上，质问你：\n"
            f"证物详情：{clue_desc}\n"
            f"请根据你的 confrontation_triggers 中关于 {confront_clue_id} 的指引来回应。"
            f"如果没有对应的trigger，请根据你的角色性格和认知自然回应。"
        )

        result["ui_type"] = "chat_mode"
        result["ui_options"].append(UIAction(label="▸ 结束对话 (消耗1行动点)", action_type="EXIT", payload="TALK"))
        collected_ids = current_state["dynamic_state"]["inventory"]["clues_collected"]
        if collected_ids:
            result["ui_options"].append(UIAction(label="» 继续出示证据", action_type="CONFRONT_SELECT_NPC", payload=target_npc_id))

        npc_profile = load_npc_profile(target_npc_id)
        if npc_profile:
            result["sender"] = npc_profile.get("static_profile", {}).get("name", "神秘人")
            npc_loc = current_state['dynamic_state'].get('npc_locations', {}).get(target_npc_id, "未知")
            npc_trust = current_state["dynamic_state"].get("npc_trust", {})
            npc_activities = current_state["dynamic_state"].get("npc_activities", {})
            system_prompt = build_npc_system_prompt(
                npc_id=target_npc_id, npc_profile=npc_profile,
                current_time=TIME_CYCLES[current_state['dynamic_state']['time_idx']],
                npc_location=npc_loc,
                player_clues=current_state["dynamic_state"]["inventory"]["clues_collected"],
                clues_db=objective_clues_db,
                npc_activities=npc_activities,
                npc_trust=npc_trust
            )
            npc_history = get_npc_history(current_state, target_npc_id)
            messages = build_llm_messages(system_prompt, npc_history, confront_user_message)
            result["reply"] = await call_llm(system_prompt, messages, model_id)
            save_npc_history(current_state, target_npc_id, f"[对质：出示{clue_name}]", result["reply"])
        else:
            result["reply"] = "找不到档案"
        
        #adjust trust for confronting
        adjust_trust(current_state["dynamic_state"], target_npc_id, "confronted_aggressively")
        result["done"] = True
    
    return result


# ------------------------------------------
# 搜查系统 (search + inspect + room enter)
# ------------------------------------------
async def handle_search(user_input, request, current_state, model_id):
    """处理: CMD_SHOW_SEARCH_MENU, CMD_ENTER_ROOM, CMD_INSPECT"""
    
    UIAction = _get("UIAction")
    ROOM_DB = _get("ROOM_DB")
    objective_clues_db = _get("objective_clues_db")
    encrypt_state = _get("encrypt_state")
    # GameResponse 只在房间进入被阻挡时需要直接返回，这里用 special_response 标记
    
    result = {"reply": "", "sender": "系统", "ui_type": "text", "ui_options": [], "bg_img": None, "done": False, "early_return": None}
    
    if user_input == "CMD_SHOW_SEARCH_MENU":
        result["reply"] = "请选择你要搜查的区域："
        result["ui_type"] = "select_room"
        for room_key, room_data in ROOM_DB.items():
            result["ui_options"].append(UIAction(label=room_data["name"], action_type="SEARCH_ENTER", payload=room_key))
        result["done"] = True
    
    elif user_input.startswith("CMD_ENTER_ROOM"):
        try:
            target_room = user_input.split(":", 1)[1]
            room_data = ROOM_DB.get(target_room)
            if not room_data:
                result["reply"] = "无法进入该区域。"
                result["done"] = True
                return result

            d_state = current_state["dynamic_state"]
            NPC_LIST = _get("NPC_LIST")
            owner_id = room_data.get("owner")
            if owner_id:
                npc_locs = d_state.get('npc_locations', {})
                if npc_locs.get(owner_id) == target_room:
                    owner_name = next((n["name"] for n in NPC_LIST if n["id"] == owner_id), "主人")
                    # 信任度 >= 70 时 NPC 允许进入
                    trust = d_state.get("npc_trust", {}).get(owner_id, 50)
                    if trust >= 70:
                        result["reply"] = f"{owner_name}看了你一眼，点点头让开了路。\n（信任度足够，允许进入搜查）\n\n"
                    else:
                        GameResponse = _get("GameResponse")
                        result["early_return"] = GameResponse(
                            reply_text=f"【无法进入】\n\n{owner_name}正在房内，你无法搜查。",
                            sender_name="系统阻拦",
                            new_encrypted_state=encrypt_state(current_state),
                            ui_type="text"
                        )
                        result["done"] = True
                        return result

            current_state['dynamic_state']['current_location'] = target_room
            # 搜查 NPC 房间，信任度下降
            if owner_id:
                adjust_trust(current_state["dynamic_state"], owner_id, "searched_their_room")

            result["sender"] = "场景描述"
            result["reply"] += f"你进入了【{target_room}】。"
            result["ui_type"] = "room_view"
            for furniture in room_data["furniture_list"]:
                result["ui_options"].append(UIAction(label=f"▸ 检查{furniture}", action_type="INSPECT", payload=f"{target_room}:{furniture}"))
            result["ui_options"].append(UIAction(label="▸ 退出搜查 ", action_type="EXIT", payload="SEARCH"))
            d_state["room_inspect_count"] = 0
        except IndexError:
            result["reply"] = "指令错误。"
        result["done"] = True
    
    elif user_input.startswith("CMD_INSPECT"):
        try:
            _, room_name, furniture_name = user_input.split(":")
            room_data = ROOM_DB.get(room_name)
            d_state = current_state["dynamic_state"]

            inspect_count = d_state.get("room_inspect_count", 0) + 1
            d_state["room_inspect_count"] = inspect_count
            if inspect_count % 2 == 0:
                advance_time = _get("advance_time")
                advance_time(current_state)
                time_idx = d_state["time_idx"]
                TIME_CYCLES = _get("TIME_CYCLES")
                result["reply"] = f"⏳ 你搜查了一阵，时间流逝……（当前：第{d_state.get('day')}日 {TIME_CYCLES[time_idx]}）\n\n"
            else:
                result["reply"] = ""

            clue_id = room_data["furniture_map"].get(furniture_name)
            custom_text = room_data.get("inspect_texts", {}).get(furniture_name)
            result["sender"] = "调查结果"
            result["ui_type"] = "room_view"
            for furniture in room_data["furniture_list"]:
                result["ui_options"].append(UIAction(label=f"▸ 检查{furniture}", action_type="INSPECT", payload=f"{room_name}:{furniture}"))
            result["ui_options"].append(UIAction(label="▸ 退出搜查", action_type="EXIT", payload="SEARCH"))
            if custom_text:
                result["reply"] += custom_text
            elif clue_id:
                clue = objective_clues_db.get(clue_id)
                if clue:
                    difficulty = clue.get("search_difficulty", 1)
                    
                    # 记录搜查次数
                    search_counts = d_state.setdefault("search_counts", {})
                    furniture_key = f"{room_name}:{furniture_name}"
                    search_counts[furniture_key] = search_counts.get(furniture_key, 0) + 1
                    current_count = search_counts[furniture_key]
                    
                    if current_count >= difficulty:
                        # 找到了！
                        result["reply"] += f"你在【{furniture_name}】处发现了：\n\n▪ **{clue['name']}**\n{clue['description']}"
                        if clue['id'] not in d_state["inventory"]["clues_collected"]:
                            d_state["inventory"]["clues_collected"].append(clue['id'])
                    else:
                        # 还没找到，给提示
                        hints = {
                            1: "你仔细翻找了一番，觉得这里似乎还藏着什么……",
                            2: "你更加用力地搜查，手指碰到了什么东西的边缘……",
                        }
                        result["reply"] += hints.get(current_count, "你继续搜查，似乎快要发现什么了……")
                else:
                    result["reply"] = "什么也没发现。"
            else:
                if room_name == "后院" and furniture_name == "泥地":
                    result["reply"] = "泥地上脚印杂乱（发现线索：混乱的足迹）。此外，尸体也横陈于此。"
                else:
                    result["reply"] = "只是普通的杂物。"
        except ValueError:
            result["reply"] = "指令错误。"
        result["done"] = True
    
    return result


# ------------------------------------------
# NPC 对话系统 (talk + free chat)
# ------------------------------------------
async def handle_talk(user_input, request, current_state, model_id):
    """处理: CMD_SHOW_TALK_MENU, NPC自由对话"""
    
    UIAction = _get("UIAction")
    NPC_LIST = _get("NPC_LIST")
    TIME_CYCLES = _get("TIME_CYCLES")
    objective_clues_db = _get("objective_clues_db")
    load_npc_profile = _get("load_npc_profile")
    call_llm = _get("call_llm")
    get_npc_history = _get("get_npc_history")
    save_npc_history = _get("save_npc_history")
    build_llm_messages = _get("build_llm_messages")
    
    result = {"reply": "", "sender": "系统", "ui_type": "text", "ui_options": [], "bg_img": None, "done": False}
    
    if user_input == "CMD_SHOW_TALK_MENU":
        result["reply"] = "请选择你要问话的对象："
        result["ui_type"] = "select_npc"
        for npc in NPC_LIST:
            result["ui_options"].append(UIAction(label=npc["name"], action_type="TALK", payload=npc["id"]))
        result["done"] = True
    
    # NPC 自由对话（request.npc_id 有值时）
    elif request.npc_id:
        result["ui_type"] = "chat_mode"
        result["ui_options"].append(UIAction(label="▸ 结束对话 (消耗1行动点)", action_type="EXIT", payload="TALK"))
        collected_ids = current_state["dynamic_state"]["inventory"]["clues_collected"]
        if collected_ids:
            result["ui_options"].append(UIAction(label="» 出示证据对质", action_type="CONFRONT_SELECT_NPC", payload=request.npc_id))

        npc_profile = load_npc_profile(request.npc_id)
       
        if npc_profile:
            result["sender"] = npc_profile.get("static_profile", {}).get("name", "神秘人")
            npc_loc = current_state['dynamic_state'].get('npc_locations', {}).get(request.npc_id, "未知")
            npc_trust = current_state["dynamic_state"].get("npc_trust", {})
            npc_activities = current_state["dynamic_state"].get("npc_activities", {})
            system_prompt = build_npc_system_prompt(
                npc_id=request.npc_id, npc_profile=npc_profile,
                current_time=TIME_CYCLES[current_state['dynamic_state']['time_idx']],
                npc_location=npc_loc,
                player_clues=current_state["dynamic_state"]["inventory"]["clues_collected"],
                clues_db=objective_clues_db,
                npc_activities=npc_activities,
                npc_trust=npc_trust
            )
            npc_history = get_npc_history(current_state, request.npc_id)
            messages = build_llm_messages(system_prompt, npc_history, user_input)
            result["reply"] = await call_llm(system_prompt, messages, model_id)
            save_npc_history(current_state, request.npc_id, user_input, result["reply"])
        else:
            result["reply"] = "找不到档案"
        
        # 记录当前对话对象，供 CMD_EXIT:TALK 时加信任度
        current_state["dynamic_state"]["last_talk_npc"] = request.npc_id
        result["done"] = True
    
    return result


# ------------------------------------------
# 公堂对质系统 (tribunal)
# ------------------------------------------
async def handle_tribunal(user_input, request, current_state, model_id):
    """处理: CMD_SHOW_TRIBUNAL_MENU, CMD_TRIBUNAL_SELECT_A, 
             CMD_TRIBUNAL_SELECT_B, CMD_TRIBUNAL_TOPIC, CMD_TRIBUNAL_EXECUTE"""
    
    UIAction = _get("UIAction")
    NPC_LIST = _get("NPC_LIST")
    objective_clues_db = _get("objective_clues_db")
    TIME_CYCLES = _get("TIME_CYCLES")
    call_llm = _get("call_llm")
    load_npc_profile = _get("load_npc_profile")
    
    result = {"reply": "", "sender": "系统", "ui_type": "text", 
            "ui_options": [], "bg_img": None, "done": False}
    d_state = current_state["dynamic_state"]
    
    MAX_TRIBUNALS = 3
    
    # --- 公堂菜单 ---
    if user_input == "CMD_SHOW_TRIBUNAL_MENU":
        used = d_state.get("tribunal_count", 0)
        if used >= MAX_TRIBUNALS:
            result["reply"] = '李德福不耐烦地挥手："够了够了，咱家不是来看你唱戏的！"'
            result["done"] = True
            return result
        
        result["reply"] = (
            f"◆ **召集公堂** (已用 {used}/{MAX_TRIBUNALS} 次)\n\n"
            f"你可以把两个嫌疑人叫到大堂当面对质。\n"
            f"※ 这会消耗大量时间（跳过2个时辰）\n\n"
            f"请选择第一个对质对象："
        )
        result["ui_type"] = "select_npc"
        for npc in NPC_LIST:
            result["ui_options"].append(UIAction(
                label=f"» {npc['name']}", 
                action_type="TRIBUNAL_SELECT_A", 
                payload=npc["id"]
            ))
        result["ui_options"].append(UIAction(
            label="‹ 取消", action_type="CANCEL", payload="MAIN"
        ))
        result["done"] = True
    
    # --- 选第一个人 ---
    elif user_input.startswith("CMD_TRIBUNAL_SELECT_A"):
        npc_a_id = user_input.split(":", 1)[1]
        d_state["temp_tribunal_a"] = npc_a_id
        npc_a_name = next((n["name"] for n in NPC_LIST if n["id"] == npc_a_id), "未知")
        
        result["reply"] = f"你选择了【{npc_a_name}】。\n请选择第二个对质对象："
        result["ui_type"] = "select_npc"
        for npc in NPC_LIST:
            if npc["id"] != npc_a_id:
                result["ui_options"].append(UIAction(
                    label=f"» {npc['name']}", 
                    action_type="TRIBUNAL_SELECT_B", 
                    payload=npc["id"]
                ))
        result["ui_options"].append(UIAction(
            label="‹ 重新选择", action_type="SHOW_TRIBUNAL_MENU", payload="BACK"
        ))
        result["done"] = True
    
    # --- 选第二个人 ---
    elif user_input.startswith("CMD_TRIBUNAL_SELECT_B"):
        npc_b_id = user_input.split(":", 1)[1]
        d_state["temp_tribunal_b"] = npc_b_id
        npc_b_name = next((n["name"] for n in NPC_LIST if n["id"] == npc_b_id), "未知")
        npc_a_id = d_state.get("temp_tribunal_a")
        npc_a_name = next((n["name"] for n in NPC_LIST if n["id"] == npc_a_id), "未知")
        
        collected_ids = d_state["inventory"]["clues_collected"]
        if not collected_ids:
            result["reply"] = "你还没有收集到任何线索，无法确定审问议题。"
            result["done"] = True
            return result
        
        result["reply"] = (
            f"【{npc_a_name}】 vs 【{npc_b_name}】\n\n"
            f"你要用什么证据作为对质议题？"
        )
        result["ui_type"] = "select_clue"
        for cid in collected_ids:
            clue = objective_clues_db.get(cid)
            if clue:
                result["ui_options"].append(UIAction(
                    label=f"▪ {clue['name']}", 
                    action_type="TRIBUNAL_EXECUTE", 
                    payload=cid
                ))
        result["done"] = True
    
    # --- 执行公堂对质 ---
    elif user_input.startswith("CMD_TRIBUNAL_EXECUTE"):
        clue_id = user_input.split(":", 1)[1]
        npc_a_id = d_state.get("temp_tribunal_a")
        npc_b_id = d_state.get("temp_tribunal_b")
        clue = objective_clues_db.get(clue_id, {})
        clue_name = clue.get("name", "未知")
        clue_desc = clue.get("description", "")
        
        npc_a_profile = load_npc_profile(npc_a_id)
        npc_b_profile = load_npc_profile(npc_b_id)
        npc_a_name = npc_a_profile["static_profile"]["name"] if npc_a_profile else "未知"
        npc_b_name = npc_b_profile["static_profile"]["name"] if npc_b_profile else "未知"
        
        # 读取双方的 relationship
        a_about_b = ""
        b_about_a = ""
        if npc_a_profile:
            rels = npc_a_profile.get("dynamic_state_template", {}).get("relationships", {})
            for key, val in rels.items():
                if key.lower().replace("_","") in npc_b_id.replace("npc_",""):
                    a_about_b = val.get("description", "")
                    break
        if npc_b_profile:
            rels = npc_b_profile.get("dynamic_state_template", {}).get("relationships", {})
            for key, val in rels.items():
                if key.lower().replace("_","") in npc_a_id.replace("npc_",""):
                    b_about_a = val.get("description", "")
                    break
        
        # 读取双方的 NPC 探索推断
        activities = d_state.get("npc_activities", {})
        a_theory = activities.get(npc_a_id, {}).get("theory", "")
        b_theory = activities.get(npc_b_id, {}).get("theory", "")

        # 读取双方的 trust 对玩家
        trust_data = d_state.get("npc_trust", {})
        a_trust_level = trust_data.get(npc_a_id, 50)
        b_trust_level = trust_data.get(npc_b_id, 50)
        
        # 构建公堂 prompt
        tribunal_prompt = f"""你是一个剧本杀的导演。现在进入"公堂对质"环节。

【场景】
调查者（李密卫）把 {npc_a_name} 和 {npc_b_name} 叫到大堂，当面对质。
调查者将证物【{clue_name}】拍在桌上：{clue_desc}

【{npc_a_name} 的身份与认知】
{json.dumps(npc_a_profile.get('static_profile', {}), ensure_ascii=False, indent=2) if npc_a_profile else '未知'}
{npc_a_name}对{npc_b_name}的看法：{a_about_b}
{npc_a_name}自己的调查推断：{a_theory if a_theory else '暂无'}
{npc_a_name}对调查者的信任度：{a_trust_level}/100

【{npc_b_name} 的身份与认知】
{json.dumps(npc_b_profile.get('static_profile', {}), ensure_ascii=False, indent=2) if npc_b_profile else '未知'}
{npc_b_name}对{npc_a_name}的看法：{b_about_a}
{npc_b_name}自己的调查推断：{b_theory if b_theory else '暂无'}
{npc_b_name}对调查者的信任度：{b_trust_level}/100

【输出要求】
请写一段 3-5 轮的对质对话，格式如下：
- 每轮包含两人各一句话
- 两人根据各自的秘密、性格、对彼此的看法来争论
- 围绕证物【{clue_name}】展开，可以互相指控、辩解、甩锅
- 信任度低的 NPC 可能对调查者说谎或不配合
- 不要暴露角色的 secrets 中的终极秘密，除非证据已经指向他
- 对话要体现性格差异，要有戏剧冲突
- 最后一轮可以不了了之（僵持）或一方情绪失控
- 控制在 300-500 字

请以 JSON 格式返回：
{{"reply": "对质内容（用 **角色名：** 开头区分发言，用 \\n\\n 分隔每轮）"}}"""

        messages = [
            {"role": "system", "content": tribunal_prompt},
            {"role": "user", "content": f"请围绕【{clue_name}】展开对质。"}
        ]
        
        result["reply"] = await call_llm(tribunal_prompt, messages, model_id)
        result["sender"] = "公堂对质"
        result["ui_type"] = "text"
        
        # 消耗时间：跳过 2 个时辰（直接修改，避免重复触发NPC探索）
        advance_time_func = _get("advance_time")
        for _ in range(7):  # 前7次只修改计数器
            d_state["ap_used_this_cycle"] = d_state.get("ap_used_this_cycle", 0) + 1
            MAX_AP = 4
            if d_state["ap_used_this_cycle"] >= MAX_AP:
                d_state["ap_used_this_cycle"] = 0
                current_idx = d_state["time_idx"]
                if current_idx == 11:
                    d_state["day"] = d_state.get("day", 1) + 1
                    d_state["time_idx"] = 0
                else:
                    d_state["time_idx"] = current_idx + 1
        # 最后一次调用 advance_time 触发一次 NPC 探索
        advance_time_func(current_state)
        
        d_state["tribunal_count"] = d_state.get("tribunal_count", 0) + 1
        
        # 双方信任度下降
        adjust_trust(d_state, npc_a_id, "tribunal_accused")
        adjust_trust(d_state, npc_b_id, "tribunal_accused")
        
        # 清理临时数据
        d_state.pop("temp_tribunal_a", None)
        d_state.pop("temp_tribunal_b", None)
        
        result["done"] = True
    
    return result
    