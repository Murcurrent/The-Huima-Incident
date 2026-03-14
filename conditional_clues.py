"""
条件线索系统 (Conditional Clues System)
========================================
管理所有需要特定触发条件才能发现的线索。

触发类型：
  sequential  - 顺序触发：持有指定线索 + 在正确地点
  combination - 组合触发：同时持有多条线索才解锁
  trust       - 信任触发：NPC信任度达标后主动给予
  time        - 时间限定：只在特定时辰可发现

对外接口：
  get_available_conditional_clues(d_state, current_location, current_time)
      → 返回当前可触发的条件线索列表（用于生成动态按钮）

  try_trigger_conditional_clue(clue_id, d_state, current_location, current_time)
      → 尝试触发某条件线索，返回结果文本或失败原因

  get_trust_triggered_clues(d_state, current_time)
      → 返回本时辰应主动推送给玩家的信任线索列表

  check_dark_attempt(clue_id, current_location, current_time)
      → 玩家在黑暗中尝试光照类线索时，返回"看不清"的反馈文本
"""

from typing import Dict, List, Optional, Tuple

# ==========================================
# 🕯️ 光照系统
# ==========================================

# 有光亮的房间及其可用时辰
# None 表示全天候可用
LIGHT_CONDITIONS: Dict[str, Optional[List[str]]] = {
    "灶房":     None,                        # 炉火全天候
    "大堂":     ["卯时","辰时","巳时","午时","未时","申时","酉时"],
    "后院":     ["卯时","辰时","巳时","午时","未时","申时"],
    "二楼走廊": ["巳时","午时","未时","申时"],   # 天窗
    # NPC 房间需要"点灯"行动，不在此列
}

def is_location_lit(location: str, current_time: str) -> bool:
    """判断当前地点在当前时辰是否有足够光线。"""
    if location not in LIGHT_CONDITIONS:
        return False
    allowed_times = LIGHT_CONDITIONS[location]
    if allowed_times is None:
        return True
    return current_time in allowed_times


# ==========================================
# 📜 条件线索数据库
# ==========================================

