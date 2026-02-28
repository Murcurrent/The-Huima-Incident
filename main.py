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
    except: return set()

BINDING_FILE = "token_bindings.json"

def load_bindings() -> Dict[str, str]:
    if not os.path.exists(BINDING_FILE): return {}
    try:
        with open(BINDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

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
    "clue_001": { "id": "clue_001", "name": "死者尸体", "location": "后院","search_difficulty": 1, "description": "张三死在后院废弃的【佛龛】前。颈部有极细的勒痕，深入皮肉。死者双目圆睁，面容惊恐，但双手呈现奇怪的'鹰爪'状僵硬，似乎临死前抓伤过凶手。他的膝盖有泥，生前似乎正在跪拜礼佛。", "visible_condition": "none", "hidden": False },
    "clue_002": { "id": "clue_002", "name": "颈部的勒痕", "location": "后院 (尸体)", "search_difficulty": 1,"description": "死者脖颈处有两道清晰的紫黑色勒痕。特别之处在于，勒痕在咽喉处呈现【x】形交叉的，深陷皮肉。这种特殊的手法似乎能阻断呼吸并碎裂喉骨。", "visible_condition": "inspect_corpse" },
    "clue_003": { "id": "clue_003", "name": "死者手部", "location": "后院 (尸体)","search_difficulty": 1, "description": "死者的左手紧握成拳，指骨处有黑紫淤青，右手指甲缝隙有血痕，似乎在死前剧烈抓住了什么丝状物品。", "visible_condition": "inspect_corpse" },
    "clue_004": { "id": "clue_004", "name": "佛龛刮痕", "location": "后院","search_difficulty": 1, "description": "木制佛龛的底座边缘有几道显眼的新鲜刮痕，像是被刀剑撬过留下的痕迹。", "visible_condition": "inspect_shrine" },
    "clue_005": { "id": "clue_005", "name": "混乱的足迹", "location": "后院","search_difficulty": 1, "description": "湿软的泥地上留有三串模糊的足迹：一串脚印宽大深重，花纹粗糙，从后门延伸到佛堂，且没有回头的路。另一串脚印虽然也是男靴样式，但形状细窄，前深后浅且步幅较小。这串脚印在尸体附近徘徊了一下，然后慌乱折返回了大堂。", "visible_condition": "inspect_ground" },
    "clue_006": { "id": "clue_006", "name": "金疮药味", "location": "赵虎房","search_difficulty": 2, "description": "房间若有若无一些金创药的味道，赵虎受伤了？", "visible_condition": "search_room" },
    "clue_007": { "id": "clue_007", "name": "加密的绢帛底稿", "location": "李德福房", "search_difficulty": 3,"description": "藏在行李深处的一卷绢帛，上面写满了难以辨认的加密字符，落款处有模糊的官方印鉴。上面有几个字迹，想必是李德福转译是备注的，只见是【旧内侍】【查清】【清除】几个字。", "visible_condition": "search_room_hard" },
    "clue_008": { "id": "clue_008", "name": "烧焦的手札残页", "location": "顾琼房", "search_difficulty": 1,"description": "火炉的冷灰中有一片未烧尽的纸角，上面残留着秀丽的字迹，隐约可见'复仇'二字。", "visible_condition": "search_fireplace" },
    "clue_009": { "id": "clue_009", "name": "被修改的星盘图", "location": "清虚子房", "search_difficulty": 1,"description": "桌上铺着一张复杂的星盘图，某些星位被人用浓墨重重地涂改过，墨迹尚新。", "visible_condition": "search_table" },
    "clue_010": { "id": "clue_010", "name": "大堂桌椅", "location": "大堂","search_difficulty": 1, "description": "几张桌子散乱摆放。最靠窗那张是那位妇人刚才坐过的。桌上放着一只【茶盏】。", "visible_condition": "search_lobby" },
    "clue_011": { "id": "clue_011", "name": "大堂茶盏", "location": "大堂","search_difficulty": 1, "description": "茶碗稳稳立在正放的茶托上，看起来并无异样。", "visible_condition": "search_lobby_teacup" },
    "clue_012": { "id": "clue_012", "name": "锦套与拂尘","location": "李德福房","search_difficulty": 4, "description": "【关键证物】内里是一柄【金镶玉柄拂尘】。但这拂尘比寻常的要沉重许多。柔软的尘尾中竟有一根极细的【乌金丝】！柄上的机关已损坏，无论你怎么按动，乌金丝都收不回去了。柄上有裂纹，似乎是被人大力使用过，并导致了机关的损坏。柄底刻着一个小篆的'运'字。",  "visible_condition": "search_room_hard" },
    "clue_013": { "id": "clue_013", "name": "小二通铺","location": "大堂侧屋","search_difficulty": 1, "description": "张三的床铺很乱。东西散落一地，似乎被人翻找过。床底的灰中有一处长条形的空白，像是原本藏着什么长条状的东西（比如拂尘）。", "visible_condition": "search_room_zhang" },
    "clue_014": { "id": "clue_014", "name": "未完全烧毁的男靴","location": "灶房", "search_difficulty": 1,"description": "在灶房的炉膛里有什么东西似乎没有烧尽，正冒着黑烟，你把它掏出来是一双鞋型细长的男靴。靴子内里竟是由绸缎包裹的，且尺码偏小。",  "visible_condition": "search_room_kitchen" },
    "clue_015": { "id": "clue_015", "name": "李德福的茶盏", "location": "李德福房", "search_difficulty": 1,"description": "这是李德福自己带的茶碗，怪异的是，底下的【茶托】（漆器底座）竟然被底朝天翻了过来，扣在桌面上。而茶碗却四平八稳地立在翻转的茶托底上。", "visible_condition": "search_room_hard" },
    "clue_016": { "id": "clue_016", "name": "顾琼衣柜", "location": "顾琼房","search_difficulty": 1,"description": "顾夫人的房间。衣柜里挂着几件便于行动的【男式长衫】，看起来她为了路上安全，经常乔装改扮。嗯？似乎少了一个配套的衣物。",  "visible_condition": "search_room_gu" },
    "clue_017": { "id": "clue_017", "name": "泥泞的折扇","location": "后院", "search_difficulty": 1,"description": "在后门处的草丛里，你捡到一把【折扇】。扇面已经湿透沾满了泥，但扇骨是湘妃竹的，颇为雅致。扇面上题着半首诗：'朱门酒肉臭，路有……' 这能是谁的呢？",  "visible_condition": "inspect_ground" },
    "clue_018": { "id": "clue_018", "name": "烧残的诗书","location": "韩子敬房","search_difficulty": 1, "description": "在韩子敬房间的炭盆里，你发现了一本没烧完的【诗稿】。上面写满了对圣人的不满。这是杀头的【反诗】！怪不得他看见李德福（宫里人）吓得脸都白了。",  "visible_condition": "search_room_han" },
    "clue_019": { "id": "clue_019", "name": "木柄拂尘", "search_difficulty": 1,"location": "后院","description": "在尸体旁的泥泞里，掉落着一把【桃木柄拂尘】，现在沾满了泥水。这是道士清虚子随身之物。拂尘的马尾毛有些凌乱毛糙，似被人紧紧攥过。你用力向外扯了扯浮尘的毛，一些毛轻飘飘的从浮沉上掉了下来。",  "visible_condition": "inspect_ground" },
    "clue_020": { "id": "clue_020", "name": "老旧却精美的荷包", "search_difficulty": 2,"location": "清虚子房","description": "在清虚子的布袋里，你搜出了一个精美的刺绣【荷包】，刺绣看起来是很老的款式，而且手法似乎出自宫中，这绝非一个道士可以拥有的东西。里面装了少许铜板和碎银。荷包内里绣着一个的'运'字。这究竟是谁的钱袋？",  "visible_condition": "search_room_qing" }
}

