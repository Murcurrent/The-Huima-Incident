import os
import json
import uuid
import random
from typing import Dict, Any, Optional, Set, List
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from cryptography.fernet import Fernet
import httpx
import uvicorn
import zlib
from npc_prompt_builder import build_npc_system_prompt
import game_handlers
from npc_exploration import run_npc_exploration
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ==========================================
# 🔐 核心鉴权逻辑：名单 + 绑定
# ==========================================

def load_allowed_tokens() -> Set[str]:
    try:
        with open("tokens.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("valid_tokens", []))
    except (IOError, json.JSONDecodeError): return set()

BINDING_FILE = "token_bindings.json"

def load_bindings() -> Dict[str, str]:
    if not os.path.exists(BINDING_FILE): return {}
    try:
        with open(BINDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError): return {}

def save_binding(token: str, device_id: str):
    bindings = load_bindings()
    bindings[token] = device_id
    with open(BINDING_FILE, "w", encoding="utf-8") as f:
        json.dump(bindings, f)

SECRET_KEY = os.getenv("GAME_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("❌ 请在 .env 中设置 GAME_SECRET_KEY，否则重启后所有存档失效！")
cipher = Fernet(SECRET_KEY.encode())

# ==========================================
# 🤖 模型注册表
# ==========================================
MODEL_REGISTRY = {
    "deepseek": {
        "display_name": "DeepSeek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "api_key_env": "DEEPSEEK_API_KEY",       # 从环境变量读取
        "model_name": "deepseek-chat",
        "supports_json_mode": True,               # 是否支持 response_format
    }
    #"grok": {
    #    "display_name": "Grok",
    #    "api_url": "https://api.x.ai/v1/chat/completions",
    #    "api_key_env": "GROK_API_KEY",
    #    "model_name": "grok-3-mini",
    #    "supports_json_mode": True,
    
    # 未来扩展只需在这里加一条
    # "openai": {
    #     "display_name": "GPT-4o",
    #     "api_url": "https://api.openai.com/v1/chat/completions",
    #     "api_key_env": "OPENAI_API_KEY",
    #     "model_name": "gpt-4o",
    #     "supports_json_mode": True,
    # },
}

DEFAULT_MODEL = "deepseek"

def get_available_models():
    """返回已配置了API Key的可用模型列表"""
    available = []
    for model_id, config in MODEL_REGISTRY.items():
        key = os.getenv(config["api_key_env"], "")
        if key and len(key) > 5:
            available.append({
                "id": model_id, 
                "name": config["display_name"]
            })
    return available


# ==========================================
# ⏳ 时间与地点配置
# ==========================================
TIME_CYCLES = ["子时", "丑时", "寅时", "卯时", "辰时", "巳时", "午时", "未时", "申时", "酉时", "戌时", "亥时"]
MAX_AP_PER_CYCLE = 4  
ALL_LOCATIONS = ["大堂", "后院", "灶房", "二楼走廊", "李德福房间", "赵虎房间", "顾琼房间", "韩子敬房间", "清虚子房间", "大堂侧屋"]

# ==========================================
# ⚖️ 核心谜底配置（GAME_TRUTH 已移除，真相由NPC JSON各自管理）
# ==========================================
SOLUTION = {
    "killer_id": "npc_zhaohu",
    "weapon_id": "clue_012",
    "mastermind_id": "npc_lidefu"
}

# ==========================================
# 🔍 核心线索库
# ==========================================
objective_clues_db = {
 
    # ── 后院·尸体 ─────────────────────────────────────────────
    "clue_001": {
        "id": "clue_001", "name": "死者尸体", "location": "后院",
        "search_difficulty": 1,
        "description": (
            "张三死在后院废弃的【佛龛】前。颈部有极细的勒痕，深入皮肉。"
            "死者双目圆睁，面容惊恐，双手呈'鹰爪'状僵硬，"
            "似乎临死前曾猛力抓向某处。他的膝盖沾满新鲜泥土，"
            "生前似乎正在跪拜礼佛。"
        ),
        "visible_condition": "none", "hidden": False
    },
    "clue_002": {
        "id": "clue_002", "name": "颈部的勒痕", "location": "后院 (尸体)",
        "search_difficulty": 1,
        "description": (
            "死者脖颈处有两道清晰的紫黑色勒痕，在咽喉处呈【X】形交叉，"
            "深陷皮肉，力道极大。这种交叉手法能同时压迫颈动脉与喉骨，"
            "死亡极为迅速。凶器极细，却韧性惊人。"
        ),
        "visible_condition": "inspect_corpse"
    },
    "clue_003": {
        "id": "clue_003", "name": "死者手部", "location": "后院 (尸体)",
        "search_difficulty": 1,
        "description": (
            "死者右手指甲缝有暗红血痕——他死前抓伤过凶手。"
            "左手紧握成死拳，指骨处乌青淤紫，似乎死死攥着什么东西。"
            "那只拳头……能掰开吗？"
        ),
        "visible_condition": "inspect_corpse"
    },
    "clue_003_new": {
        "id": "clue_003_new", "name": "掌心压痕", "location": "后院 (尸体)",
        "search_difficulty": 1,
        "description": (
            "【条件线索】费力掰开死者左拳后， 手掌中央有一个做工精美的鎏金指套，内侧刻着一个运字。似乎在什么地方看到过相似的东西？"
            
        ),
        "visible_condition": "conditional"
    },
    "clue_004": {
        "id": "clue_004", "name": "佛龛刮痕", "location": "后院",
        "search_difficulty": 1,
        "description": (
            "木制佛龛底座边缘有数道新鲜刮痕，漆面剥落，木茬雪白。"
            "像是被金属器物反复撬动过。佛龛底座与地面之间，"
            "有一条隐约的缝隙……"
        ),
        "visible_condition": "inspect_shrine"
    },
    "clue_005": {
        "id": "clue_005", "name": "混乱的足迹", "location": "后院",
        "search_difficulty": 1,
        "description": (
            "湿软泥地上有三串足迹。\n"
            "第一串：宽大深重，花纹粗糙，从后门直通佛龛。\n"
            "第二串：细窄男靴，前深后浅、步幅小，在尸体旁短暂停留后慌乱折返大堂。\n"
            "第三串：宽大深重，花纹粗糙，从佛龛处延伸后院中央，随后消失——"
            "像是有人从墙上攀爬离开。"
        ),
        "visible_condition": "inspect_ground"
    },
 
    # ── 各房间 ────────────────────────────────────────────────
    "clue_006": {
        "id": "clue_006", "name": "金疮药味", "location": "赵虎房",
        "search_difficulty": 2,
        "description": (
            "房间里若有若无地飘着一股金创药的气味。"
            "赵虎……近期受了伤？伤在哪里？"
        ),
        "visible_condition": "search_room"
    },
    "clue_007": {
        "id": "clue_007", "name": "加密的绢帛底稿", "location": "李德福房",
        "search_difficulty": 2,
        "description": (
            "藏在行李最深处的一卷绢帛，写满难以辨认的加密字符，"
            "落款处有模糊的官方印鉴。旁边以蝇头小楷批注着几个字："
            "【旧内侍】【查清】【清除】。"
            "这是一道密旨——有人要杀人灭口。"
        ),
        "visible_condition": "search_room_hard"
    },
    "clue_008": {
        "id": "clue_008", "name": "烧焦的手札残页", "location": "顾琼房",
        "search_difficulty": 1,
        "description": (
            "火炉冷灰中有一片未烧尽的纸角，秀丽字迹，"
            "隐约可见「复仇」二字，以及半个残缺的人名。"
            "顾琼在此之前，经历过什么？"
        ),
        "visible_condition": "search_fireplace"
    },
    "clue_009": {
        "id": "clue_009", "name": "被修改的星盘图", "location": "清虚子房",
        "search_difficulty": 1,
        "description": (
            "桌上铺着一张复杂的星盘图，某些星位被浓墨重重涂改，墨迹尚新。"
            "涂改的位置……对应的是今夜的天象。"
            "清虚子在掩盖什么预言？"
        ),
        "visible_condition": "search_table"
    },
    "clue_010": {
        "id": "clue_010", "name": "大堂桌椅", "location": "大堂",
        "search_difficulty": 1,
        "description": (
            "几张桌子散乱摆放。靠窗那张是顾琼坐过的，桌上有一只茶盏。"
            "柜台后方有一个上锁的小木柜，锁头看起来很新。"
        ),
        "visible_condition": "search_lobby"
    },
    "clue_010_new": {
        "id": "clue_010_new", "name": "柜台锦袋", "location": "大堂",
        "search_difficulty": 2,
        "description": (
            "柜台后方的小木柜里，压着一只空的【锦袋】。"
            "袋口的流苏是宫廷样式，袋身绣着云纹，内里还残留着淡淡的龙涎香气。"
            "这种香料只有内廷才用得起。"
            "锦袋是空的——原本装着的东西已经不见了。"
        ),
        "visible_condition": "conditional"
    },
    "clue_011": {
        "id": "clue_011", "name": "大堂茶盏", "location": "大堂",
        "search_difficulty": 1,
        "description": (
            "顾琼桌上的茶碗稳稳立在正放的茶托上，看起来并无异样。"
            "茶水已凉，碗沿有浅浅的口脂印记。"
        ),
        "visible_condition": "search_lobby_teacup"
    },
    "clue_012": {
        "id": "clue_012", "name": "锦套内的乌金丝拂尘", "location": "李德福房",
        "search_difficulty": 2,
        "description": (
            "【关键证物】枕头里藏着的锦套内，是一柄【金镶玉柄拂尘】。"
            "拂尘比寻常的沉重许多——尘尾中藏着一根极细且坚硬，泛着金属光泽的丝线！"
            "柄上的收线机关已经损坏，金属丝线无法缩回，微微外露。"
            "柄身有裂纹，系被人大力使用后损坏。"
            "柄底刻着小篆「运」字。这不是装饰品，是一件杀人的兵器。"
        ),
        "visible_condition": "search_room_hard"
    },
    "clue_013": {
        "id": "clue_013", "name": "小二通铺", "location": "大堂侧屋",
        "search_difficulty": 1,
        "description": (
            "张三的床铺凌乱，东西散落一地，明显被人翻找过。"
            "床底灰尘中有一处长条形空白痕迹，长约三尺——"
            "像是原本藏着什么细长的东西"
        ),
        "visible_condition": "search_room_zhang"
    },
    "clue_014": {
        "id": "clue_014", "name": "未完全烧毁的男靴", "location": "灶房",
        "search_difficulty": 1,
        "description": (
            "灶房炉膛里有东西没烧尽，还在冒黑烟。"
            "掏出来是一双鞋型细长的靴子，靴底花纹是男式，"
            "但内里竟是绸缎衬里，尺码偏小——穿这双靴子的人，"
            "是个习惯乔装的女人。"
        ),
        "visible_condition": "search_room_kitchen"
    },
    "clue_015": {
        "id": "clue_015", "name": "李德福房的覆托立盏", "location": "李德福房",
        "search_difficulty": 1,
        "description": (
            "李德福自带的茶碗，底下的漆器茶托被底朝天扣在桌面上，"
            "茶碗却四平八稳立在翻转的茶托底面上。"
        ),
        "visible_condition": "search_room_hard"
    },
    "clue_016": {
        "id": "clue_016", "name": "顾琼衣柜", "location": "顾琼房",
        "search_difficulty": 1,
        "description": (
            "衣柜里挂着几件便于行动的男式长衫，显然她路上惯于乔装。"
            "奇怪的是，其中一套明显缺了配套的靴子——"
            "那双靴子去哪了？"
        ),
        "visible_condition": "search_room_gu"
    },
    "clue_017": {
        "id": "clue_017", "name": "泥泞的折扇", "location": "后院",
        "search_difficulty": 1,
        "description": (
            "后门草丛里有一把折扇，扇面湿透沾满泥，"
            "但扇骨是湘妃竹，颇为雅致。"
            "扇面上题着半首诗：「朱门酒肉臭，路有……」"
            "笔迹清秀，墨色被雨水晕开。这是谁的？"
        ),
        "visible_condition": "inspect_ground"
    },
    "clue_018": {
        "id": "clue_018", "name": "烧残的诗稿", "location": "韩子敬房",
        "search_difficulty": 1,
        "description": (
            "韩子敬房间的炭盆里，有一本没烧完的诗稿。"
            "字里行间写满对圣人、对朝廷的愤懑不满——"
            "这是要杀头的【反诗】。"
            "难怪他见到李德福（宫里人）吓得脸色惨白。"
        ),
        "visible_condition": "search_room_han"
    },
    "clue_019": {
        "id": "clue_019", "name": "木柄拂尘", "location": "后院",
        "search_difficulty": 1,
        "description": (
            "尸体旁泥泞中掉落着一把【桃木柄拂尘】，沾满泥水。"
            "这是道士清虚子的随身之物。"
            "拂尘的马尾毛凌乱毛糙，似被人紧紧攥握过。"
            "你扯了扯尘毛，几根轻飘飘地脱落——毛根处有些异常。"
        ),
        "visible_condition": "inspect_ground"
    },
    "clue_020": {
        "id": "clue_020", "name": "老旧精美的荷包", "location": "清虚子房",
        "search_difficulty": 2,
        "description": (
            "清虚子布袋里搜出一个刺绣荷包，款式极老，针法出自宫中，"
            "绝非寻常道士所能拥有。"
            "荷包内里绣着「运」字，里面只有几枚铜板和碎银。"
            "这是谁的钱袋？「运」字……在哪里还见过？"
        ),
        "visible_condition": "search_room_qing"
    },
 
    # ── 新增线索 ──────────────────────────────────────────────
    "clue_021": {
        "id": "clue_021", "name": "拂尘柄内的乌金丝残段", "location": "后院",
        "search_difficulty": 2,
        "description": (
            "【条件线索·需光亮】借助充足的光线，仔细检查桃木拂尘的柄部——"
            "木柄根部的马尾毛束中，混入了一根极细的金属丝，"
            "泛着幽幽的乌光。这不是马毛。"
            "它从哪里断下来的？"
        ),
        "visible_condition": "conditional"
    },
    "clue_022": {
        "id": "clue_022", "name": "佛龛底座暗槽", "location": "后院",
        "search_difficulty": 2,
        "description": (
            "循着尸体的鎏金指套线索返回检查佛龛——"
            "底座侧面有一道极细的缝隙，用力按压后弹开，"
            "露出一个浅浅的暗槽。槽内壁有一处凹陷，很小，像是能放下什么首饰"
            "像是环形物品收纳于此。"
            "张三把什么藏在这里？"
        ),
        "visible_condition": "conditional"
    },
    "clue_023": {
        "id": "clue_023", "name": "尸体的体温", "location": "后院 (尸体)",
        "search_difficulty": 1,
        "description": (
            "你将手覆上死者胸口——"
            "尸身尚有残温，远未到完全僵硬的程度。"
            "死亡时间约两个时辰前。"
            "这说明：凶手就在驿站之中，现在还没走。"
        ),
        "visible_condition": "conditional"
    },
    "clue_024": {
        "id": "clue_024", "name": "断裂的绑带", "location": "赵虎房",
        "search_difficulty": 2,
        "description": (
            "【条件线索】搜查赵虎床铺底板缝隙，发现一截被撕断的布绑带，"
            "布面有陈旧血迹，已经干透变黑。"
            "这是包扎伤口用的——伤在什么部位，需要藏得这么深？"
        ),
        "visible_condition": "conditional"
    },
    "clue_025": {
        "id": "clue_025", "name": "「李福运」字条", "location": "大堂侧屋",
        "search_difficulty": 2,
        "description": (
            "张三床铺木板夹缝中藏着一张折叠字条，"
            "纸张已经被揉皱再展开过无数次。"
            "字条上只有三个字：【李福运】。"
            "下方有一行更小的字，几乎难以辨认：「若我死，此名可保命。」"
            "张三……知道自己有危险。"
        ),
        "visible_condition": "conditional"
    },
    "clue_new_wall": {
        "id": "clue_new_wall", "name": "二楼外墙划痕", "location": "二楼走廊",
        "search_difficulty": 2,
        "description": (
            "检查二楼走廊外侧窗台——"
            "窗框下沿和外墙砖面有数道新鲜划痕，还粘着泥土和细碎的青苔。"
            "有人从这里翻出去，或者攀爬上来。"
        ),
        "visible_condition": "conditional"
    },
    "clue_li_finger": {
        "id": "clue_li_finger", "name": "錾花金指套", "location": "大堂",
        "search_difficulty": 2,
        "description": (
            "与李德福交谈时注意他的右手——"
            "他惯于把玩一枚【錾花金指套】，套在拇指上。这枚指套似曾相识？"
        ),
        "visible_condition": "conditional"
    },
 
    # ── 信任/高难度线索 ───────────────────────────────────────
    "clue_026": {
        "id": "clue_026", "name": "顾琼的家书", "location": "顾琼房",
        "search_difficulty": 1,
        "description": (
            "顾琼主动递给你一封家书。"
            "信中提及她的家族三年前死于一桩冤案，"
            "主谋正是当时的掌印太监。"
            "「我此行不是探亲，」她的字迹颤抖，「我要亲眼看着他死。」"
        ),
        "visible_condition": "trust_triggered"
    },
    "clue_027": {
        "id": "clue_027", "name": "韩子敬的落榜文书", "location": "韩子敬房",
        "search_difficulty": 2,
        "description": (
            "书页夹层中发现一张官府文书——"
            "韩子敬此前已参加过两届春闱，皆以「文风不正」为由落榜。"
            "主考官的批语：「狂悖之词，不堪大用。」"
            "他的诗稿是反诗，也是他对整个科举制度的绝望控诉。"
        ),
        "visible_condition": "conditional"
    },
    "clue_028": {
        "id": "clue_028", "name": "清虚子的度牒", "location": "清虚子房",
        "search_difficulty": 2,
        "description": (
            "床铺底下压着一份道士度牒，"
            "官方印鉴是真的，但姓名栏被人工涂改过。"
            "他的真实身份不是道士——或者说，他不一直是道士。"
        ),
        "visible_condition": "conditional"
    },
    "clue_029": {
        "id": "clue_029", "name": "清虚子的证词：拂尘是做法时遗落的", "location": "后院",
        "search_difficulty": 1,
        "description": (
            "清虚子压低声音告诉你——"
            "戌时前，张三请他在后院佛龛前做法消灾。"
            "做完法事后清虚子回屋，忘记带走拂尘，遗落在佛龛旁。"
            "「贫道的拂尘……是自己忘拿的，不是故意放在那里的！」"
        ),
        "visible_condition": "trust_triggered"
    },
    "clue_030": {
        "id": "clue_030", "name": "李德福行李中的画像", "location": "李德福房",
        "search_difficulty": 3,
        "description": (
            "行李夹层最深处藏着一张折叠画像——"
            "画中人身着内侍官服，面容与张三有五分相似，"
            "但眼神截然不同：画中人目光锐利，气度威严。"
            "画像背面写着：「掌印太监李福运，先帝十二年。」"
            "死者……是他。"
        ),
        "visible_condition": "conditional"
    },
    "clue_037_testimony": {
        "id": "clue_037_testimony", "name": "韩子敬的脚步声证词", "location": "韩子敬房",
        "search_difficulty": 1,
        "description": (
            "韩子敬颤抖着开口——"
            "寅时前他出门去后院埋诗稿时，看到了尸体，"
            "吓得拔腿就跑，折扇掉落都顾不上捡。"
            "但他发誓，他出门之前（大约丑时），就已经听到过一阵沉重的脚步声"
            "从走廊经过——那脚步声，不像是去如厕，更像是有目的地行动。"
        ),
        "visible_condition": "trust_triggered"
    },
}

# ==========================================
# 🏠 场景配置
# ==========================================
ROOM_DB = {
    "后院": {
        "name": "后院",
        "atmosphere": "破败的佛龛，泥泞的地面，暴雨声盖过了一切。",
        "furniture_list": [
            "死者全身", "死者颈部", "死者手部", "死者左拳",
            "佛龛", "佛龛底部", "泥地", "草丛", "尸体旁的泥泞"
        ],
        "furniture_map": {
            "死者全身":     "clue_001",
            "死者颈部":     "clue_002",
            "死者手部":     "clue_003",
            "死者左拳":     "clue_003_new",   # 条件：持有clue_003
            "佛龛":         "clue_004",
            "佛龛底部":     "clue_022",        # 条件：持有clue_021
            "泥地":         "clue_005",
            "草丛":         "clue_017",
            "尸体旁的泥泞": "clue_019",
        },
        "conditional_furniture": {"死者左拳", "佛龛底部"},  # 前端渲染为◈按钮
        "owner": None
    },
 
    "灶房": {
        "name": "灶房",
        "atmosphere": "灰烬的焦味，柴火堆潮湿，有什么东西没烧干净。",
        "furniture_list": ["炉膛", "水缸", "柴火堆"],
        "furniture_map": {
            "炉膛":   "clue_014",
            "水缸":   None,
            "柴火堆": None,
        },
        "owner": None
    },
 
    "大堂": {
        "name": "大堂",
        "atmosphere": "油灯昏黄，雨水从破窗缝渗入，空气潮腻。",
        "furniture_list": ["大堂桌椅", "顾琼的桌子", "柜台", "柜台后木柜", "角落"],
        "furniture_map": {
            "大堂桌椅":   "clue_010",
            "顾琼的桌子": "clue_011",
            "柜台":       None,
            "柜台后木柜": "clue_010_new",   # 条件：持有clue_010
            "角落":       None,
        },
        "conditional_furniture": {"柜台后木柜"},
        "inspect_texts": {
            "柜台": "柜台后方有一个上锁的小木柜，锁头看起来很新。也许值得仔细搜一搜。"
        },
        "owner": None
    },
 
    "大堂侧屋": {
        "name": "小二通铺",
        "atmosphere": "杂乱的铺盖，东西散落一地，有人翻找过。",
        "furniture_list": ["床铺", "床底", "枕头", "破衣柜", "床板夹缝"],
        "furniture_map": {
            "床铺":     None,
            "床底":     "clue_013",
            "枕头":     None,
            "破衣柜":   None,
            "床板夹缝": "clue_025",   # 条件：持有clue_007+020
        },
        "conditional_furniture": {"床板夹缝"},
        "inspect_texts": {
            "床铺":   "乱作一团，似乎被人翻过。",
            "枕头":   "掉在地上，芯子被翻了出来。",
            "破衣柜": "衣柜门敞开，里面乱七八糟，几件衣服掉在地上。",
            "床底":   "床底积满灰尘，但有一处长条形空白——像是藏过什么细长的东西。",
        },
        "owner": None
    },
 
    "李德福房间": {
        "name": "李德福房间",
        "atmosphere": "龙涎香气残存，被褥质地极好，处处透着宫廷习气。",
        "furniture_list": ["行李", "行李夹层", "桌子", "床铺", "枕头"],
        "furniture_map": {
            "行李":     "clue_007",
            "行李夹层": "clue_030",   # 条件：持有clue_007+025，难度5
            "桌子":     "clue_015",
            "床铺":     None,
            "枕头":     "clue_012",
        },
        "conditional_furniture": {"行李夹层"},
        "hidden_until": {"枕头": "床铺"},  # 枕头在检查床铺后才出现
        "inspect_texts": {
            "床铺": (
                "被褥虽乱，但质地极好。你在被褥间摸索了一番，"
                "除了残温外一无所获。不过这【枕头】看起来过于鼓囊，"
                "里面像是塞了什么硬物。"
            ),
            "行李": "沉甸甸的行李，最外层是些寻常衣物。夹层深处似乎还有东西……",
        },
        "owner": "npc_lidefu"
    },
 
    "赵虎房间": {
        "name": "赵虎房间",
        "atmosphere": "床铺硬实，药味若有若无，窗户关得严实。",
        "furniture_list": ["桌上", "床边", "床底", "床板底缝"],
        "furniture_map": {
            "桌上":    "clue_006",
            "床边":    None,
            "床底":    None,
            "床板底缝": "clue_024",   # 条件：持有clue_006，难度3
        },
        "conditional_furniture": {"床板底缝"},
        "inspect_texts": {
            "床底":    "床底积满灰尘，没有明显异物。但床板和地面之间……有条缝。",
            "床边":    "床边放着一双靴子，靴底有新鲜的泥点。",
        },
        "owner": "npc_zhaohu"
    },
 
    "顾琼房间": {
        "name": "顾琼房间",
        "atmosphere": "梳妆台上有佛珠，衣柜微开，淡淡的女子脂粉香。",
        "furniture_list": ["衣柜", "火炉", "梳妆台"],
        "furniture_map": {
            "衣柜":   "clue_016",
            "火炉":   "clue_008",
            "梳妆台": None,
        },
        "inspect_texts": {
            "梳妆台": "梳妆台上摆着一串佛珠和一面铜镜，没有其他异常。",
        },
        "owner": "npc_guqiong"
    },
 
    "韩子敬房间": {
        "name": "韩子敬房间",
        "atmosphere": "墨香混着炭灰味，书卷叠了半桌，炭盆还有余温。",
        "furniture_list": ["书桌", "炭盆", "书页夹层"],
        "furniture_map": {
            "炭盆":     "clue_018",
            "书桌":     None,
            "书页夹层": "clue_027",   # 条件：持有clue_018，难度2
        },
        "conditional_furniture": {"书页夹层"},
        "inspect_texts": {
            "书桌": "桌上摆着几本经义，翻开的那页用手指抠出了折痕。有一本书页间似乎夹着什么。",
        },
        "owner": "npc_hanzijing"
    },
 
    "清虚子房间": {
        "name": "清虚子房间",
        "atmosphere": "符纸贴了满壁，药草香混着尘土，透着几分江湖气。",
        "furniture_list": ["桌子", "布袋", "床铺", "床底"],
        "furniture_map": {
            "桌子": "clue_009",
            "布袋": "clue_020",
            "床铺": None,
            "床底": "clue_028",   # 条件：持有clue_009，难度2
        },
        "conditional_furniture": {"床底"},
        "inspect_texts": {
            "床铺": "铺着旧棉被，有些潮。床底压着什么东西——边角露出一点。",
        },
        "owner": "npc_qingxuzi"
    },
 
    "二楼走廊": {
        "name": "二楼走廊",
        "atmosphere": "走廊昏暗，窗外雨声如注，脚步声在此处格外清晰。",
        "furniture_list": ["走廊窗台", "地面"],
        "furniture_map": {
            "走廊窗台": "clue_new_wall",   # 条件：持有clue_005+006
            "地面":     None,
        },
        "conditional_furniture": {"走廊窗台"},
        "inspect_texts": {
            "地面":     "走廊地板有几处新鲜的泥脚印，来自楼下。",
            "走廊窗台": "窗框紧闭，但窗台下沿……似乎有划痕。",
        },
        "owner": None
    },
}

NPC_LIST = [
    {"id": "npc_lidefu", "name": "李德福"},
    {"id": "npc_zhaohu", "name": "赵虎"},
    {"id": "npc_guqiong", "name": "顾琼"},
    {"id": "npc_hanzijing", "name": "韩子敬"},
    {"id": "npc_qingxuzi", "name": "清虚子"}
]

# ==========================================
# 📡 数据模型
# ==========================================
class UIAction(BaseModel):
    label: str          
    action_type: str    
    payload: str
    image: Optional[str] = None

class GameRequest(BaseModel):
    user_input: str
    encrypted_state: Optional[str] = None
    npc_id: Optional[str] = None
    confront_clue_id: Optional[str] = None
    model_id: Optional[str] = None   # ← 玩家选择的模型

class GameResponse(BaseModel):
    reply_text: str
    sender_name: str
    new_encrypted_state: str
    ui_type: str = "text"   
    ui_options: List[UIAction] = []
    bg_image: Optional[str] = None
    status_info: Optional[Dict[str, Any]] = None

class VerifyRequest(BaseModel):
    token: str
    device_id: Optional[str] = None

# ==========================================
# 🔧 辅助函数
# ==========================================
def load_json(filename: str):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def decrypt_state(token: str) -> Dict:
    if not token:
        initial_npc_locations = {npc['id']: random.choice(ALL_LOCATIONS) for npc in NPC_LIST}
        initial_npc_locations["npc_lidefu"] = "李德福房间"
        return {
            "player_name": "李密卫",
            "dynamic_state": {
                "day": 1,
                "current_location": "大堂",
                "time_idx": 4,
                "ap_used_this_cycle": 0, 
                "inventory": {"clues_collected": []},
                "npc_locations": initial_npc_locations,
                "game_over": False,
                "temp_accuse_target": None,
                "conversation_history": {},
                "confrontation_used": {},
                "npc_activities": {
                    npc['id']: {"discovered": [], "theory": "", "last_action": ""}
                    for npc in NPC_LIST
                },
                "tribunal_count": 0,
                "inferences_unlocked": [],
                "trust_clues_triggered": [],
                "npc_statements": {},
                "npc_trust": {
                    "npc_lidefu": 30,     # 李德福天生对玩家警惕
                    "npc_zhaohu": 20,     # 赵虎把玩家当威胁
                    "npc_guqiong": 10,    # 顾琼敌视官差
                    "npc_hanzijing": 40,  # 韩子敬胆小但无恶意
                    "npc_qingxuzi": 45    # 清虚子想利用玩家洗清嫌疑
                }
            }
        }
    try:
        decrypted = cipher.decrypt(token.encode())
        try:
            raw = zlib.decompress(decrypted)
        except zlib.error:
            raw = decrypted  # 兼容旧的未压缩 state
        state = json.loads(raw.decode())
        if "day" not in state["dynamic_state"]: state["dynamic_state"]["day"] = 1
        if "game_over" not in state["dynamic_state"]: state["dynamic_state"]["game_over"] = False
        if "conversation_history" not in state["dynamic_state"]: state["dynamic_state"]["conversation_history"] = {}
        if "confrontation_used" not in state["dynamic_state"]: state["dynamic_state"]["confrontation_used"] = {}
        if "npc_activities" not in state["dynamic_state"]:
            state["dynamic_state"]["npc_activities"] = {
                npc['id']: {"discovered": [], "theory": "", "last_action": ""}
                for npc in NPC_LIST
            }
        if "npc_trust" not in state["dynamic_state"]:
            state["dynamic_state"]["npc_trust"] = {
                "npc_lidefu": 30, "npc_zhaohu": 20, "npc_guqiong": 10,
                "npc_hanzijing": 40, "npc_qingxuzi": 45
            }
        if "tribunal_count" not in state["dynamic_state"]:
            state["dynamic_state"]["tribunal_count"] = 0
        if "inferences_unlocked" not in state["dynamic_state"]:
            state["dynamic_state"]["inferences_unlocked"] = []
        if "trust_clues_triggered" not in state["dynamic_state"]:
            state["dynamic_state"]["trust_clues_triggered"] = []
        if "npc_statements" not in state["dynamic_state"]:
            state["dynamic_state"]["npc_statements"] = {}
        return state
    except Exception:
        return decrypt_state(None)

def encrypt_state(state: Dict) -> str:
    raw = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()
    compressed = zlib.compress(raw)
    return cipher.encrypt(compressed).decode()

def advance_time(global_state: Dict):
    if "dynamic_state" in global_state:
        global_state["dynamic_state"]["ap_used_this_cycle"] += 1
        if global_state["dynamic_state"]["ap_used_this_cycle"] >= MAX_AP_PER_CYCLE:
            global_state["dynamic_state"]["ap_used_this_cycle"] = 0
            current_idx = global_state["dynamic_state"]["time_idx"]
            if current_idx == 11:
                global_state["dynamic_state"]["day"] += 1
                global_state["dynamic_state"]["time_idx"] = 0
            else:
                global_state["dynamic_state"]["time_idx"] += 1

        run_npc_exploration(
            global_state=global_state,
            npc_list=NPC_LIST,
            time_cycles=TIME_CYCLES,
            all_locations=ALL_LOCATIONS,
            load_npc_profile_func=load_npc_profile
        )

        # ── 信任线索推送：时间推进后检查是否有 NPC 达到信任阈值 ──
        from conditional_clues import get_trust_triggered_clues, register_trust_clue_triggered
        d_state = global_state["dynamic_state"]
        current_time = TIME_CYCLES[d_state["time_idx"]]
        pending_trust_clues = get_trust_triggered_clues(d_state, current_time)
        if pending_trust_clues:
            # 存入 pending_trust_clues，前端下次请求时会带回给玩家
            existing = d_state.setdefault("pending_trust_clues", [])
            for tc in pending_trust_clues:
                if tc["clue_id"] not in [x["clue_id"] for x in existing]:
                    existing.append({
                        "clue_id": tc["clue_id"],
                        "npc_id": tc["npc_id"],
                        "feed_text": tc["feed_text"],
                        "trigger_text": tc["trigger_text"],
                        "clue_data": tc["clue_data"],
                    })
                    register_trust_clue_triggered(d_state, tc["clue_id"])

def check_caught_searching(current_state):
    """检查玩家是否在 NPC 房间被撞见"""
    d_state = current_state["dynamic_state"]
    player_loc = d_state.get("current_location")

    room_owner_map = {
        "李德福房间": "npc_lidefu",
        "赵虎房间": "npc_zhaohu",
        "顾琼房间": "npc_guqiong",
        "韩子敬房间": "npc_hanzijing",
        "清虚子房间": "npc_qingxuzi"
    }

    owner_id = room_owner_map.get(player_loc)
    if not owner_id:
        return None  # 公共区域，不会被撞见

    npc_loc = d_state.get("npc_locations", {}).get(owner_id)
    if npc_loc == player_loc:
        owner_name = next(
            (n["name"] for n in NPC_LIST if n["id"] == owner_id), "主人"
        )
        # 信任度暴跌
        game_handlers.adjust_trust(d_state, owner_id, "caught_searching")

        return {
            "caught": True,
            "owner_id": owner_id,
            "message": (
                f"⚠️ **被抓到了！**\n\n"
                f"你正在翻找时，{owner_name}突然推门而入！\n"
                f'"{owner_name}"怒目圆睁："你在我房里做什么？！"\n\n'
                f"你被赶出了房间。（{owner_name}对你的信任度大幅下降）"
            )
        }
    return None

def get_status_report(state: Dict) -> str:
    d_state = state['dynamic_state']
    time_idx = d_state.get('time_idx', 4)
    current_time_str = TIME_CYCLES[time_idx]
    used_ap = d_state.get('ap_used_this_cycle', 0)
    remaining_ap = MAX_AP_PER_CYCLE - used_ap
    npc_locs = d_state.get('npc_locations', {})
    loc_rumors = []
    visible_npcs = random.sample(NPC_LIST, 2)
    for npc in visible_npcs:
        loc = npc_locs.get(npc['id'], "未知")
        loc_rumors.append(f"{npc['name']} 似乎在 {loc}")
    return f"""**当前时辰**：{current_time_str}
**剩余精力**：{remaining_ap}/{MAX_AP_PER_CYCLE}
**所在位置**：{d_state.get('current_location', '未知')}

**听到的动静**：
{chr(10).join(['- ' + r for r in loc_rumors])}"""

def check_auto_trigger_endgame(state: Dict) -> bool:
    d_state = state["dynamic_state"]
    if d_state.get("day", 1) >= 3 and d_state.get("time_idx", 0) == 4:
        return True
    return False

# ==========================================
# 🎭 NPC对话辅助函数（新增）
# ==========================================
def load_npc_profile(npc_id: str):
    """根据NPC ID加载对应的Profile JSON文件。"""
    file_base = npc_id.replace('npc_', '').title()
    base_map = {
        "Lidefu": "LiDefu", "Zhaohu": "ZhaoHu", "Guqiong": "GuQiong",
        "Hanzijing": "HanZijing", "Qingxuzi": "QingXuzi"
    }
    file_base = base_map.get(file_base, file_base)
    npc_filename = f"NPC_Profiles/{file_base}_Profile.json"
    if not os.path.exists(npc_filename):
        npc_filename = f"{file_base}_Profile.json"
    if os.path.exists(npc_filename):
        return load_json(npc_filename)
    return None

async def call_llm(system_prompt: str, messages: list, model_id: str = None) -> str:
    """根据 model_id 调用对应的 LLM。"""
    model_id = model_id or DEFAULT_MODEL
    config = MODEL_REGISTRY.get(model_id)
    if not config:
        return f"不支持的模型：{model_id}"

    api_key = os.getenv(config["api_key_env"], "")
    if not api_key:
        return f"模型 {config['display_name']} 未配置 API Key"

    request_body = {
        "model": config["model_name"],
        "messages": messages,
    }
    # 部分模型不支持 json_mode
    if config.get("supports_json_mode"):
        request_body["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                config["api_url"],
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=request_body,
                timeout=30.0
            )
        if resp.status_code == 200:
            content = resp.json()['choices'][0]['message']['content']
            if not content or not content.strip():
                return "（沉默不语）"
            # 尝试解析 JSON，失败则直接返回原文
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    reply = parsed.get("reply", "")
                    return reply if reply else content
                return content
            except json.JSONDecodeError:
                # 清理残留的 JSON 标记
                cleaned = content.strip()
                # 去掉 markdown 代码块包裹
                if cleaned.startswith('```'):
                    cleaned = cleaned.split('\n', 1)[-1] if '\n' in cleaned else cleaned[3:]
                    if cleaned.endswith('```'):
                        cleaned = cleaned[:-3]
                    cleaned = cleaned.strip()
                    # 再尝试解析一次
                    try:
                        parsed = json.loads(cleaned)
                        if isinstance(parsed, dict):
                            reply = parsed.get("reply", "")
                            return reply if reply else cleaned
                    except json.JSONDecodeError:
                        pass
                # 尝试去掉常见的 JSON 前缀残留
                for prefix in ['{"reply":', '{"reply" :', 'reply:']:
                    if cleaned.startswith(prefix):
                        cleaned = cleaned[len(prefix):].strip()
                        # 去首尾引号和大括号
                        if cleaned.startswith('"') and '"' in cleaned[1:]:
                            cleaned = cleaned[1:cleaned.rindex('"')]
                        elif cleaned.endswith('}'):
                            cleaned = cleaned[:-1].strip().strip('"')
                        cleaned = cleaned.replace('\\n', '\n').replace('\\"', '"')
                        return cleaned if cleaned else content
                return content
        else:
            return f"模型调用失败 ({resp.status_code}): {resp.text[:200]}"
    except Exception as e:
        return f"网络错误: {str(e)}"

def get_npc_history(state: Dict, npc_id: str) -> list:
    """获取指定NPC的对话历史。"""
    conv = state["dynamic_state"].setdefault("conversation_history", {})
    return conv.setdefault(npc_id, [])

def save_npc_history(state: Dict, npc_id: str, user_msg: str, assistant_msg: str):
    """保存一轮对话到NPC历史，并限制长度。"""
    conv = state["dynamic_state"].setdefault("conversation_history", {})
    history = conv.setdefault(npc_id, [])
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})
    if len(history) > 20:
        conv[npc_id] = history[-20:]

