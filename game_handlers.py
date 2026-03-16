import json
import random
from typing import Dict, List

# import from main
from npc_prompt_builder import build_npc_system_prompt
from recall_system import handle_recall
from conditional_clues import (
    get_available_conditional_clues,
    try_trigger_conditional_clue,
    get_trust_triggered_clues,
    collect_trust_clue,
    register_trust_clue_triggered,
    CONDITIONAL_CLUE_DB,
)

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
    "accepted_bribe": +20,         # 接受行贿（信任暴涨但评分受损）
    
    # 负面行为（减信任）
    "accused_wrongly": -20,        # 对该 NPC 错误指控
    "confronted_aggressively": -5, # 出示该 NPC 的隐藏证据（翻他房间）
    "tribunal_accused": -10,       # 在公堂上作为被指控方
    "searched_their_room": -8,     # 搜查了该 NPC 的房间
    "caught_searching": -15,       # 搜查时被房主撞见
    "rejected_bribe": -10,         # 拒绝行贿
}

# ── 房主试探台词（信任 25-70 时房主在场，允许进入但增加难度）──
OWNER_PROBE_LINES = {
    "npc_lidefu":   "李德福眯着眼盯着你，不动声色地挡在了行李前面。",
    "npc_zhaohu":   "赵虎冷冷地看了你一眼，双手抱在胸前，贴墙站着。",
    "npc_guqiong":  "顾琼转过身去，用背挡住了梳妆台的方向。",
    "npc_hanzijing":"韩子敬手忙脚乱地把桌上的东西往书堆底下塞。",
    "npc_qingxuzi": "清虚子嘿嘿一笑：「官爷随便看，贫道的东西都干干净净~」",
}

