"""
inference_engine.py
证据组合推断系统

逻辑：
  - INFERENCE_DB 定义所有推断规则，每条规则有 required_clues（触发条件）
    和 unlocks（解锁的推断结论 ID）
  - 玩家每收集一条新线索后，调用 check_new_inferences() 返回新触发的推断
  - 推断结论存入 d_state["inferences_unlocked"]，不会重复触发
  - 推断有两种类型：
      "insight"  — 侦探内心独白，帮助玩家理解线索关系（不直接给答案）
      "unlock"   — 解锁新的游戏选项（如对质时多出一个追问选项）
"""

from typing import Dict, List, Set

# ─────────────────────────────────────────────
#  推断规则库
# ─────────────────────────────────────────────
INFERENCE_DB = {

    # ── 凶器推断链 ──────────────────────────────────────

    "inf_weapon_silk": {
        "id": "inf_weapon_silk",
        "type": "insight",
        "required_clues": {"clue_002", "clue_003", "clue_019"},
        # X形勒痕 + 死者手部丝状物 + 拂尘马尾凌乱
        "title": "凶器：细丝状物",
        "text": (
            "X形交叉的勒痕、死者指缝中的丝状残留、拂尘上凌乱的马尾毛……"
            "三者指向同一个方向——凶器是某种极细的丝线，"
            "而拂尘可能是它最后被藏匿的地方。"
        ),
        "hint_for_search": "仔细检查那柄拂尘",  # 提示玩家下一步去哪
    },

    "inf_weapon_confirmed": {
        "id": "inf_weapon_confirmed",
        "type": "insight",
        "required_clues": {"clue_002", "clue_012"},
        # X形勒痕 + 锦套内的乌金丝拂尘
        "title": "凶器确认：乌金丝",
        "text": (
            "锦套里的拂尘柄中藏着一根乌金丝，柄上有大力使用后的裂纹。"
            "X形勒痕与这根极细却足够坚韧的金丝完全吻合。"
            "这就是杀人的凶器，而且来自宫廷。"
        ),
        "hint_for_search": None,
    },

    # ── 凶手行迹链 ──────────────────────────────────────

    "inf_killer_path": {
        "id": "inf_killer_path",
        "type": "insight",
        "required_clues": {"clue_005", "clue_006"},
        # 宽大脚印从后门进 + 赵虎房金疮药味
        "title": "凶手行迹：从后门进出",
        "text": (
            "泥地上宽大深重的那串脚印没有折返路线——凶手不是从大堂走的。"
            "结合赵虎房间若有若无的金创药气味，"
            "他可能从外墙翻入后院，得手后又从外墙离开。"
            "脚踝的伤……是死者临终前抓伤的吗？"
        ),
        "hint_for_search": "检查二楼走廊外墙",
    },

    "inf_killer_wound": {
        "id": "inf_killer_wound",
        "type": "insight",
        "required_clues": {"clue_003", "clue_006"},
        # 死者右手指甲血痕 + 金疮药
        "title": "死者抓伤了凶手",
        "text": (
            "死者右手指甲缝有血痕——他在死前抓伤了某人。"
            "赵虎房间的金创药味说明他近期有伤在身。"
            "这两条线索合在一起，意义已经很明确了。"
        ),
        "hint_for_search": None,
    },

    "inf_rope_binding": {
        "id": "inf_rope_binding",
        "type": "insight",
        "required_clues": {"clue_006", "clue_005"},
        # 金疮药 + 脚印（先有clue_006才解锁床底搜查提示）
        "title": "赵虎有不在场的漏洞",
        "text": (
            "宽大的脚印直通佛龛，没有返回路线。"
            "赵虎号称守在李德福门口，但若他走的是外墙这条路，"
            "大堂里就不会有人察觉他曾经离开。"
            "他的床铺下面，或许藏着答案。"
        ),
        "hint_for_search": "仔细搜查赵虎床铺",
    },

    # ── 身份揭秘链 ──────────────────────────────────────

    "inf_victim_identity": {
        "id": "inf_victim_identity",
        "type": "insight",
        "required_clues": {"clue_007", "clue_020"},
        # 加密绢帛（「旧内侍」「清除」）+ 荷包「运」字
        "title": "死者真实身份",
        "text": (
            "绢帛上「旧内侍」「清除」几字，荷包内里绣着「运」字。"
            "两件东西都指向同一个人——"
            "张三不是普通的店小二，他曾经是宫里的人，"
            "而且有人专程奉命来「清除」他。"
        ),
        "hint_for_search": "在大堂侧屋张三床铺夹缝中继续搜查",
    },

    "inf_conspiracy": {
        "id": "inf_conspiracy",
        "type": "insight",
        "required_clues": {"clue_007", "clue_012", "clue_020"},
        # 密旨 + 乌金丝拂尘（「运」字柄）+ 荷包「运」字
        "title": "有预谋的宫廷授权谋杀",
        "text": (
            "密旨、乌金丝、荷包上相同的「运」字——"
            "三者拼出了一幅完整的图：死者是前掌印太监李福运，"
            "而他的死不是意外，是有人带着宫廷授权特意来此了结他。"
            "幕后黑手，就在这家驿站里。"
        ),
        "hint_for_search": None,
    },

    # ── 目击者链 ──────────────────────────────────────

    "inf_gu_saw_body": {
        "id": "inf_gu_saw_body",
        "type": "insight",
        "required_clues": {"clue_005", "clue_014"},
        # 细窄脚印在尸体旁徘徊折返 + 灶房烧靴（尺码偏小）
        "title": "有人比清虚子更早发现尸体",
        "text": (
            "细窄的脚印在尸体旁短暂停留后慌乱折返。"
            "灶房炉膛里那双烧了一半的男靴，尺码偏小，内里是绸缎——"
            "这不是男人的靴子。有个女人曾经到过后院，"
            "发现了尸体，然后急忙销毁证据。"
        ),
        "hint_for_search": None,
    },

    "inf_han_saw_body": {
        "id": "inf_han_saw_body",
        "type": "insight",
        "required_clues": {"clue_017", "clue_018"},
        # 泥泞折扇 + 烧残反诗
        "title": "韩子敬深夜去过后院",
        "text": (
            "折扇是韩子敬的，扇骨湘妃竹，扇面上他亲笔题诗。"
            "他深夜去后院做什么？烧反诗的人又为何如此惊慌？"
            "他很可能目睹了案发后的现场，却选择了沉默。"
        ),
        "hint_for_search": None,
    },

    # ── 覆托立盏暗号链 ─────────────────────────────────

    "inf_secret_signal": {
        "id": "inf_secret_signal",
        "type": "insight",
        "required_clues": {"clue_015", "clue_011"},
        # 李德福茶托倒扣 + 大堂茶盏正放
        "title": "死者发出了求救暗号",
        "text": (
            "李德福房间的茶托被底朝天扣着，茶碗却四平八稳立在上面。"
            "这种「覆托立盏」在宫廷中是一种极隐秘的求救暗号。"
            "是张三上茶时故意为之——他在向谁求救？求的又是什么？"
        ),
        "hint_for_search": None,
    },

    # ── 指套物证链 ─────────────────────────────────────

    "inf_finger_guard": {
        "id": "inf_finger_guard",
        "type": "insight",
        "required_clues": {"clue_003", "clue_015", "clue_012"},
        # 死者左拳握紧 + 覆托立盏（与李德福的接触）+ 乌金丝柄上「运」字
        "title": "死者握着什么",
        "text": (
            "死者左手握成死拳，指骨淤青，"
            "覆托立盏说明他与李德福有过秘密接触，"
            "而那柄刻着「运」字的拂尘柄显然与李福运有关。"
            "死者的左拳里……会不会握着他从李德福身上取走的东西？"
        ),
        "hint_for_search": "用力掰开死者的左拳",
    },

}