def build_llm_messages(system_prompt: str, npc_history: list, current_msg: str) -> list:
    """构建发给LLM的完整messages列表。"""
    messages = [{"role": "system", "content": system_prompt}]
    for msg in npc_history[-20:]:
        messages.append(msg)
    messages.append({"role": "user", "content": current_msg})
    return messages

# ==========================================
# 🌐 路由接口
# ==========================================

@app.get("/")
async def read_root():
    return FileResponse('index.html')

@app.post("/verify_token")
async def verify_token(req: VerifyRequest):
    valid_tokens = load_allowed_tokens()
    if req.token not in valid_tokens:
        raise HTTPException(status_code=401, detail="无效的邀请码")
    bindings = load_bindings()
    existing_device = bindings.get(req.token)
    if existing_device:
        if req.device_id == existing_device:
            return {"status": "valid", "device_id": existing_device}
        else:
            raise HTTPException(status_code=403, detail="此邀请码已绑定其他设备，无法使用")
    else:
        new_device_id = str(uuid.uuid4())
        save_binding(req.token, new_device_id)
        return {"status": "bound", "device_id": new_device_id}

# 返回可用模型列表（前端用来渲染选择器）
@app.get("/api/models")
async def list_models():
    return {"models": get_available_models(), "default": DEFAULT_MODEL}