# ==========================================
# 🏠 场景配置
# ==========================================
ROOM_DB = {
    "后院": {
        "name": "后院",
        "furniture_list": ["死者全身", "死者颈部", "死者手部", "佛龛", "泥地", "草丛", "尸体旁的泥泞"],
        "furniture_map": {
            "死者全身": "clue_001", "死者颈部": "clue_002", "死者手部": "clue_003",
            "佛龛": "clue_004", "泥地": "clue_005", "草丛": "clue_017",
            "尸体旁的泥泞": "clue_019"
        },
        "owner": None
    },
    "灶房": {
        "name": "灶房",
        "furniture_list": ["炉膛", "柴火堆", "水缸"],
        "furniture_map": { "炉膛": "clue_014", "水缸": None, "柴火堆": None },
        "owner": None
    },
    "大堂": {
        "name": "大堂",
        "furniture_list": ["大堂桌椅","顾琼的桌子", "柜台", "角落"],
        "furniture_map": { "大堂桌椅": "clue_010", "顾琼的桌子": "clue_011", "柜台": None, "角落": None },
        "owner": None
    },
    "大堂侧屋": {
        "name": "小二通铺",
        "furniture_list": ["床铺", "床底", "枕头", "破衣柜"],
        "furniture_map": { "床铺": None, "床底": "clue_013", "枕头": None, "破衣柜": None },
        "inspect_texts": {
            "床铺":"乱作一团，似乎被人翻过。",
            "枕头": "掉在地上，芯子被翻了出来",
            "破衣柜": "衣柜门是开的，里面乱七八糟，几件衣服掉在了地上。"
        },
        "owner": None
    },
    "李德福房间": {
        "name": "李德福房间",
        "furniture_list": ["行李", "桌子", "床铺", "枕头"],
        "furniture_map": { "行李": "clue_007", "桌子": "clue_015", "床铺": None, "枕头": "clue_012" },
        "inspect_texts": { "床铺": "被褥虽乱，但质地极好。你伸手在被褥间摸索了一番，除了温热的触感外一无所获。不过……这**枕头**看起来有些过于鼓囊了，里面像是塞了什么硬物。" },
        "owner":"npc_lidefu"
    },
    "赵虎房间": {
        "name": "赵虎房间",
        "furniture_list": ["床边", "桌上", "床底"],
        "furniture_map": { "桌上": "clue_006", "床边": None, "床底": None },
        "owner":"npc_zhaohu"
    },
    "顾琼房间": {
        "name": "顾琼房间",
        "furniture_list": ["衣柜", "火炉", "梳妆台"],
        "furniture_map": { "衣柜": "clue_016", "火炉": "clue_008", "梳妆台": None },
        "owner": "npc_guqiong"
    },
    "韩子敬房间": {
        "name": "韩子敬房间",
        "furniture_list": ["书桌", "炭盆"],
        "furniture_map": { "炭盆": "clue_018", "书桌": None },
        "owner":"npc_hanzijing"
    },
    "清虚子房间": {
        "name": "清虚子房间",
        "furniture_list": ["桌子", "布袋", "床铺"],
        "furniture_map": { "桌子": "clue_009", "布袋": "clue_020", "床铺": None },
        "owner":"npc_qingxuzi"
    }
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
    return f"""🕰️ **当前时辰**：{current_time_str}
⚡ **剩余精力**：{remaining_ap}/{MAX_AP_PER_CYCLE}
📍 **所在位置**：{d_state.get('current_location', '未知')}

👀 **听到的动静**：
{chr(10).join(['- ' + r for r in loc_rumors])}"""

def check_auto_trigger_endgame(state: Dict) -> bool:
    d_state = state["dynamic_state"]
    if d_state.get("day", 1) >= 2 and d_state.get("time_idx", 0) == 11:
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
            # 尝试解析 JSON，失败则直接返回原文
            try:
                return json.loads(content).get("reply", content)
            except json.JSONDecodeError:
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
        reply = ('【⏳ 时间已到】\n\n窗外惊雷炸响，第二日的亥时已至。李德福失去了耐心，命人封锁了驿站。\n'
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
            "听着，今晚这包袱若有闪失，咱家要了你们的脑袋！"他压低声音，语气阴狠，"赵虎守上半夜，李密卫，你守下半夜。除了你们俩，谁也不许靠近我的门半步！"
            赵虎抱拳领命，像根钉子一样扎在了门口。你虽心有疑虑——一个太监出宫，究竟带了什么要命的东西？但皇命难违，你只能回房，和衣而卧。

            窗外的雨声不仅没停，反而越发凄厉，像无数冤魂在拍打窗棂。
            迷迷糊糊中，你似乎听到隔壁赵虎沉重的脚步声，还有楼下隐约传来的……**诵经声**？

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
    return GameResponse(
        reply_text=reply, sender_name=sender, new_encrypted_state=new_encrypted_token,
        ui_type=ui_type, ui_options=ui_options, bg_image=bg_img
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