# ── 房主阻碍台词（信任 <25 时房主在场，完全封锁）──
OWNER_BLOCK_LINES = {
    "npc_lidefu":   "李德福拍案而起：「放肆！谁许你进咱家的房？滚出去！」",
    "npc_zhaohu":   "赵虎一把将门推上，铁塔般挡在门口，眼神如刀。",
    "npc_guqiong":  "顾琼冷哼一声：「你这鹰犬若敢踏入一步，休怪我不客气。」",
    "npc_hanzijing":"韩子敬吓得扑到门前：「官……官爷不要进来！小生什么都没有！」",
    "npc_qingxuzi": "清虚子挡在门前，脸色一变：「贫道屋里有法器结界，外人不可入内！」",
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

    # 行贿惩罚
    if d.get("bribe_accepted"):
        score = max(0, score - 15)

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

        # ── 揭穿逻辑：检测当前线索能否揭穿已记录的 NPC 陈述 ──
        all_stmts = current_state["dynamic_state"].get("npc_statements", {}).get(target_npc_id, [])
        expose_info = None
        confronted_statements = []
        for stmt in all_stmts:
            if stmt.get("confronted"):
                continue
            needed = set(stmt.get("contradiction_clues", []))
            held = set(current_state["dynamic_state"]["inventory"]["clues_collected"])
            # AND 逻辑：玩家必须持有所有矛盾线索才能揭穿
            if needed and needed.issubset(held):
                stmt["confronted"] = True
                expose_info = stmt
                confronted_statements.append({"npc": target_npc_id, "text": stmt["text"]})
                break  # 每次对质最多揭穿一条陈述

        # 根据揭穿结果构建对质消息
        if expose_info:
            stage = expose_info.get("expose_stage", "deny")
            stage_labels = {
                "deny":    "强硬否认（死不承认，转移话题，反指控玩家）",
                "partial": "部分承认（承认行为事实，但否认最严重的指控）",
                "collapse": "完全崩溃（彻底坦白，情绪失控，求饶或哭泣）",
            }
            stage_desc = stage_labels.get(stage, stage_labels["deny"])
            hint_text = expose_info.get("expose_hint", "")
            confront_user_message = (
                f"【揭穿对质】玩家将证物【{clue_name}】拍在桌上，直指你的陈述矛盾：\n"
                f"你曾声称「{expose_info['text']}」，但这份证据揭穿了你的谎言。\n"
                f"证物详情：{clue_desc}\n"
                f"请按【{stage_desc}】路径回应。"
                + (f"\n（承认后可透露：{hint_text}）" if hint_text else "")
            )
            # 把已揭穿陈述挂在 result 上，main.py 放入 status_info 通知前端
            result["confronted_statements"] = confronted_statements
        else:
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
            TIME_CYCLES = _get("TIME_CYCLES")
            owner_id = room_data.get("owner")
            owner_present = False
            if owner_id:
                npc_locs = d_state.get('npc_locations', {})
                if npc_locs.get(owner_id) == target_room:
                    owner_present = True
                    owner_name = next((n["name"] for n in NPC_LIST if n["id"] == owner_id), "主人")
                    trust = d_state.get("npc_trust", {}).get(owner_id, 50)

                    # ── 信任三档判定 ──
                    if trust >= 70:
                        # 高信任：放行
                        result["reply"] = f"{owner_name}看了你一眼，点点头让开了路。\n（信任度足够，允许进入搜查）\n\n"
                    elif trust < 25:
                        # 低信任：完全封锁
                        block_line = OWNER_BLOCK_LINES.get(owner_id,
                            f"{owner_name}怒目圆睁，挡在门口不让你进入。")
                        GameResponse = _get("GameResponse")
                        result["early_return"] = GameResponse(
                            reply_text=f"【被拦住了】\n\n{block_line}",
                            sender_name="系统阻拦",
                            new_encrypted_state=encrypt_state(current_state),
                            ui_type="text"
                        )
                        result["done"] = True
                        return result
                    else:
                        # 中间信任 (25-70)：试探——允许进入，但搜查难度 +1
                        probe_line = OWNER_PROBE_LINES.get(owner_id,
                            f"{owner_name}正在房内，用警惕的目光审视着你。")
                        result["reply"] = f"⚠ {probe_line}\n（{owner_name}在旁监视，搜查难度提升）\n\n"
                        # 标记该房间的搜查惩罚
                        penalty = d_state.setdefault("search_penalty", {})
                        penalty[target_room] = 1

            current_state['dynamic_state']['current_location'] = target_room
            if owner_id:
                adjust_trust(current_state["dynamic_state"], owner_id, "searched_their_room")

            current_time = TIME_CYCLES[d_state["time_idx"]]

            result["sender"] = "场景描述"
            result["reply"] += f"你进入了【{target_room}】。"
            result["ui_type"] = "room_view"

            # 普通家具按钮
            for furniture in room_data["furniture_list"]:
                result["ui_options"].append(UIAction(
                    label=f"▸ 检查{furniture}",
                    action_type="INSPECT",
                    payload=f"{target_room}:{furniture}"
                ))

            # ── 条件线索按钮注入 ──
            objective_clues_db = _get("objective_clues_db")
            cond_clues = get_available_conditional_clues(
                d_state, target_room, current_time, context="search"
            )
            for cc in cond_clues:
                # 光线不足时按钮标签加提示，但仍显示
                label = f"◈ {cc['button_text']}"
                if not cc["light_ok"]:
                    label += "（光线不足）"
                result["ui_options"].append(UIAction(
                    label=label,
                    action_type="INSPECT_CONDITIONAL",
                    payload=f"{target_room}:{cc['clue_id']}"
                ))

            result["ui_options"].append(UIAction(
                label="▸ 退出搜查 ",
                action_type="EXIT",
                payload="SEARCH"
            ))
            d_state["room_inspect_count"] = 0
        except IndexError:
            result["reply"] = "指令错误。"
        result["done"] = True
    
    # ── 条件线索触发（必须在 CMD_INSPECT 之前，避免 startswith 误拦截）──
    elif user_input.startswith("CMD_INSPECT_CONDITIONAL"):
        try:
            _, room_name, cond_clue_id = user_input.split(":", 2)
            d_state = current_state["dynamic_state"]
            TIME_CYCLES = _get("TIME_CYCLES")
            objective_clues_db = _get("objective_clues_db")
            ROOM_DB = _get("ROOM_DB")
            current_time = TIME_CYCLES[d_state["time_idx"]]

            success, text, added_clue_id = try_trigger_conditional_clue(
                clue_id=cond_clue_id,
                d_state=d_state,
                current_location=room_name,
                current_time=current_time,
                objective_clues_db=objective_clues_db
            )

            result["sender"] = "调查结果"
            result["ui_type"] = "room_view"

            # 重建房间按钮
            room_data = ROOM_DB.get(room_name, {})
            for furniture in room_data.get("furniture_list", []):
                result["ui_options"].append(UIAction(
                    label=f"▸ 检查{furniture}",
                    action_type="INSPECT",
                    payload=f"{room_name}:{furniture}"
                ))
            # 刷新剩余条件线索按钮
            cond_clues = get_available_conditional_clues(
                d_state, room_name, current_time, context="search"
            )
            for cc in cond_clues:
                label = f"◈ {cc['button_text']}"
                if not cc["light_ok"]:
                    label += "（光线不足）"
                result["ui_options"].append(UIAction(
                    label=label,
                    action_type="INSPECT_CONDITIONAL",
                    payload=f"{room_name}:{cc['clue_id']}"
                ))
            result["ui_options"].append(UIAction(
                label="▸ 退出搜查",
                action_type="EXIT",
                payload="SEARCH"
            ))

            if success:
                clue_name = objective_clues_db.get(added_clue_id, {}).get("name", added_clue_id)
                result["reply"] = text + f"\n\n**▪ 新线索入档：{clue_name}**"
                # 成功才计入搜查计数 / 推进时间
                d_state["room_inspect_count"] = d_state.get("room_inspect_count", 0) + 1
                if d_state["room_inspect_count"] % 2 == 0:
                    advance_time_func = _get("advance_time")
                    advance_time_func(current_state)
            else:
                # 失败（黑暗/时间不对）：反馈文本，不消耗行动点
                result["reply"] = text

        except ValueError:
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
                    # ── 信任试探惩罚：房主在场监视时搜查更难 ──
                    penalty = d_state.get("search_penalty", {}).get(room_name, 0)
                    difficulty = difficulty + penalty

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
                            # ── 推断检查 ──
                            from inference_engine import check_new_inferences, format_inference_message
                            new_infs = check_new_inferences(d_state)
                            if new_infs:
                                inf_texts = [format_inference_message(i) for i in new_infs]
                                result["reply"] += "\n\n" + "\n\n".join(inf_texts)
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
        d_state = current_state["dynamic_state"]
        current_time = TIME_CYCLES[d_state["time_idx"]]
        npc_id = request.npc_id

        result["ui_options"].append(UIAction(label="▸ 结束对话 (消耗1行动点)", action_type="EXIT", payload="TALK"))
        collected_ids = d_state["inventory"]["clues_collected"]
        if collected_ids:
            result["ui_options"].append(UIAction(label="» 出示证据对质", action_type="CONFRONT_SELECT_NPC", payload=npc_id))

        # ── 对话中条件线索按钮（如：注意李德福的手）──
        talk_context = f"talk_with_npc:{npc_id}"
        cond_clues_talk = get_available_conditional_clues(
            d_state,
            current_location=d_state.get("current_location", "大堂"),
            current_time=current_time,
            context=talk_context
        )
        for cc in cond_clues_talk:
            result["ui_options"].append(UIAction(
                label=f"◈ {cc['button_text']}",
                action_type="OBSERVE_NPC_DETAIL",
                payload=f"{npc_id}:{cc['clue_id']}"
            ))

        npc_profile = load_npc_profile(npc_id)

        if npc_profile:
            result["sender"] = npc_profile.get("static_profile", {}).get("name", "神秘人")
            npc_loc = d_state.get("npc_locations", {}).get(npc_id, "未知")
            npc_trust = d_state.get("npc_trust", {})
            npc_activities = d_state.get("npc_activities", {})
            system_prompt = build_npc_system_prompt(
                npc_id=npc_id, npc_profile=npc_profile,
                current_time=current_time,
                npc_location=npc_loc,
                player_clues=collected_ids,
                clues_db=objective_clues_db,
                npc_activities=npc_activities,
                npc_trust=npc_trust
            )
            npc_history = get_npc_history(current_state, npc_id)
            messages = build_llm_messages(system_prompt, npc_history, user_input)
            result["reply"] = await call_llm(system_prompt, messages, model_id)
            save_npc_history(current_state, npc_id, user_input, result["reply"])

            # ── 陈述提取：检测本轮对话是否触发了可证伪陈述 ──
            confrontable_stmts = npc_profile.get("confrontable_statements", [])
            new_statements = []
            user_input_lower = user_input.lower()
            reply_lower = result["reply"].lower()
            for stmt in confrontable_stmts:
                stmt_id = stmt["id"]
                # 避免重复记录已触发过的陈述
                already = d_state.setdefault("npc_statements", {}).get(npc_id, [])
                if any(s["id"] == stmt_id for s in already):
                    continue
                # 检测触发关键词（玩家输入或 NPC 回复中包含任意一个关键词即触发）
                keywords = stmt.get("trigger_keywords", [])
                if any(kw in user_input_lower or kw in reply_lower for kw in keywords):
                    entry = {
                        "id": stmt_id,
                        "text": stmt["statement_text"],
                        "contradiction_clues": stmt.get("contradiction_clues", []),
                        "expose_stage": stmt.get("expose_stage", "deny"),
                        "expose_hint": stmt.get("expose_hint", ""),
                        "confronted": False,
                    }
                    d_state["npc_statements"].setdefault(npc_id, []).append(entry)
                    new_statements.append({"npc": result["sender"], "text": stmt["statement_text"]})

            # 把新陈述挂在 result 上，main.py 会将其放入 status_info
            if new_statements:
                result["new_statements"] = new_statements

            # ── 信任双向博弈：对话中的三档行为 ──
            trust_val = d_state.get("npc_trust", {}).get(npc_id, 50)

            # 高信任（≥70）：NPC 主动提供线索提示
            if trust_val >= 70:
                act_data = d_state.get("npc_activities", {}).get(npc_id, {})
                if act_data.get("theory"):
                    sender_name = result["sender"]
                    result["reply"] += (
                        f"\n\n💡 {sender_name}压低声音凑近你："
                        f"「{act_data['theory']}」"
                    )

            # 低信任（<25）：NPC 散布谣言 / 敌意干扰
            elif trust_val < 25:
                sender_name = result["sender"]
                _LOW_TRUST_REACTIONS = {
                    "npc_lidefu":   "「咱家觉得你这个密卫……办事不太牢靠啊。」他意味深长地看了赵虎一眼。",
                    "npc_zhaohu":   "赵虎冷冷地瞥了你一眼，手不自觉地摸向腰间佩刀。",
                    "npc_guqiong":  "顾琼别过脸去：「跟鹰犬说话，脏了我的嘴。」她不愿再多说一个字。",
                    "npc_hanzijing":"韩子敬吞吞吐吐：「小生……小生什么都不知道……」说完缩进角落。",
                    "npc_qingxuzi": "清虚子嘿嘿冷笑：「官爷要问就问吧，反正贫道说什么你都不信。」",
                }
                hostility = _LOW_TRUST_REACTIONS.get(npc_id, f"{sender_name}显然不想和你多说。")
                result["reply"] += f"\n\n⚠ {hostility}"

            # 中间信任（25-50）且是李德福：触发行贿抉择（仅一次）
            if npc_id == "npc_lidefu" and 25 <= trust_val <= 50:
                bribe_key = "lidefu_bribe_offered"
                if not d_state.get(bribe_key):
                    d_state[bribe_key] = True
                    result["reply"] += (
                        "\n\n李德福忽然压低声音：「密卫大人，查案辛苦了。"
                        "咱家这里有些银两……不成敬意。你看这案子，"
                        "就不必太……较真了吧？」"
                    )
                    result["ui_options"].append(UIAction(
                        label="◇ 收下银两（获取信任，但……）",
                        action_type="ACCEPT_BRIBE", payload="lidefu"
                    ))
                    result["ui_options"].append(UIAction(
                        label="◆ 严词拒绝",
                        action_type="REJECT_BRIBE", payload="lidefu"
                    ))

        else:
            result["reply"] = "找不到档案"

        current_state["dynamic_state"]["last_talk_npc"] = npc_id
        result["done"] = True

    # ── 对话中细节观察（触发对话型条件线索）──
    elif user_input.startswith("CMD_OBSERVE_NPC_DETAIL"):
        try:
            _, npc_id, cond_clue_id = user_input.split(":", 2)
            d_state = current_state["dynamic_state"]
            current_time = TIME_CYCLES[d_state["time_idx"]]
            objective_clues_db = _get("objective_clues_db")
            current_location = d_state.get("current_location", "大堂")

            success, text, added_clue_id = try_trigger_conditional_clue(
                clue_id=cond_clue_id,
                d_state=d_state,
                current_location=current_location,
                current_time=current_time,
                objective_clues_db=objective_clues_db
            )

            result["ui_type"] = "chat_mode"
            result["sender"] = "观察"
            result["ui_options"].append(UIAction(
                label="▸ 结束对话 (消耗1行动点)",
                action_type="EXIT", payload="TALK"
            ))
            if d_state["inventory"]["clues_collected"]:
                result["ui_options"].append(UIAction(
                    label="» 出示证据对质",
                    action_type="CONFRONT_SELECT_NPC", payload=npc_id
                ))

            # 刷新剩余观察按钮
            talk_context = f"talk_with_npc:{npc_id}"
            for cc in get_available_conditional_clues(
                d_state, current_location, current_time, context=talk_context
            ):
                result["ui_options"].append(UIAction(
                    label=f"◈ {cc['button_text']}",
                    action_type="OBSERVE_NPC_DETAIL",
                    payload=f"{npc_id}:{cc['clue_id']}"
                ))

            if success:
                clue_name = objective_clues_db.get(added_clue_id, {}).get("name", added_clue_id)
                result["reply"] = text + f"\n\n**▪ 新线索入档：{clue_name}**"
            else:
                result["reply"] = text

        except ValueError:
            result["reply"] = "指令错误。"
        result["done"] = True

    return result


# ------------------------------------------
# 公堂对质系统 (tribunal)
# ------------------------------------------
async def handle_tribunal(user_input, request, current_state, model_id):
    """处理全员公堂系统:
       CMD_SHOW_TRIBUNAL_MENU  → 选呈堂证物
       CMD_TRIBUNAL_TOPIC:clue_id → 选首要质问对象
       CMD_TRIBUNAL_EXECUTE:npc_id → 执行全员公堂（LLM生成焦点回应+旁听者反应）
       CMD_TRIBUNAL_REDIRECT:npc_id → 5秒内转向追问另一人（复用EXECUTE路径）
       CMD_TRIBUNAL_CLOSE → 结束公堂，消耗时间
       （旧指令 CMD_TRIBUNAL_SELECT_A/B 保留兼容，不再使用）
    """
    UIAction = _get("UIAction")
    NPC_LIST = _get("NPC_LIST")
    objective_clues_db = _get("objective_clues_db")
    TIME_CYCLES = _get("TIME_CYCLES")
    call_llm = _get("call_llm")
    load_npc_profile = _get("load_npc_profile")
    advance_time_func = _get("advance_time")

    result = {"reply": "", "sender": "公堂", "ui_type": "text",
              "ui_options": [], "bg_img": None, "done": False}
    d_state = current_state["dynamic_state"]
    MAX_TRIBUNALS = 3

    # ── 公堂菜单：选呈堂证物 ──────────────────────────────────────────────
    if user_input == "CMD_SHOW_TRIBUNAL_MENU":
        used = d_state.get("tribunal_count", 0)
        if used >= MAX_TRIBUNALS:
            result["reply"] = '李德福不耐烦地挥手："够了够了，咱家不是来看你唱戏的！"'
            result["done"] = True
            return result

        collected_ids = d_state["inventory"]["clues_collected"]
        if not collected_ids:
            result["reply"] = "你尚无线索可呈堂，先去收集证据吧。"
            result["done"] = True
            return result

        result["reply"] = (
            f"◆ **召集公堂**（已用 {used}/{MAX_TRIBUNALS} 次）\n\n"
            "所有人将聚集大堂，你当众出示证物并质问在场之人。\n"
            "※ 结束公堂后消耗 2 个时辰\n\n"
            "请选择呈堂证物："
        )
        result["ui_type"] = "select_clue"
        for cid in collected_ids:
            clue = objective_clues_db.get(cid)
            if clue:
                result["ui_options"].append(UIAction(
                    label=f"▪ {clue['name']}",
                    action_type="TRIBUNAL_TOPIC",
                    payload=cid
                ))
        result["ui_options"].append(UIAction(label="‹ 取消", action_type="CANCEL", payload="MAIN"))
        result["done"] = True

    # ── 选好证物 → 选首要质问对象 ────────────────────────────────────────
    elif user_input.startswith("CMD_TRIBUNAL_TOPIC:"):
        clue_id = user_input.split(":", 1)[1]
        d_state["temp_tribunal_clue"] = clue_id
        clue = objective_clues_db.get(clue_id, {})
        result["reply"] = f"证物【{clue.get('name', '未知')}】已置于桌上。请点击上方头像选择质问对象。"
        result["ui_type"] = "tribunal_mode"
        result["done"] = True

    # ── 执行全员公堂质问 ─────────────────────────────────────────────────
    elif user_input.startswith("CMD_TRIBUNAL_EXECUTE:"):
        focus_npc_id = user_input.split(":", 1)[1]
        clue_id = d_state.get("temp_tribunal_clue", "")
        clue = objective_clues_db.get(clue_id, {})
        clue_name = clue.get("name", "未知证物")
        clue_desc = clue.get("description", "")
        focus_profile = load_npc_profile(focus_npc_id)
        focus_name = focus_profile["static_profile"]["name"] if focus_profile else "未知"

        # ── 构建旁听者摘要 ──
        bystander_lines = []
        bystander_npc_ids = []
        for npc in NPC_LIST:
            if npc["id"] == focus_npc_id:
                continue
            profile = load_npc_profile(npc["id"])
            if not profile:
                continue
            trust = d_state.get("npc_trust", {}).get(npc["id"], 50)
            # 取该旁听者对焦点 NPC 的 relationship 描述
            rels = profile.get("dynamic_state_template", {}).get("relationships", {})
            rel_desc = ""
            for k, v in rels.items():
                if k.lower() == focus_npc_id.replace("npc_", ""):
                    rel_desc = v.get("description", "")
                    break
            # 取该旁听者对当前线索的推断（来自 exploration_config.theories）
            clue_theory = profile.get("exploration_config", {}).get("theories", {}).get(clue_id, {}).get("theory", "")
            bystander_lines.append(
                f"  {profile['static_profile']['name']}"
                f"（信任度{trust}，性格：{'、'.join(profile['static_profile']['personality']['traits'][:2])}）\n"
                f"    对{focus_name}的看法：{rel_desc or '不熟悉'}\n"
                f"    对此证物的推断：{clue_theory or '无特别看法'}"
            )
            bystander_npc_ids.append(npc["id"])

        bystander_summary = "\n".join(bystander_lines)

        # ── 取焦点 NPC 信任度与已有陈述 ──
        focus_trust = d_state.get("npc_trust", {}).get(focus_npc_id, 50)
        focus_stmts = d_state.get("npc_statements", {}).get(focus_npc_id, [])
        recorded_stmts_text = ""
        if focus_stmts:
            lines = [f"  「{s['text']}」（{'已被揭穿' if s.get('confronted') else '尚未揭穿'}）"
                     for s in focus_stmts]
            recorded_stmts_text = "\n已记录的陈述：\n" + "\n".join(lines)

        # ── 焦点 NPC 的 role_directive（作战指令）──
        focus_directive = focus_profile.get("role_directive", "") if focus_profile else ""

        tribunal_prompt = f"""你是一个古风悬疑剧本的导演。现在进入「全员公堂」环节。

【场景】
调查者（李密卫）将所有人召集大堂，当众出示证物【{clue_name}】：{clue_desc}
首要质问对象：{focus_name}（信任度{focus_trust}/100）

【{focus_name} 的完整档案】
静态背景：{json.dumps(focus_profile.get('static_profile', {}), ensure_ascii=False) if focus_profile else '未知'}
行为准则：{focus_directive}
{recorded_stmts_text}

【旁听者列表（每人给出一句简短的肢体/神情反应，不超过15字/人）】
{bystander_summary}

【输出要求】
请严格以 JSON 格式返回，不要包含任何 markdown 代码块标记：
{{
  "focus_reply": "{focus_name}的回答（2-4句，符合其秘密和行为准则，结合证物内容）",
  "bystander_reactions": [
    {{"name": "旁听者姓名", "reaction": "简短肢体/神情（不超过15字）", "suspicion_shift": "increase/decrease/none"}}
  ],
  "redirect_hint": "建议下一个转向追问的对象姓名，若无则为空字符串"
}}

注意：
- {focus_name} 的回复必须符合其秘密和行为准则，不能主动泄露终极秘密
- 旁听者的反应要体现其性格和与焦点人物的关系
- suspicion_shift: increase=该旁听者神色慌张/可疑, decrease=放松/如释重负, none=无明显变化"""

        messages = [
            {"role": "system", "content": tribunal_prompt},
            {"role": "user", "content": f"请围绕证物【{clue_name}】对{focus_name}展开公堂质问。"}
        ]

        raw = await call_llm(tribunal_prompt, messages, model_id)

        # ── 解析 LLM JSON 输出 ──
        import re as _re
        try:
            clean = _re.sub(r"```json|```", "", raw).strip()
            parsed = json.loads(clean)
        except Exception:
            parsed = {"focus_reply": raw, "bystander_reactions": [], "redirect_hint": ""}

        focus_reply = parsed.get("focus_reply", raw)
        reactions = parsed.get("bystander_reactions", [])
        redirect_hint = parsed.get("redirect_hint", "")

        # ── 组合显示文本（只保留焦点 NPC 回复）──
        result["reply"] = f"**{focus_name}：** {focus_reply}"
        result["sender"] = focus_name
        result["ui_type"] = "tribunal_mode"

        result["ui_options"].append(UIAction(
            label="◆ 结束公堂", action_type="TRIBUNAL_CLOSE", payload="CLOSE"
        ))

        # ── 信任度调整（静默执行）──
        adjust_trust(d_state, focus_npc_id, "tribunal_accused")
        trust_map = d_state.setdefault("npc_trust", {})
        for r in reactions:
            npc_match = next((n["id"] for n in NPC_LIST if n["name"] == r["name"]), None)
            if npc_match:
                if r.get("suspicion_shift") == "increase":
                    trust_map[npc_match] = max(0, trust_map.get(npc_match, 50) - 5)
                elif r.get("suspicion_shift") == "decrease":
                    trust_map[npc_match] = min(100, trust_map.get(npc_match, 50) + 5)

        result["done"] = True

    # ── 结束公堂：消耗 2 个时辰 ─────────────────────────────────────────
    elif user_input == "CMD_TRIBUNAL_CLOSE":
        # 直接推进 2 个时辰（无论进入时 ap_used_this_cycle 为何值）
        d_state["ap_used_this_cycle"] = 0
        current_idx = d_state["time_idx"]
        new_idx = current_idx + 2
        if new_idx > 11:
            d_state["day"] = d_state.get("day", 1) + 1
            d_state["time_idx"] = new_idx - 12
        else:
            d_state["time_idx"] = new_idx
        # 调用一次 advance_time 触发 NPC 探索
        advance_time_func(current_state)

        d_state["tribunal_count"] = d_state.get("tribunal_count", 0) + 1
        d_state.pop("temp_tribunal_clue", None)
        d_state.pop("tribunal_session", None)

        result["reply"] = (
            f"公堂散场。\n"
            f"（当前：第{d_state['day']}日 {TIME_CYCLES[d_state['time_idx']]}）"
        )
        result["ui_type"] = "text"
        result["done"] = True

    # ── 兼容旧指令（SELECT_A / SELECT_B），重定向到新菜单 ────────────────
    elif user_input.startswith("CMD_TRIBUNAL_SELECT_A") or user_input.startswith("CMD_TRIBUNAL_SELECT_B"):
        result["reply"] = "公堂流程已更新，请重新召集公堂。"
        result["ui_type"] = "text"
        result["ui_options"].append(UIAction(
            label="» 重新召集公堂", action_type="SHOW_TRIBUNAL_MENU", payload=""
        ))
        result["done"] = True

    return result

async def handle_recall_cmd(user_input, request, current_state, model_id):
    """处理 CMD_SHOW_RECALL_MENU / CMD_RECALL_* 指令（纯只读，不消耗 AP）"""
    RECALL_CMDS = {
        "CMD_SHOW_RECALL_MENU", "CMD_RECALL_CLUES",
        "CMD_RECALL_INFERENCES", "CMD_RECALL_TIMELINE"
    }
    if user_input not in RECALL_CMDS:
        return {"done": False, "reply": "", "sender": "系统",
                "ui_type": "text", "ui_options": [], "bg_img": None}

    UIAction = _get("UIAction")
    d_state = current_state["dynamic_state"]
    objective_clues_db = _get("objective_clues_db")

    res = handle_recall(user_input, d_state, objective_clues_db)

    ui_opts = [
        UIAction(label=o["label"], action_type=o["action_type"], payload=o["payload"])
        for o in res["ui_options"]
    ]

    return {
        "reply": res["reply"],
        "sender": "调查笔记",
        "ui_type": res["ui_type"],
        "ui_options": ui_opts,
        "bg_img": None,
        "done": True,
    }
    