# ─────────────────────────────────────────────
#  核心函数
# ─────────────────────────────────────────────

def check_new_inferences(d_state: Dict) -> List[Dict]:
    """
    根据当前已收集线索，检查哪些推断首次满足触发条件。
    返回新触发的推断列表（每条只触发一次）。
    同时把已解锁的 id 写入 d_state["inferences_unlocked"]。
    """
    collected: Set[str] = set(d_state["inventory"]["clues_collected"])
    already: Set[str]   = set(d_state.setdefault("inferences_unlocked", []))

    newly_triggered = []
    for inf_id, inf in INFERENCE_DB.items():
        if inf_id in already:
            continue
        if inf["required_clues"].issubset(collected):
            newly_triggered.append(inf)
            already.add(inf_id)

    d_state["inferences_unlocked"] = list(already)
    return newly_triggered


def get_all_unlocked(d_state: Dict) -> List[Dict]:
    """返回所有已解锁推断，供「回想」界面使用。"""
    unlocked_ids = set(d_state.get("inferences_unlocked", []))
    return [INFERENCE_DB[i] for i in unlocked_ids if i in INFERENCE_DB]


def get_hint_for_next_step(d_state: Dict) -> List[str]:
    """
    返回当前已解锁推断中，hint_for_search 不为空的提示列表。
    用于状态栏「下一步线索提示」功能（P1）。
    """
    hints = []
    for inf in get_all_unlocked(d_state):
        h = inf.get("hint_for_search")
        if h:
            hints.append(h)
    return hints


def format_inference_message(inf: Dict) -> str:
    """把一条推断格式化为聊天气泡文本。"""
    return f"【推断：{inf['title']}】\n\n{inf['text']}"