CONDITIONAL_CLUE_DB: Dict[str, Dict] = {

    # --------------------------------------------------
    # clue_021：拂尘中的乌金丝
    # 触发：持有拂尘+两条勒痕线索 + 有光亮的房间
    # --------------------------------------------------
    "clue_021": {
        "type": "sequential",
        "name": "拂尘中的乌金丝",
        "requires_clues": ["clue_019", "clue_002", "clue_003"],
        "valid_locations": ["灶房", "大堂", "二楼走廊", "后院"],
        "light_required": True,
        "button_text": "借光线仔细检查拂尘",
        "dark_fail_text": (
            "你把拂尘凑近眼前仔细打量，但光线太暗，"
            "只能感觉到马尾毛质地均匀柔软，看不出任何异常。"
            "也许换个亮堂的地方再看看？"
        ),
        "success_text": (
            "借着光线，你用指甲从根部开始仔细拨开马尾毛。\n\n"
            "大多数毛柔软蓬松，毫无重量。但靠近柄部的根部，"
            "你摸到一处轻微的结块——把那一撮毛拨开，"
            "其中混着三四根外观相近却截然不同的细丝：\n"
            "没有弹性，拉扯时有轻微的割手感，"
            "在光线下泛着极细微的金属光泽。\n\n"
            "你把其中一根贴近灶火，它没有燃烧，只是微微发红——\n\n"
            "**这不是马尾毛。这是金属丝。**"
        ),
        "clue_data": {
            "id": "clue_021",
            "name": "拂尘柄内的乌金丝残段",
            "location": "后院",
            "search_difficulty": 0,
            "description": (
                "借着光线，从桃木拂尘马尾毛根部分离出三四根细丝。"
                "细丝坚韧异常，有轻微割手感，贴近火焰不燃烧，只是发红——"
                "是金属材质。颜色黑亮，与马尾毛极为相近，夜间几乎无法分辨。"
                "这种材质的细丝，在民间从未见过。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_022：佛龛底座暗痕（升级版clue_004）
    # 触发：持有clue_003_new 后重新检查后院佛龛
    # --------------------------------------------------
    "clue_022": {
        "type": "sequential",
        "name": "佛龛底座暗槽",
        "requires_clues": ["clue_003_new"],
        "valid_locations": ["后院"],
        "light_required": False,
        "button_text": "重新检查佛龛底座",
        "success_text": (
            "循着鎏金指套的线索，你重新检查佛龛。\n\n"
            "底座侧面有一道极细的缝隙，用力按压后弹开，"
            "露出一个浅浅的暗槽。\n\n"
            "你把那枚从尸体手里找到的指套放了进去——**严丝合缝。**\n\n"
            "张三把什么藏在这里？"
        ),
        "clue_data": {
            "id": "clue_022",
            "name": "佛龛底座暗槽",
            "location": "后院",
            "search_difficulty": 0,
            "description": (
                "循着尸体的鎏金指套线索返回检查佛龛——"
                "底座侧面有一道极细的缝隙，用力按压后弹开，"
                "露出一个浅浅的暗槽。槽内壁有一处凹陷，很小，像是能放下什么首饰"
                "像是环形物品收纳于此。"
                "张三把什么藏在这里？"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_003_new：死者掌心压痕（clue_003的深入检查版）
    # 触发：持有clue_003 后再次检查死者手部
    # --------------------------------------------------
    "clue_003_new": {
        "type": "sequential",
        "name": "死者掌心的压痕",
        "requires_clues": ["clue_003"],
        "valid_locations": ["后院"],
        "light_required": False,
        "button_text": "用力掰开死者左拳",
        "success_text": (
            "你费力掰开死者紧握的左拳。\n\n"
            "费力掰开死者左拳后， 手掌中央有一个做工精美的鎏金指套，内侧刻着一个运字。圆形，直径约一寸，边缘隐约有细密的纹路\n\n"
            "似乎在什么地方看到过相似的东西？"

        ),
        "clue_data": {
            "id": "clue_003_new",
            "name": "掌心压痕",
            "location": "后院 (尸体)",
            "search_difficulty": 0,
            "description": (
                "费力掰开死者左拳后，手掌中央有一个做工精美的鎏金指套，"
                "内侧刻着一个运字。似乎在什么地方看到过相似的东西？"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_010_new：锦袋痕迹（大堂桌椅的深入检查）
    # 触发：持有clue_010 后检查柜台
    # --------------------------------------------------
    "clue_010_new": {
        "type": "sequential",
        "name": "柜台旁的空锦袋",
        "requires_clues": ["clue_010"],
        "valid_locations": ["大堂"],
        "light_required": False,
        "button_text": "检查柜台旁的锦袋",
        "success_text": (
            "柜台旁挂着一个空的锦袋，系口处的布料被压出了痕迹，"
            "像是原本装着什么圆形的硬物，现在已经不在了。\n\n"
            "这是张三的随身物件——他平日把什么放在这里？"
        ),
        "clue_data": {
            "id": "clue_010_new",
            "name": "柜台锦袋",
            "location": "大堂",
            "search_difficulty": 0,
            "description": (
                "张三挂在柜台边的锦袋，系口处有压痕，"
                "原本装着某物，现在已经空了。"
                "一个驿卒，随身携带这样精致的锦袋，本身就很反常。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_li_finger：注意李德福的指套
    # 触发：持有clue_003_new + clue_010_new + 与李德福对话时
    # --------------------------------------------------
    "clue_li_finger": {
        "type": "sequential",
        "name": "李德福手上的錾花金指套",
        "requires_clues": ["clue_003_new", "clue_010_new"],
        "valid_locations": ["大堂", "李德福房间"],
        "light_required": False,
        "trigger_context": "talk_with_npc",  # 在对话中触发，不是搜查
        "trigger_npc": "npc_lidefu",
        "button_text": "注意他的手",
        "success_text": (
            "你的目光落在李德福右手食指上。\n\n"
            "他戴着一枚錾花金指套，在灯光下可以看清花纹——"
            "是细密的云纹，中央錾刻着一个篆体「福」字。\n\n"
            "圆形，直径约一寸，边缘有细密的纹路……\n\n"
            "**和死者掌心的压痕，分毫不差。**"
        ),
        "clue_data": {
            "id": "clue_li_finger",
            "name": "錾花金指套",
            "location": "大堂",
            "search_difficulty": 0,
            "description": (
                "李德福右手食指上戴着一枚錾花金指套，"
                "云纹底，中央篆体「福」字，直径约一寸，边缘有细密纹路。"
                "形状与死者掌心压痕完全吻合——"
                "死者死前，曾握住这枚指套。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_025：李福运的最后字条
    # 触发：持有clue_007 + clue_020，搜查大堂侧屋床板夹缝
    # --------------------------------------------------
    "clue_025": {
        "type": "combination",
        "name": "李福运的最后字条",
        "requires_clues": ["clue_007", "clue_020"],
        "valid_locations": ["大堂侧屋"],
        "light_required": False,
        "button_text": "检查床板夹缝",
        "success_text": (
            "你蹲下，把手指插入床板与床框之间的缝隙。\n\n"
            "指尖碰到了一张叠得极小的薄纸。\n\n"
            "纸已发黄，但字迹工整秀丽，绝不是一个驿卒的手笔：\n\n"
            "「余自马嵬坡一别，改名换姓，蛰伏至今已二十三年。"
            "今日忽见故人之面，知大限将至。\n"
            "若有人得见此纸，烦转告：\n"
            "西南方向，有一人尚在人世，她的名字不可写于纸上。\n"
            "余此生唯一憾事，是那夜亲手掩埋了一个谎言。」\n\n"
            "纸角有一个墨点，像是写完后犹豫了许久，才放下了笔。"
        ),
        "clue_data": {
            "id": "clue_025",
            "name": "「李福运」字条",
            "location": "大堂侧屋",
            "search_difficulty": 0,
            "description": (
                "张三藏在床板夹缝里的字条，字迹秀丽工整，非驿卒所能。"
                "自述改名换姓蛰伏二十三年，知「故人」来访即大限将至。"
                "留言称「西南方向有一人尚在人世」，名字不可见纸。"
                "「马嵬坡」「掩埋谎言」——死者知道一个足以颠覆一切的秘密。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_023：张三床铺的体温痕迹
    # 触发：时间限定，只在丑时~卯时进入大堂侧屋
    # --------------------------------------------------
    "clue_023": {
        "type": "time",
        "name": "尸体的体温",
        "requires_clues": [],
        "valid_locations": ["后院"],
        "available_times": ["辰时", "巳时"],
        "light_required": False,
        "button_text": "感知尸体温度",
        "unavailable_text": "尸体已经冰冷僵硬，体温信息早已消散。",
        "success_text": (
            "你将手覆上死者胸口——\n\n"
            "尸身尚有残温，远未到完全僵硬的程度。\n"
            "死亡时间不超过两个时辰。\n\n"
            "**这说明：凶手就在驿站之中，现在还没走。**"
        ),
        "clue_data": {
            "id": "clue_023",
            "name": "尸体的体温",
            "location": "后院 (尸体)",
            "search_difficulty": 0,
            "description": (
                "尸身尚有残温，远未到完全僵硬的程度。"
                "死亡时间不超过两个时辰。"
                "这说明：凶手就在驿站之中，现在还没走。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_030：李德福随身携带的画像
    # 触发：持有clue_007 + clue_025，高难度搜查李德福行李
    # --------------------------------------------------
    "clue_030": {
        "type": "combination",
        "name": "李德福行李深处的画像",
        "requires_clues": ["clue_007", "clue_025"],
        "valid_locations": ["李德福房间"],
        "light_required": False,
        "button_text": "翻找行李最深处",
        "success_text": (
            "你把行李翻到最底层，手指碰到一个油纸包。\n\n"
            "展开——里面是一幅小小的工笔画像。\n"
            "画中女子年约三十，眉目间有一种沉静的美。\n\n"
            "你翻到画像背面：\n"
            "上面用针刺出密密麻麻的小孔，拼成三个字——"
            "但那三个字被人用浓墨涂黑了。\n\n"
            "墨迹渗透了纸背，无论怎么看，都认不出原来的字形。\n\n"
            "**李德福随身带着这幅画，带了多少年了？**"
        ),
        "clue_data": {
            "id": "clue_030",
            "name": "李德福行李中的画像",
            "location": "李德福房",
            "search_difficulty": 0,
            "description": (
                "工笔画像，画中女子年约三十，眉目沉静。"
                "背面用针孔拼出三个字，已被浓墨涂黑，无法辨认。"
                "李德福随身携带此画，与他「奉旨办差」的身份形成奇异的对比。"
                "那三个字，和马嵬坡之变有关吗？"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_024：断裂的绑带
    # 触发：持有clue_006，搜查赵虎房间床底
    # --------------------------------------------------
    "clue_024": {
        "type": "sequential",
        "name": "床板缝里的断裂绑带",
        "requires_clues": ["clue_006"],
        "valid_locations": ["赵虎房间"],
        "light_required": False,
        "button_text": "仔细检查床板缝隙",
        "success_text": (
            "你伸手到床板缝隙里摸索。\n\n"
            "指尖碰到一截布条——拽出来，是一段撕断的绑带，"
            "带着淡淡的血迹和金疮药的气味。\n"
            "布条的断口处纤维杂乱，像是用力过猛一把撕断的。\n\n"
            "第一次包扎时，手在抖，或者伤势比想象的严重，"
            "才会用力到把布条撕断。"
        ),
        "clue_data": {
            "id": "clue_024",
            "name": "断裂的绑带",
            "location": "赵虎房",
            "search_difficulty": 0,
            "description": (
                "撕断的布条，质地与赵虎腿上的绑带相同，"
                "带有血迹和金疮药气味。断口处纤维杂乱，"
                "是仓促包扎时用力过猛所致。"
                "伤势远比赵虎表现出来的严重，且是案发当夜紧急处理的。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_new_wall：二楼外墙划痕
    # 触发：持有clue_005 + clue_006，检查二楼走廊窗户
    # --------------------------------------------------
    "clue_new_wall": {
        "type": "combination",
        "name": "二楼外墙的攀爬痕迹",
        "requires_clues": ["clue_005", "clue_006"],
        "valid_locations": ["二楼走廊"],
        "light_required": False,
        "button_text": "检查走廊尽头的窗户外侧",
        "success_text": (
            "你打开走廊尽头的窗户，探身向外看。\n\n"
            "窗台外侧的砖面上有两道新鲜的划痕，"
            "间距与人的双手撑墙时的宽度相当。\n"
            "墙面距离下方地面约一丈，"
            "正常人不会从这里攀爬——\n\n"
            "但一个习惯了宫廷武功的人，可以做到。\n"
            "落地时会很重，脚踝承受的冲击不小。"
        ),
        "clue_data": {
            "id": "clue_new_wall",
            "name": "二楼外墙划痕",
            "location": "二楼走廊",
            "search_difficulty": 0,
            "description": (
                "二楼走廊末端窗台外侧有新鲜双手撑墙痕迹，"
                "距地面约一丈。有人昨夜从此处跳下或攀上，"
                "落地时脚踝所受冲击极大。"
                "这条路线完全绕开了楼梯，可以在不经过大堂的情况下进出二楼。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_028：清虚子的度牒（非本人）
    # 触发：搜查清虚子房间，持有clue_009
    # --------------------------------------------------
    "clue_028": {
        "type": "sequential",
        "name": "清虚子的度牒",
        "requires_clues": ["clue_009"],
        "valid_locations": ["清虚子房间"],
        "light_required": False,
        "button_text": "翻找床铺下的包裹",
        "success_text": (
            "床铺下有个扁平的油布包裹，打开——\n\n"
            "是一张正规的道士度牒，"
            "发放机构：青城山玄元观，持有人法号：清虚子。\n\n"
            "你抬头看了一眼那个满口「无量天尊」的道士，"
            "再低头看度牒上的画像。\n\n"
            "**画像上的人，和眼前这位清虚子，面貌有明显差异。**\n\n"
            "这张度牒不是他的。\n"
            "或者说——他不是原来的那个清虚子。"
        ),
        "clue_data": {
            "id": "clue_028",
            "name": "清虚子的度牒",
            "location": "清虚子房",
            "search_difficulty": 0,
            "description": (
                "青城山玄元观颁发的度牒，持有人法号清虚子。"
                "但度牒上的画像与眼前的「清虚子」面貌有明显差异。"
                "他用的是别人的度牒，还是他根本不是他自称的那个人？"
                "本案中此线索暂无直接指向，但它说明此人来历不明。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # clue_027：韩子敬的落榜文书（假阳性，有情感价值）
    # 触发：搜查韩子敬房间，持有clue_018
    # --------------------------------------------------
    "clue_027": {
        "type": "sequential",
        "name": "韩子敬的落榜文书",
        "requires_clues": ["clue_018"],
        "valid_locations": ["韩子敬房间"],
        "light_required": False,
        "button_text": "检查书页夹层",
        "success_text": (
            "你翻开桌上那摞圣贤书，"
            "从其中一本的夹层里滑出一张揉皱了又展开的文书。\n\n"
            "礼部红章，字迹正式：\n"
            "「本届春闱，应试士子韩子敬，"
            "文章立意偏颇，有悖圣意，着令落第。」\n\n"
            "落第原因一栏，朱批四个字：\n\n"
            "**「词意僭越」**\n\n"
            "这已经不是第一次了。"
        ),
        "clue_data": {
            "id": "clue_027",
            "name": "韩子敬的落榜文书",
            "location": "韩子敬房",
            "search_difficulty": 0,
            "description": (
                "礼部落第文书，朱批「词意僭越」。"
                "韩子敬此前已因文章被认定越矩而落榜，"
                "这次写反诗是有迹可循的积怨，而非一时冲动。"
                "此线索与本案主线无直接关联，但能帮助理解韩子敬其人。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },

    # --------------------------------------------------
    # 系列伏笔：枯井「勿开」
    # 触发：在后院四处查看（持有任意3条线索以上时解锁，
    #       表示玩家已经有一定调查深度）
    # --------------------------------------------------
    "clue_D": {
        "type": "sequential",
        "name": "后院角落的枯井",
        "requires_clues": ["clue_001", "clue_005"],
        "valid_locations": ["后院"],
        "light_required": False,
        "button_text": "查看后院角落",
        "success_text": (
            "后院角落有一口废弃的枯井，井口用木板钉死了。\n\n"
            "木板上有人用炭笔写了两个字：\n\n"
            "**「勿开」**\n\n"
            "字迹……你总觉得在哪见过这样的笔法。\n\n"
            "井已经废弃多年，但那两个字写得很新。"
        ),
        "clue_data": {
            "id": "clue_D",
            "name": "后院的枯井",
            "location": "后院（角落）",
            "search_difficulty": 0,
            "description": (
                "废弃枯井，井口木板上炭笔写着「勿开」二字，字迹新鲜。"
                "字迹风格与李福运的手笔相近——但无法确认。"
                "这口井里藏着什么？本案中此问题没有答案。"
            ),
            "hidden": False,
            "visible_condition": "conditional"
        }
    },
}


# ==========================================
# 🔧 核心接口函数
# ==========================================

def get_available_conditional_clues(
    d_state: Dict,
    current_location: str,
    current_time: str,
    context: str = "search"   # "search" 或 "talk_with_npc:npc_id"
) -> List[Dict]:
    """
    返回当前可以触发（显示按钮）的条件线索列表。
    包含：
      - 已满足线索条件
      - 地点正确
      - 尚未被收集
      - 时间条件满足（如有）
      - 不包含 trust 类型（那个由 get_trust_triggered_clues 处理）

    每条返回：{"clue_id": ..., "button_text": ..., "light_ok": bool}
    light_ok=False 表示按钮应显示但执行会得到"看不清"反馈。
    """
    collected = set(d_state.get("inventory", {}).get("clues_collected", []))
    results = []

    for clue_id, config in CONDITIONAL_CLUE_DB.items():
        # 跳过已收集的
        if clue_id in collected:
            continue

        ctype = config["type"]

        # trust 类型不在这里处理
        if ctype == "trust":
            continue

        # 检查地点
        valid_locs = config.get("valid_locations", [])
        if current_location not in valid_locs:
            continue

        # 检查 context（对话中触发 vs 搜查触发）
        trigger_ctx = config.get("trigger_context", "search")
        if trigger_ctx == "talk_with_npc":
            # 只在和指定 NPC 对话时出现
            expected_npc = config.get("trigger_npc", "")
            if not context.startswith("talk_with_npc:"):
                continue
            if context != f"talk_with_npc:{expected_npc}":
                continue
        else:
            # 普通搜查，跳过对话专属
            if context.startswith("talk_with_npc:"):
                continue

        # 检查时间条件（time 类型）
        if ctype == "time":
            available_times = config.get("available_times", [])
            if current_time not in available_times:
                continue

        # 检查线索前提
        requires = set(config.get("requires_clues", []))
        if not requires.issubset(collected):
            continue

        # 检查光照需求（不阻止按钮显示，只影响执行结果）
        light_required = config.get("light_required", False)
        light_ok = True
        if light_required:
            light_ok = is_location_lit(current_location, current_time)

        results.append({
            "clue_id": clue_id,
            "button_text": config["button_text"],
            "light_ok": light_ok,
        })

    return results


def try_trigger_conditional_clue(
    clue_id: str,
    d_state: Dict,
    current_location: str,
    current_time: str,
    objective_clues_db: Dict
) -> Tuple[bool, str, Optional[str]]:
    """
    尝试触发指定条件线索。

    返回 (success, text, clue_id_added)
      success=True  : 成功发现，text 是成功文本，clue_id_added 是新线索ID
      success=False : 失败，text 是失败/黑暗反馈，clue_id_added=None
    """
    config = CONDITIONAL_CLUE_DB.get(clue_id)
    if not config:
        return False, "未知的条件线索。", None

    collected = set(d_state.get("inventory", {}).get("clues_collected", []))

    # 已收集
    if clue_id in collected:
        return False, "你已经检查过这里了。", None

    # 光照检查
    if config.get("light_required", False):
        if not is_location_lit(current_location, current_time):
            return False, config.get("dark_fail_text", "光线不足，看不清楚。"), None

    # 时间检查（time类型）
    if config["type"] == "time":
        available_times = config.get("available_times", [])
        if current_time not in available_times:
            return False, config.get("unavailable_text", "现在时机不对，看不出什么。"), None

    # 成功：加入线索
    clue_data = config["clue_data"]
    clue_id_to_add = clue_data["id"]

    # 同步到 objective_clues_db（运行时注册）
    if clue_id_to_add not in objective_clues_db:
        objective_clues_db[clue_id_to_add] = clue_data

    if clue_id_to_add not in d_state["inventory"]["clues_collected"]:
        d_state["inventory"]["clues_collected"].append(clue_id_to_add)

    return True, config["success_text"], clue_id_to_add


def get_trust_triggered_clues(
    d_state: Dict,
    current_time: str
) -> List[Dict]:
    """
    返回本时辰应主动推送给玩家的信任触发线索。
    由 advance_time 调用，检查是否有 NPC 达到信任阈值且线索未发放。

    每条返回：
    {
      "clue_id": ...,
      "npc_id": ...,
      "npc_name": ...,
      "feed_text": ...,   # 动态feed里显示的模糊描述
      "trigger_text": ... # 玩家点击后的完整对话文本
    }
    """
    # 信任触发线索配置（独立于 CONDITIONAL_CLUE_DB，因为触发逻辑不同）
    TRUST_CLUES = [
        {
            "clue_id": "clue_026",  # 顾琼家书
            "npc_id": "npc_guqiong",
            "trust_threshold": 70,
            "trigger_time": ["巳时", "午时"],
            "feed_text": "顾琼似乎想和你说些什么",
            "trigger_text": (
                "顾琼在走廊拦住你，四下看了看，把一封写了一半的信递过来。\n\n"
                "「你自己看吧。」\n\n"
                "信纸上，收信人只写了「吾儿」二字，正文写道：\n"
                "「娘此行若不归，箱底红木匣内有你的身世文书。\n"
                "记住，杨氏的血不是罪，是证据。等你长大，去找——」\n\n"
                "后面被撕掉了。\n\n"
                "顾琼重新接过信，折好收进袖中，\n"
                "眼神里有什么东西一闪而过，随即恢复冷漠：\n"
                "「现在你知道我为什么不能死在这里了。」"
            ),
            "clue_data": {
                "id": "clue_026",
                "name": "顾琼的家书",
                "location": "顾琼房",
                "description": (
                    "顾琼写给孩子的未完成家书，提到「杨氏的血不是罪，是证据」。"
                    "后半段被撕去。她是杨氏后人，此行背负着远不止复仇的使命。"
                ),
                "hidden": False,
                "visible_condition": "trust"
            }
        },
        {
            "clue_id": "clue_037_testimony",  # 韩子敬脚步声证词
            "npc_id": "npc_hanzijing",
            "trust_threshold": 50,
            "trigger_time": ["午时", "未时"],
            "feed_text": "韩子敬在你门口徘徊了许久",
            "trigger_text": (
                "韩子敬鼓起勇气找到你，结结巴巴地说：\n\n"
                "「小……小生昨夜听到了一些动静，一直不敢说。」\n\n"
                "「大约丑时，有人从二楼走到楼梯口，脚步很重，"
                "是男人，靴子底有铁掌声——咚咚咚的。\n"
                "然后大概过了半炷香，同样的脚步声从楼梯上来，"
                "但走得……很不均匀，像是有一条腿不太对劲。」\n\n"
                "「小生不知道这有没有用……但小生说的都是真的，"
                "求大人不要追究小生那些诗稿！」"
            ),
            "clue_data": {
                "id": "clue_037_testimony",
                "name": "韩子敬的脚步声证词",
                "location": "韩子敬（主动提供）",
                "description": (
                    "韩子敬昨夜听到：丑时有人下楼，铁掌靴底，脚步沉重。"
                    "约半炷香后，同一人上楼，步伐明显不均匀，像一条腿有伤。"
                    "丑时正是赵虎独自守夜的时段——这条证词直指他。"
                ),
                "hidden": False,
                "visible_condition": "trust"
            }
        },
        {
            "clue_id": "clue_qingxuzi_testimony",  # 清虚子：拂尘是做法时遗落的
            "npc_id": "npc_qingxuzi",
            "trust_threshold": 65,
            "trigger_time": ["辰时", "巳时"],
            "feed_text": "清虚子神色不安，像是想到了什么",
            "trigger_text": (
                "清虚子拉住你的袖子，压低声音：\n\n"
                "「贫道想到了一件非常要紧的事……」\n\n"
                "「那把拂尘，确实是贫道的。\n"
                "你们来驿站之前，张三施主找贫道在后院佛龛前做了场法事，"
                "说是最近心神不宁，要请神消灾。\n"
                "贫道做完法事就回屋了，"
                "但拂尘……贫道忘在佛龛旁边了。」\n\n"
                "「后来贫道一直没想起来去拿。\n"
                "直到起夜看见尸体时，才发现拂尘就在旁边——\n"
                "所以贫道才顺手捡了起来，结果就被你们当成嫌疑人了！」\n\n"
                "清虚子的眼神第一次真正流露出恐惧，"
                "而不是装出来的惶恐。"
            ),
            "clue_data": {
                "id": "clue_qingxuzi_testimony",
                "name": "清虚子的证词：拂尘是做法时遗落的",
                "location": "清虚子（主动提供）",
                "description": (
                    "清虚子证实：戌时前张三请他在后院佛龛做法事，"
                    "做完后清虚子忘记带走拂尘，遗落在佛龛旁。"
                    "拂尘出现在案发现场是巧合——"
                    "但张三为什么偏偏选在佛龛前做法？他是否预见了什么？"
                ),
                "hidden": False,
                "visible_condition": "trust"
            }
        },
    ]

    collected = set(d_state.get("inventory", {}).get("clues_collected", []))
    trust = d_state.get("npc_trust", {})
    already_triggered = set(d_state.get("trust_clues_triggered", []))
    results = []

    for tc in TRUST_CLUES:
        clue_id = tc["clue_id"]
        npc_id = tc["npc_id"]

        if clue_id in collected:
            continue
        if clue_id in already_triggered:
            continue
        if current_time not in tc["trigger_time"]:
            continue
        if trust.get(npc_id, 0) < tc["trust_threshold"]:
            continue

        results.append(tc)

    return results


def register_trust_clue_triggered(d_state: Dict, clue_id: str):
    """标记信任线索已推送，避免重复触发。"""
    triggered = d_state.setdefault("trust_clues_triggered", [])
    if clue_id not in triggered:
        triggered.append(clue_id)


def collect_trust_clue(
    clue_id: str,
    clue_data: Dict,
    d_state: Dict,
    objective_clues_db: Dict
):
    """玩家接受信任线索后，正式加入收集列表。"""
    if clue_id not in objective_clues_db:
        objective_clues_db[clue_id] = clue_data
    if clue_id not in d_state["inventory"]["clues_collected"]:
        d_state["inventory"]["clues_collected"].append(clue_id)
    register_trust_clue_triggered(d_state, clue_id)


def get_clue_summary_for_prompt(d_state: Dict, objective_clues_db: Dict) -> str:
    """
    为 NPC prompt 生成线索摘要，包含条件线索。
    直接替换 npc_prompt_builder 中的 build_player_clue_summary。
    """
    collected = d_state.get("inventory", {}).get("clues_collected", [])
    if not collected:
        return "玩家目前没有发现任何线索。"

    lines = []
    for cid in collected:
        clue = objective_clues_db.get(cid)
        if clue:
            loc = clue.get("location", "")
            name = clue.get("name", cid)
            lines.append(f"- {name}（{loc}）" if loc else f"- {name}")
        else:
            # 可能是运行时注册的条件线索，从 CONDITIONAL_CLUE_DB 里找
            for cfg in CONDITIONAL_CLUE_DB.values():
                if cfg["clue_data"]["id"] == cid:
                    cd = cfg["clue_data"]
                    loc = cd.get("location", "")
                    name = cd.get("name", cid)
                    lines.append(f"- {name}（{loc}）" if loc else f"- {name}")
                    break
    return "\n".join(lines)