# ==========================================
# 🚀 核心聊天接口
# ==========================================
@app.post("/chat", response_model=GameResponse)
async def chat_endpoint(
    request: GameRequest, 
    x_access_token: str = Header(..., alias="X-Access-Token"), 
    x_device_id: str = Header(..., alias="X-Device-Id")
   
):
    # --- 1. 安全检查 ---
    valid_tokens = load_allowed_tokens()
    bindings = load_bindings()
    if x_access_token not in valid_tokens:
        raise HTTPException(status_code=401, detail="邀请码无效")
    bound_device = bindings.get(x_access_token)
    if not bound_device or bound_device != x_device_id:
        raise HTTPException(status_code=403, detail="设备校验失败，请勿分享邀请码")
    
    model_id = request.model_id or DEFAULT_MODEL

    # --- 2. 游戏逻辑 ---
    current_state = decrypt_state(request.encrypted_state)
    user_input = request.user_input.strip()
    
    reply = ""
    sender = "系统"
    ui_type = "text"
    ui_options = []
    bg_img = None

    # 3. 游戏结束拦截（允许查看报告）
    if current_state["dynamic_state"].get("game_over", False):
        if not user_input.startswith("CMD_SHOW_REPORT"):
            return GameResponse(
                reply_text="【游戏已结束】请刷新页面重新开始。",
                sender_name="系统", new_encrypted_state=encrypt_state(current_state),
                ui_type="text", ui_options=[]
            )

    # 4. 自动触发结局判定
    if check_auto_trigger_endgame(current_state) and not user_input.startswith("CMD_"):
        user_input = "CMD_SHOW_ACCUSE_MENU"
        reply = ('【⏳ 时间已到】\n\n第三日的晨光透过窗棂，李德福彻底失去了耐心。...\n'
                 '"密卫大人，时间到了。咱家要的交代呢？"\n\n(强制进入指认流程)')
        sender = "强制剧情"
    
     # --- 5. 按顺序尝试各 handler ---
    # each handler return {"done": True/False, ...}
    # done=True means match completed, skip following handler

    handlers = [
        game_handlers.handle_tribunal,   # 群讨系统
        game_handlers.handle_accuse,     # 指认系统
        game_handlers.handle_confront,   # 对质系统
        game_handlers.handle_search,     # 搜查系统
        game_handlers.handle_talk,       # 对话系统（含 NPC 自由对话）
        game_handlers.handle_recall_cmd, # 回想系统（只读，不消耗 AP）
    ]

    result = None
    for handler in handlers:
        result = await handler(user_input, request, current_state, model_id)
        if result["done"]:
            # 特殊情况:handler 需要直接返回 GameResponse(如房间被阻挡）
            if result.get("early_return"):
                return result["early_return"]
            reply = result["reply"]
            sender = result["sender"]
            break
    
    # --- 6. no handler matched → bottom logic(stay  in main )---
    if not result or not result["done"]:
        ui_type = "text"
        ui_options = []
        bg_img = None

        if user_input == "系统菜单":
            d_state = current_state['dynamic_state']
            reply = get_status_report(current_state)
            reply = f"📅 **第 {d_state.get('day', 1)} 日**\n" + reply

        elif user_input.startswith("CMD_ACCEPT_TRUST_CLUE"):
            # 玩家点击"接受"信任触发线索
            # payload: clue_id
            clue_id_to_accept = user_input.split(":", 1)[1] if ":" in user_input else ""
            d_state = current_state["dynamic_state"]
            from conditional_clues import collect_trust_clue, get_trust_triggered_clues
            # 找到对应的 trust_clue 配置
            pending = d_state.get("pending_trust_clues", [])
            matched = next((tc for tc in pending if tc["clue_id"] == clue_id_to_accept), None)
            if matched:
                collect_trust_clue(
                    clue_id=matched["clue_id"],
                    clue_data=matched["clue_data"],
                    d_state=d_state,
                    objective_clues_db=objective_clues_db
                )
                # 从 pending 移除
                d_state["pending_trust_clues"] = [
                    tc for tc in pending if tc["clue_id"] != clue_id_to_accept
                ]
                clue_name = matched["clue_data"].get("name", clue_id_to_accept)
                reply = matched["trigger_text"] + f"\n\n**▪ 新线索入档：{clue_name}**"
                sender = next(
                    (n["name"] for n in NPC_LIST if n["id"] == matched["npc_id"]),
                    "神秘人"
                )
            else:
                reply = "（线索已收取或不存在）"

        elif user_input.startswith("CMD_EXIT"):
            mode = user_input.split(":", 1)[1]
            d_state = current_state["dynamic_state"]

            if mode == "SEARCH":
                # 退出搜查：不消耗行动点，但检查是否被撞见
                d_state["room_inspect_count"] = 0
                caught = check_caught_searching(current_state)
                if caught:
                    reply = caught["message"]
                    sender = "突发事件"
                    d_state["current_location"] = "大堂"
                else:
                    reply = "你离开了搜查区域。"
            else:
                # 对话等其他行为：正常消耗行动点
                advance_time(current_state)
                if mode == "TALK":
                    last_npc = d_state.get("last_talk_npc")
                    if last_npc:
                        game_handlers.adjust_trust(d_state, last_npc, "talked_nicely")
                reply = f"你结束了行动。\n⏳ 时间：第{d_state.get('day')}日 {TIME_CYCLES[d_state['time_idx']]}"


        elif "进入游戏" in user_input:
            reply = '''
            轰隆——！
            一道惨白的雷光撕裂夜空，瞬间照亮了头顶那块摇摇欲坠的牌匾——"回马驿"。

            暴雨如注，泥石流早已冲毁了来时的官道。这座深山破驿，此刻已成了一座**死地孤岛**。
            冰冷的雨水顺着你的盔甲缝隙渗入中衣，黏腻阴冷。你下意识地按了按腰间的佩刀，看向身旁二人：内廷总管**李德福**正缩着脖子瑟瑟发抖，死死护着怀里那个被油布层层包裹的**包袱**；而护卫**赵虎**则抹了一把脸上的泥水，神情木然，像一尊没有痛觉的石像。

            "咳咳……咱家这把老骨头，迟早要交代在这鬼地方。"李德福尖声抱怨着，让赵虎一脚踹开了虚掩的大门。

            屋内光线昏黄，空气中弥漫着霉味、湿木头味和一股若有若无的烧纸气。
            柜台后，驿卒**张三**正用一块发黑的抹布擦拭着桌面。见到你们，他抬起那双浑浊的眼睛，嘴角扯出一个卑微却僵硬的笑："几位官爷，路断了吧？今儿晚上，谁也走不了了。"
            不知为何，你觉得他看李德福的眼神，不像是在看客人，倒像是在看一个死人。

            大堂里还有两桌客人，气氛诡异：
            左边窗下，坐着个**锦衣妇人**。她虽衣衫微湿，但发髻一丝不苟，手腕上的佛珠转得飞快。她瞥了你们一眼，目光在你腰间的官刀上停顿了一瞬，随后厌恶地转过头去，低声骂了句"鹰犬"。
            角落阴影里，缩着个**穷酸书生**。他借着微弱的油灯死盯着手中的古籍，嘴里念念有词，手指神经质地抠着书角，对周遭的一切充耳不闻。

            "少废话！要上房！三间！挨着的！"
            李德福并没有理会旁人，他焦躁地把一锭银子拍在柜台上。张三佝偻着腰领路，木楼梯在脚下发出令人牙酸的"吱呀"声。

            到了二楼走廊尽头，李德福猛地转身，那双布满血丝的老眼死死盯着你和赵虎：
            "听着，今晚这包袱若有闪失，咱家要了你们的脑袋！"他压低声音，语气阴狠，"李密卫，你守上半夜，赵虎守下半夜。除了你们俩，谁也不许靠近我的门半步！"
            你抱拳领命，像根钉子一样扎在了门口。赵虎面无表情地瞟了你一眼，回房去了。

            窗外的雨声不仅没停，反而越发凄厉，像无数冤魂在拍打窗棂。
            上半夜平安无事，只有雨声和偶尔传来的楼下……**诵经声**？

            子时刚过，赵虎准时出现在走廊，冲你点了点头。你交出岗位，回房和衣而卧。
            迷迷糊糊中，你似乎听到隔壁赵虎沉重的脚步声，但困意实在太重……

            突然！
            **"啊————！！！"**
            一声凄厉至极的惨叫刺穿了雨幕。

            你猛地惊醒，提刀冲出门外。赵虎也正一脸惊愕地看向楼下。你们冲至大堂，只见大门敞开，冷风夹杂着雨水灌入。
            一个疯疯癫癫的道士正跌坐在后院门口，手里抓着一把湿漉漉的拂尘，颤抖的手指指向雨夜深处：
            "无量天尊……报应……报应啊！"

            顺着他的手指看去，在后院那尊残破的佛龛前，**张三**仰面朝天躺在泥水里。
            他双目圆睁，死死盯着漆黑的夜空，脖子上勒痕深紫，脑袋以一个诡异的角度歪在一边。
            他死了。

            李德福披着外袍出现在楼梯口，面色惨白如纸。他看了一眼尸体，又看了一眼你，从牙缝里挤出一句话：
            "查……给咱家查！都给咱家报上名来，刚才都在哪、干了什么？若有半句虚言，就地格杀！"

            在李德福的威压下，众人神色各异，被迫开口：
            那个在入店时见过的锦衣女人冷哼一声，甚至没有正眼看李德福。她慢条斯理地转着手中的佛珠：
            "**民妇顾氏，单名一个琼字。** 乃是回乡探亲的良家眷属。昨夜我因认床睡不着，一直在房中念经祈福。那惨叫声我也听到了，但我一个妇道人家，哪敢出门查看？哼，倒是你们这群官爷，一来就死人，真是晦气！"

            那位穷酸书生吓得把书都掉在了地上，他哆哆嗦嗦地捡起来，说话结结巴巴：
            "小……小生**韩子敬**，是进京赶考的举子。圣人云，非礼勿视……小生昨晚一直在房中温书，备战春闱，半步未曾离开！那死人的事，和小生一点关系都没有啊！求官爷明察！"说着，他连连摆手求饶，你注意到他的袖口和指尖似乎沾了些黑灰。

            那个疯疯癫癫的道士现在已经缓了过来，朝你嘿嘿一笑，捋了把胡子，眼神透着股算计：
            "无量天尊~ 贫道道号**清虚子**，云游四方，替人消灾解难。昨夜贫道夜观天象……呃，其实是起夜如厕，恰好路过庭院。谁知刚一开门，就看到那张施主倒在地上，魂归西天喽！贫道可是第一个发现尸体的好心人呐！"

            你听着几人的叙述皱了皱眉，罢了，这可是你在李公公面前露脸的好机会，不管是谁在此装神弄鬼你都要查个水落石出！
            '''
        else:
            reply = "请选择操作。"

    else:
        ui_type = result.get("ui_type", "text")
        ui_options = result.get("ui_options", [])
        bg_img = result.get("bg_img")

    new_encrypted_token = encrypt_state(current_state)
    d = current_state["dynamic_state"]

    # ── 信任线索推送：把待推送的线索附在 status_info 里发给前端 ──
    pending_trust = d.pop("pending_trust_clues", [])

    # ── 陈述追踪：从 handler result 中提取新陈述和已揭穿陈述 ──
    new_statements = result.get("new_statements", [])
    confronted_stmts = result.get("confronted_statements", [])

    status_info = {
        "day": d.get("day", 1),
        "time": TIME_CYCLES[d.get("time_idx", 4)],
        "energy": MAX_AP_PER_CYCLE - d.get("ap_used_this_cycle", 0),
        "max_energy": MAX_AP_PER_CYCLE,
        "pending_trust_clues": pending_trust,   # 前端据此弹出 NPC 主动递线索的提示
        "inference_count": len(d.get("inferences_unlocked", [])),
        "new_statements": new_statements,          # 本轮对话新触发的可证伪陈述
        "confronted_statements": confronted_stmts, # 本轮对质中被揭穿的陈述
    }
    return GameResponse(
        reply_text=reply, sender_name=sender, new_encrypted_state=new_encrypted_token,
        ui_type=ui_type, ui_options=ui_options, bg_image=bg_img,
        status_info=status_info
    )

game_handlers.init({
    # 数据
    "UIAction": UIAction,
    "GameResponse": GameResponse,
    "NPC_LIST": NPC_LIST,
    "SOLUTION": SOLUTION,
    "objective_clues_db": objective_clues_db,
    "ROOM_DB": ROOM_DB,
    "TIME_CYCLES": TIME_CYCLES,
    "ALL_LOCATIONS": ALL_LOCATIONS,
    # 函数
    "encrypt_state": encrypt_state,
    "load_npc_profile": load_npc_profile,
    "call_llm": call_llm,
    "get_npc_history": get_npc_history,
    "save_npc_history": save_npc_history,
    "build_llm_messages": build_llm_messages,
    "advance_time": advance_time,

})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
