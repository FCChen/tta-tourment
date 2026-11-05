#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTA (Through the Ages) Replay & Memory Statistics Parser
========================================================
Provides:
1. Twofish-256 CFB + zlib decryptor and ParamTree parser for TTA `.info` & `.v2` files.
2. Binary `.events` log parser (`autosavemultiplayer_finished.events`).
3. Live process memory reader (`ThroughTheAges_steam.exe`) for:
   - `TTA::GameStats` (`vtable RVA = 0x00a02de0`) & `TTA::GameStatsItem` (`vtable RVA = 0x00a02dd8`)
   - `TTA::GraphStats` (`vtable RVA = 0x00a02fe8`) & `TTA::PlayerGraphStats` (`vtable RVA = 0x00a0aebc`)
4. Complete tournament statistics aggregator for all 27 Fun Awards, per-Age Leader/Wonder
   favorites, and automatic 1st-4th place ranking extraction.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import zlib
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

# ============================================================================
# 1. Player Mappings & Card Metadata
# ============================================================================

LOGIN_TO_PLAYER = {
    "gglab": "bala",
    "littlebob": "bob",
    "cookieben": "duck",
    "neil.w": "Neil",
    "dhyellow": "DHYellow",
    "pg30123": "PG",
}

# Permanent CGE Server userID -> In-Game Login mapping for the 6 tournament players
KNOWN_USER_ID_TO_LOGIN = {
    28362: "pg30123",
    28864: "Neil.W",
    28912: "cookieben",
    39880: "DHYellow",
    88675: "gglab",
    89689: "LittleBob",
}

PLAYER_TO_LOGIN = {v: k for k, v in LOGIN_TO_PLAYER.items()}

TOURNAMENT_PLAYERS = ["bala", "bob", "duck", "Neil", "DHYellow", "PG"]

# 10-letter Spectate Code <-> 32-bit sessionID conversion constants (RVA 0x5f7950)
_SPECTATE_VOWELS = "AEIOU"
_SPECTATE_CONSONANTS = "JMNRLBDGVZPTKFSCH"


def decode_game_id(game_id: str) -> Optional[int]:
    """
    Convert a 10-letter TTA spectate code (e.g. 'LATADELUMA') to its 32-bit integer sessionID.
    Formula from ThroughTheAges_steam.exe (RVA 0x5f7950):
      5 consonant-vowel pairs (left-to-right, base 84 = 17 consonants * 5 vowels):
      digit_i = VOWELS.index(V_i) * 17 + CONSONANTS.index(C_i)
      sessionID = sum(digit_i * (84 ** i) for i in range(5))
    """
    if not game_id:
        return None
    code = game_id.strip().upper()
    if len(code) != 10:
        return None
    session_id = 0
    mult = 1
    for i in range(0, 10, 2):
        c_idx = _SPECTATE_CONSONANTS.find(code[i])
        v_idx = _SPECTATE_VOWELS.find(code[i + 1])
        if c_idx < 0 or v_idx < 0:
            return None
        digit = v_idx * 17 + c_idx
        session_id += digit * mult
        mult *= 84
    return session_id


def encode_game_id(session_id: int) -> str:
    """Convert a 32-bit integer sessionID back to its 10-letter TTA spectate code."""
    chars = []
    val = int(session_id)
    for _ in range(5):
        digit = val % 84
        val //= 84
        v_idx = digit // 17
        c_idx = digit % 17
        chars.append(_SPECTATE_CONSONANTS[c_idx])
        chars.append(_SPECTATE_VOWELS[v_idx])
    return "".join(chars)

FEMALE_LEADERS = {
    "BOUDICA",
    "CLEOPATRA",
    "JOAN_OF_ARC",
    "ISABELLA",
    "ELEANOR",
    "CATHERINE",
    "MARIA_THERESA",
    "MARIE_CURIE",
    "MARLENE_DIETRICH",
}

LEADERS_BY_AGE = {
    "A": {
        "HAMMURABI", "HAMMURABI_2", "JULIUS_CAESAR", "JULIUS_CAESAR_2",
        "ALEXANDER_THE_GREAT", "ARISTOTLE", "MOSES", "HOMER",
        "BOUDICA", "CLEOPATRA", "HIPPOCRATES", "CONFUCIUS", "SUN_TZU", "ASHOKA",
    },
    "I": {
        "GENGHIS_KHAN", "FREDERICK_BARBAROSSA", "CHRISTOPHER_COLUMBUS",
        "LEONARDO_DA_VINCI", "MICHELANGELO", "MICHELANGELO_2", "JOAN_OF_ARC",
        "ISABELLA", "ELEANOR", "ZIZKA", "NOSTRADAMUS", "GUTENBERG", "SALADIN",
    },
    "II": {
        "NAPOLEON_BONAPARTE", "NAPOLEON_BONAPARTE_2",
        "WILLIAM_SHAKESPEARE", "WILLIAM_SHAKESPEARE_2",
        "ISAAC_NEWTON", "MAXMILIEN_ROBESPIERRE", "JAMES_COOK",
        "JOHANN_SEBASTIAN_BACH", "JOHANN_SEBASTIAN_BACH_2",
        "CATHERINE", "MARIA_THERESA", "WATT", "NOBEL", "GAUDI", "DARWIN",
    },
    "III": {
        "MAHATMA_GANDHI", "MAHATMA_GANDHI_2", "BILL_GATES", "SID_MEIER",
        "WINSTON_CHURCHILL", "ALBERT_EINSTEIN", "CHARLIE_CHAPLIN",
        "MARIE_CURIE", "MARLENE_DIETRICH", "STEVE_JOBS",
        "NELSON_MANDELA", "COUBERTIN", "IAN_FLEMING",
    },
}

WONDERS_BY_AGE = {
    "A": {
        "PYRAMIDS", "HANGING_GARDENS", "HANGING_GARDENS_2",
        "LIBRARY_OF_ALEXANDRIA", "COLOSSUS", "COLOSSUS_2",
        "COLOSSEUM", "ROMAN_ROADS", "STONEHENGE", "ACROPOLIS",
    },
    "I": {
        "GREAT_WALL", "UNIVERSITAS_CAROLINA", "TAJ_MAHAL",
        "ST_PETERS_BASILICA", "ST_PETERS_BASILICA_2",
        "MACHU_PICCHU", "HIMEJI_CASTLE", "FORBIDDEN_CITY", "SILK_ROAD",
    },
    "II": {
        "TRANSCONTINENTAL_RAILROAD", "TRANSCONTINENTAL_RAILROAD_2",
        "OCEAN_LINER_SERVICE", "OCEAN_LINER_SERVICE_2",
        "EIFFEL_TOWER", "KREMLIN", "HARVARD",
        "STATUE_OF_LIBERTY", "LOUVRE", "SUEZ",
    },
    "III": {
        "FAST_FOOD_CHAINS", "FIRST_SPACE_FLIGHT", "HOLLYWOOD",
        "INTERNET", "EMPIRE_STATE", "UNITED_NATIONS",
        "RED_CROSS", "MANHATTAN_PROJECT",
    },
}

# Per-turn base culture produced by Wonders (Ages A, I, II)
WONDER_BASE_CULTURE_PER_TURN = {
    "PYRAMIDS": 1,
    "HANGING_GARDENS": 1,
    "HANGING_GARDENS_2": 1,
    "LIBRARY_OF_ALEXANDRIA": 1,
    "COLOSSUS": 1,
    "COLOSSUS_2": 1,
    "COLOSSEUM": 1,
    "ROMAN_ROADS": 1,
    "STONEHENGE": 1,
    "ACROPOLIS": 1,
    "GREAT_WALL": 1,
    "UNIVERSITAS_CAROLINA": 1,
    "TAJ_MAHAL": 3,
    "ST_PETERS_BASILICA": 2,
    "ST_PETERS_BASILICA_2": 2,
    "MACHU_PICCHU": 0,
    "HIMEJI_CASTLE": 2,
    "FORBIDDEN_CITY": 1,
    "SILK_ROAD": 2,
    "TRANSCONTINENTAL_RAILROAD": 0,
    "TRANSCONTINENTAL_RAILROAD_2": 0,
    "OCEAN_LINER_SERVICE": 0,
    "OCEAN_LINER_SERVICE_2": 0,
    "EIFFEL_TOWER": 4,
    "KREMLIN": 4,
    "HARVARD": 1,
    "STATUE_OF_LIBERTY": 0,
    "LOUVRE": 3,
    "SUEZ": 1,
}

URBAN_BUILDINGS = {
    "RELIGION", "THEOLOGY", "ORGANIZED_RELIGION",
    "PHILOSOPHY", "ALCHEMY", "SCIENTIFIC_METHOD", "COMPUTERS",
    "PRINTING_PRESS", "JOURNALISM", "MULTIMEDIA",
    "BREAD_AND_CIRCUSES", "TEAM_SPORTS", "PROFESSIONAL_SPORTS", "PROFESSIONAL_SPORTS_2",
    "DRAMA", "OPERA", "MOVIES",
}

GOVERNMENTS = {
    "DESPOTISM", "MONARCHY", "THEOCRACY", "REPUBLIC", "REPUBLIC_2",
    "CONSTITUTIONAL_MONARCHY", "COMMUNISM", "COMMUNISM_2",
    "FUNDAMENTALISM", "FUNDAMENTALISM_2", "DEMOCRACY",
}

CARD_TO_AGE = {}
for _age, _s in LEADERS_BY_AGE.items():
    for _c in _s:
        CARD_TO_AGE[_c] = _age
for _age, _s in WONDERS_BY_AGE.items():
    for _c in _s:
        CARD_TO_AGE[_c] = _age

# Default Traditional Chinese card names (loaded from game YAML if available, with built-in table)
CARD_NAMES_ZH = {
    "BRONZE": "銅礦", "IRON": "鐵礦", "COAL": "煤礦", "OIL": "石油",
    "AGRICULTURE": "農業", "IRRIGATION": "灌溉", "SELECTIVE_BREEDING": "育種", "MECHANIZED_AGRICULTURE": "機械化農業",
    "RELIGION": "宗教", "THEOLOGY": "神學", "ORGANIZED_RELIGION": "制度性宗教",
    "PHILOSOPHY": "哲學", "ALCHEMY": "煉金術", "SCIENTIFIC_METHOD": "科學方法", "COMPUTERS": "電腦",
    "PRINTING_PRESS": "印刷術", "JOURNALISM": "新聞學", "MULTIMEDIA": "多媒體",
    "BREAD_AND_CIRCUSES": "麵包和馬戲", "TEAM_SPORTS": "團隊運動", "PROFESSIONAL_SPORTS": "職業運動", "PROFESSIONAL_SPORTS_2": "職業運動",
    "DRAMA": "戲劇", "OPERA": "歌劇", "MOVIES": "電影",
    "DESPOTISM": "獨裁", "MONARCHY": "君主", "THEOCRACY": "神權政治", "REPUBLIC": "共和", "REPUBLIC_2": "共和",
    "CONSTITUTIONAL_MONARCHY": "君主立憲", "COMMUNISM": "共產主義", "COMMUNISM_2": "共產主義",
    "FUNDAMENTALISM": "基本教義", "FUNDAMENTALISM_2": "基本教義", "DEMOCRACY": "民主",
    "HAMMURABI": "漢摩拉比", "HAMMURABI_2": "漢摩拉比",
    "JULIUS_CAESAR": "尤利烏斯・凱撒", "JULIUS_CAESAR_2": "尤利烏斯・凱撒",
    "ALEXANDER_THE_GREAT": "亞歷山大大帝", "ARISTOTLE": "亞里斯多德", "MOSES": "摩西", "HOMER": "荷馬",
    "BOUDICA": "布狄卡", "CLEOPATRA": "克麗奧佩托拉", "HIPPOCRATES": "希波克拉底", "CONFUCIUS": "孔子",
    "SUN_TZU": "孫子", "ASHOKA": "阿育王",
    "GENGHIS_KHAN": "成吉思汗", "FREDERICK_BARBAROSSA": "巴巴羅薩", "CHRISTOPHER_COLUMBUS": "克里斯多福・哥倫布",
    "LEONARDO_DA_VINCI": "李奧納多・達文西", "MICHELANGELO": "米開朗基羅", "MICHELANGELO_2": "米開朗基羅",
    "JOAN_OF_ARC": "聖女貞德", "ISABELLA": "伊莎貝拉一世", "ELEANOR": "亞奎丹的艾莉諾",
    "ZIZKA": "揚·傑式卡", "NOSTRADAMUS": "諾斯特拉達姆士", "GUTENBERG": "約翰內斯·谷登堡", "SALADIN": "薩拉丁",
    "NAPOLEON_BONAPARTE": "拿破崙・波拿巴", "NAPOLEON_BONAPARTE_2": "拿破崙・波拿巴",
    "WILLIAM_SHAKESPEARE": "威廉・莎士比亞", "WILLIAM_SHAKESPEARE_2": "威廉・莎士比亞",
    "ISAAC_NEWTON": "艾薩克・牛頓", "MAXMILIEN_ROBESPIERRE": "馬克西米連・羅伯斯比爾",
    "JAMES_COOK": "詹姆斯・庫克", "JOHANN_SEBASTIAN_BACH": "約翰・塞巴斯蒂安・巴哈", "JOHANN_SEBASTIAN_BACH_2": "約翰・塞巴斯蒂安・巴哈",
    "CATHERINE": "凱薩琳大帝", "MARIA_THERESA": "瑪麗亞·特蕾莎", "WATT": "詹姆斯·瓦特",
    "NOBEL": "阿佛烈·諾貝爾", "GAUDI": "安東尼·高第", "DARWIN": "查爾斯·達爾文",
    "MAHATMA_GANDHI": "聖雄甘地", "MAHATMA_GANDHI_2": "聖雄甘地", "BILL_GATES": "比爾・蓋茲",
    "SID_MEIER": "席德・梅爾", "WINSTON_CHURCHILL": "溫斯頓・邱吉爾", "ALBERT_EINSTEIN": "阿爾伯特・愛因斯坦",
    "CHARLIE_CHAPLIN": "查理・卓别林", "MARIE_CURIE": "瑪麗・居禮", "MARLENE_DIETRICH": "瑪琳·黛德麗",
    "STEVE_JOBS": "史蒂夫・賈伯斯", "NELSON_MANDELA": "納爾遜·曼德拉", "COUBERTIN": "顧拜旦", "IAN_FLEMING": "伊恩·佛萊明",
    "PYRAMIDS": "金字塔", "HANGING_GARDENS": "空中花園", "HANGING_GARDENS_2": "空中花園",
    "LIBRARY_OF_ALEXANDRIA": "亞歷山大圖書館", "COLOSSUS": "巨人像", "COLOSSUS_2": "巨人像",
    "COLOSSEUM": "羅馬競技場", "ROMAN_ROADS": "羅馬大道", "STONEHENGE": "巨石陣", "ACROPOLIS": "雅典衛城",
    "GREAT_WALL": "萬里長城", "UNIVERSITAS_CAROLINA": "查理大學", "TAJ_MAHAL": "泰姬瑪哈陵",
    "ST_PETERS_BASILICA": "聖彼得大教堂", "ST_PETERS_BASILICA_2": "聖彼得大教堂",
    "MACHU_PICCHU": "馬丘比丘", "HIMEJI_CASTLE": "姫路城", "FORBIDDEN_CITY": "紫禁城", "SILK_ROAD": "絲路",
    "TRANSCONTINENTAL_RAILROAD": "跨洲鐵路", "TRANSCONTINENTAL_RAILROAD_2": "洲際鐵路",
    "OCEAN_LINER_SERVICE": "遠洋郵輪", "OCEAN_LINER_SERVICE_2": "遠洋郵輪",
    "EIFFEL_TOWER": "艾菲爾鐵塔", "KREMLIN": "克里姆林宮", "HARVARD": "哈佛大學",
    "STATUE_OF_LIBERTY": "自由女神像", "LOUVRE": "羅浮宮", "SUEZ": "蘇伊士運河",
    "FAST_FOOD_CHAINS": "連鎖速食店", "FIRST_SPACE_FLIGHT": "太空飛行", "HOLLYWOOD": "好萊塢",
    "INTERNET": "網際網路", "EMPIRE_STATE": "帝國大廈", "UNITED_NATIONS": "聯合國",
    "RED_CROSS": "國際紅十字會", "MANHATTAN_PROJECT": "曼哈頓計畫",
    "IMPACT_OF_INDUSTRY": "工業的影響", "IMPACT_OF_AGRICULTURE": "農業的影響",
    "IMPACT_OF_COMPETITION": "競爭的影響", "IMPACT_OF_PROGRESS": "進步的影響",
    "IMPACT_OF_WONDERS": "奇觀的影響", "IMPACT_OF_POPULATION": "人口的影響",
    "IMPACT_OF_COLONIES": "殖民的影響", "IMPACT_OF_GOVERNMENT": "政府的影響",
    "IMPACT_OF_ARCHITECTURE": "建築的影響", "IMPACT_OF_STRENGTH": "軍事的影響",
    "IMPACT_OF_HAPPINESS": "幸福的影響", "IMPACT_OF_SCIENCE": "科學的影響",
    "IMPACT_OF_TECHNOLOGY": "科技的影響", "IMPACT_OF_BALANCE": "均衡的影響",
    "IMPACT_OF_VARIETY": "多樣性的影響", "IMPACT_OF_MODERNIZATION": "現代化的影響",
    "IMPACT_OF_HARMONY": "和諧的影響", "IMPACT_OF_CULTURE": "文化的影響",
    "WAR_OVER_TERRITORY": "領土戰爭", "WAR_OVER_TECHNOLOGY": "科技戰爭",
    "WAR_OVER_CULTURE": "文化戰爭", "WAR_HYBRID": "綜合戰爭",
    "RAID_1": "侵略：突襲", "PLUNDER_1": "侵略：掠劫", "ENSLAVE_1": "侵略：奴役", "KIDNAPPING": "侵略：綁架",
    "RAID_2": "侵略：突襲", "PLUNDER_2": "侵略：掠劫", "SPY": "侵略：間諜", "ANNEX": "侵略：併吞", "INFILTRATE": "侵略：潛入",
    "RAID_3": "侵略：突襲", "PLUNDER_3": "侵略：掠劫", "ARMED_INTERVENTION": "侵略：武力干涉", "EXPLOITING": "侵略：佔領",
}


def normalize_card_id(card_id: str) -> str:
    """Normalize `_2` card variants so e.g. `HAMMURABI_2` and `HAMMURABI` count together."""
    if not card_id:
        return ""
    card_id = resolve_card_token(card_id)
    if card_id.endswith("_2") and card_id[:-2] in CARD_NAMES_ZH:
        return card_id[:-2]
    return card_id


# ----------------------------------------------------------------------------
# Card database from the game's own data files (`Cards/*.yaml` + `exp_data/v*/*.yaml`).
# `TTA::GameStats` lines reference cards by numeric `statId` (e.g. BRONZE = 31,
# CHARLIE_CHAPLIN = 315); this table maps them back to card names automatically.
# ----------------------------------------------------------------------------
TTA_GAME_DIR = r"E:\steam\steamapps\common\Through the Ages"
_CARD_DB: Optional[Dict[int, Dict[str, Any]]] = None
_MIL_DB: Dict[int, Dict[str, Any]] = {}


def load_card_db(game_dir: str = TTA_GAME_DIR) -> Dict[int, Dict[str, Any]]:
    """Return `{statId: {"id": name, "age": int, "type": str}}` merged from base + all expansion versions."""
    global _CARD_DB
    if _CARD_DB is not None:
        return _CARD_DB
    db: Dict[int, Dict[str, Any]] = {}
    files: List[str] = []
    exp_root = os.path.join(game_dir, "exp_data")
    vers: List[str] = []
    if os.path.isdir(exp_root):
        vers = sorted(
            (d for d in os.listdir(exp_root) if d.startswith("v") and d[1:].isdigit()),
            key=lambda d: int(d[1:]),
        )
    # Military first, then Civil, so Civil statIds win on collisions (leaders/wonders are Civil cards)
    for fn in ("MilitaryCards.yaml", "CivilCards.yaml"):
        files.append(os.path.join(game_dir, "Cards", fn))
        for d in vers:
            files.append(os.path.join(exp_root, d, fn))
    for path in files:
        if not os.path.exists(path):
            continue
        cur: Dict[str, Any] = {}

        def _flush():
            if "id" in cur and "statId" in cur:
                entry = {"id": cur["id"], "age": cur.get("age"), "type": cur.get("type")}
                db[cur["statId"]] = entry
                if "Military" in os.path.basename(path):
                    _MIL_DB[cur["statId"]] = entry

        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("- id:"):
                    _flush()
                    cur = {"id": line.split(":", 1)[1].strip()}
                elif line.startswith("  ") and not line.startswith("   ") and ":" in line:
                    key, val = line.strip().split(":", 1)
                    val = val.strip()
                    if key == "statId" and val.lstrip("-").isdigit():
                        cur["statId"] = int(val)
                    elif key == "age" and val.lstrip("-").isdigit():
                        cur["age"] = int(val)
                    elif key == "type":
                        cur["type"] = val
            _flush()
    _CARD_DB = db
    return db


def resolve_card_token(token: str, military: bool = False) -> str:
    """Translate a numeric GameStats `statId` token into its card name (non-numeric tokens are returned as-is).
    Civil and Military cards have separate statId spaces: Civil wins by default, `military=True` for events/tactics."""
    if token and token.isdigit():
        db = load_card_db()
        info = (_MIL_DB.get(int(token)) if military else None) or db.get(int(token))
        if info:
            return info["id"]
    return token


def card_age_label(token: str) -> Optional[str]:
    """Return the Age label ('A', 'I', 'II', 'III') of a card from the game's card database."""
    name = resolve_card_token(token)
    for info in load_card_db().values():
        if info["id"] == name and info.get("age") is not None:
            return ["A", "A", "I", "II", "III"][max(-1, min(3, info["age"])) + 1]
    return None


def get_card_zh(card_id: str) -> str:
    norm = normalize_card_id(card_id)
    return CARD_NAMES_ZH.get(norm, CARD_NAMES_ZH.get(card_id, card_id))


def map_login_to_player(login: str) -> str:
    if not login:
        return login
    return LOGIN_TO_PLAYER.get(login.strip().lower(), login.strip())


# ============================================================================
# 2. Twofish-256 CFB + zlib Decryptor & ParamTree Parser (`.info` / `.v2`)
# ============================================================================

_Q0_BASE = bytes.fromhex(
    "0801070d060f0302000b05090e0c0a040e0c0b08010203050f040a060700090d"
    "0b0a050e060d09000c080f03020407010d070f040102060e090b030008050c0a"
)
_Q1_BASE = bytes.fromhex(
    "02080b0d0f07060e03010904000a0c05010e020b040c0307060d0a050f090008"
    "040c07050106090a000e0d08020b030f0b0905010c030d0e0604070f0200080a"
)


def _make_q_table(t: bytes) -> List[int]:
    t0, t1, t2, t3 = t[:16], t[16:32], t[32:48], t[48:64]
    q = [0] * 256
    for x in range(256):
        a0, b0 = x >> 4, x & 15
        a1 = a0 ^ b0
        b1 = a0 ^ ((b0 >> 1) | ((b0 << 3) & 15)) ^ ((8 * a0) & 15)
        a2, b2 = t0[a1], t1[b1]
        a3 = a2 ^ b2
        b3 = a2 ^ ((b2 >> 1) | ((b2 << 3) & 15)) ^ ((8 * a2) & 15)
        a4, b4 = t2[a3], t3[b3]
        q[x] = (b4 << 4) | a4
    return q


_Q0 = _make_q_table(_Q0_BASE)
_Q1 = _make_q_table(_Q1_BASE)
_MDS = [[0] * 256 for _ in range(4)]

for _x in range(256):
    _s0 = _Q0[_x]
    _d2 = (_s0 >> 1) ^ (0xB4 if (_s0 & 1) else 0)
    _c2 = (_d2 >> 1) ^ (0xB4 if (_d2 & 1) else 0) ^ _s0
    _ef0 = _c2 ^ _d2
    _b50 = _c2
    _MDS[1][_x] = (_s0 << 24) | (_b50 << 16) | (_ef0 << 8) | _ef0
    _MDS[3][_x] = (_b50 << 24) | (_ef0 << 16) | (_s0 << 8) | _b50

    _s1 = _Q1[_x]
    _d2 = (_s1 >> 1) ^ (0xB4 if (_s1 & 1) else 0)
    _c2 = (_d2 >> 1) ^ (0xB4 if (_d2 & 1) else 0) ^ _s1
    _ef1 = _c2 ^ _d2
    _b51 = _c2
    _MDS[0][_x] = (_ef1 << 24) | (_ef1 << 16) | (_b51 << 8) | _s1
    _MDS[2][_x] = (_ef1 << 24) | (_s1 << 16) | (_ef1 << 8) | _b51


def _rol32(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _ror32(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def _h32(x_word: int, l_words: List[int]) -> int:
    b0 = x_word & 0xFF
    b1 = (x_word >> 8) & 0xFF
    b2 = (x_word >> 16) & 0xFF
    b3 = (x_word >> 24) & 0xFF
    k = len(l_words)
    if k >= 4:
        l3 = l_words[3]
        b0 = _Q1[b0] ^ (l3 & 0xFF)
        b1 = _Q0[b1] ^ ((l3 >> 8) & 0xFF)
        b2 = _Q0[b2] ^ ((l3 >> 16) & 0xFF)
        b3 = _Q1[b3] ^ ((l3 >> 24) & 0xFF)
    if k >= 3:
        l2 = l_words[2]
        b0 = _Q1[b0] ^ (l2 & 0xFF)
        b1 = _Q1[b1] ^ ((l2 >> 8) & 0xFF)
        b2 = _Q0[b2] ^ ((l2 >> 16) & 0xFF)
        b3 = _Q0[b3] ^ ((l2 >> 24) & 0xFF)
    l1, l0 = l_words[1], l_words[0]
    b0 = _Q0[_Q0[b0] ^ (l1 & 0xFF)] ^ (l0 & 0xFF)
    b1 = _Q0[_Q1[b1] ^ ((l1 >> 8) & 0xFF)] ^ ((l0 >> 8) & 0xFF)
    b2 = _Q1[_Q0[b2] ^ ((l1 >> 16) & 0xFF)] ^ ((l0 >> 16) & 0xFF)
    b3 = _Q1[_Q1[b3] ^ ((l1 >> 24) & 0xFF)] ^ ((l0 >> 24) & 0xFF)
    return _MDS[0][b0] ^ _MDS[1][b1] ^ _MDS[2][b2] ^ _MDS[3][b3]


class Twofish:
    def __init__(self, key: bytes):
        k = len(key) // 8
        words = list(struct.unpack(f"<{2*k}I", key))
        me, mo = words[0::2], words[1::2]
        s = []
        for i in range(k):
            buf = [0, 0, 0, 0] + list(key[8 * i : 8 * i + 8])
            for pos in range(11, 3, -1):
                b = buf[pos]
                c = ((b << 1) ^ (0x4D if (b & 0x80) else 0)) & 0xFF
                d = ((b >> 1) ^ (0xA6 if (b & 1) else 0) ^ c) & 0xFF
                buf[pos - 1] ^= d
                buf[pos - 2] ^= c
                buf[pos - 3] ^= d
                buf[pos - 4] ^= b
            s.append(buf[0] | (buf[1] << 8) | (buf[2] << 16) | (buf[3] << 24))
        s.reverse()
        self.K = []
        for i in range(20):
            a = _h32(2 * i * 0x01010101, me)
            b = _rol32(_h32((2 * i + 1) * 0x01010101, mo), 8)
            self.K.append((a + b) & 0xFFFFFFFF)
            self.K.append(_rol32((a + 2 * b) & 0xFFFFFFFF, 9))
        self.s0, self.s1, self.s2, self.s3 = [0] * 256, [0] * 256, [0] * 256, [0] * 256
        for x in range(256):
            b0 = b1 = b2 = b3 = x
            if k >= 4:
                l3 = s[3]
                b0 = _Q1[b0] ^ (l3 & 0xFF)
                b1 = _Q0[b1] ^ ((l3 >> 8) & 0xFF)
                b2 = _Q0[b2] ^ ((l3 >> 16) & 0xFF)
                b3 = _Q1[b3] ^ ((l3 >> 24) & 0xFF)
            if k >= 3:
                l2 = s[2]
                b0 = _Q1[b0] ^ (l2 & 0xFF)
                b1 = _Q1[b1] ^ ((l2 >> 8) & 0xFF)
                b2 = _Q0[b2] ^ ((l2 >> 16) & 0xFF)
                b3 = _Q0[b3] ^ ((l2 >> 24) & 0xFF)
            l1, l0 = s[1], s[0]
            self.s0[x] = _MDS[0][_Q0[_Q0[b0] ^ (l1 & 0xFF)] ^ (l0 & 0xFF)]
            self.s1[x] = _MDS[1][_Q0[_Q1[b1] ^ ((l1 >> 8) & 0xFF)] ^ ((l0 >> 8) & 0xFF)]
            self.s2[x] = _MDS[2][_Q1[_Q0[b2] ^ ((l1 >> 16) & 0xFF)] ^ ((l0 >> 16) & 0xFF)]
            self.s3[x] = _MDS[3][_Q1[_Q1[b3] ^ ((l1 >> 24) & 0xFF)] ^ ((l0 >> 24) & 0xFF)]

    def encrypt_block(self, block: bytes) -> bytes:
        x0, x1, x2, x3 = struct.unpack("<4I", block)
        k = self.K
        s0, s1, s2, s3 = self.s0, self.s1, self.s2, self.s3
        x0 ^= k[0]
        x1 ^= k[1]
        x2 ^= k[2]
        x3 ^= k[3]
        for r in range(0, 16, 2):
            t0 = s0[x0 & 0xFF] ^ s1[(x0 >> 8) & 0xFF] ^ s2[(x0 >> 16) & 0xFF] ^ s3[(x0 >> 24) & 0xFF]
            t1 = s0[(x1 >> 24) & 0xFF] ^ s1[x1 & 0xFF] ^ s2[(x1 >> 8) & 0xFF] ^ s3[(x1 >> 16) & 0xFF]
            x2 = _ror32(x2 ^ ((t0 + t1 + k[2 * r + 8]) & 0xFFFFFFFF), 1)
            x3 = _rol32(x3, 1) ^ ((t0 + 2 * t1 + k[2 * r + 9]) & 0xFFFFFFFF)

            t0 = s0[x2 & 0xFF] ^ s1[(x2 >> 8) & 0xFF] ^ s2[(x2 >> 16) & 0xFF] ^ s3[(x2 >> 24) & 0xFF]
            t1 = s0[(x3 >> 24) & 0xFF] ^ s1[x3 & 0xFF] ^ s2[(x3 >> 8) & 0xFF] ^ s3[(x3 >> 16) & 0xFF]
            x0 = _ror32(x0 ^ ((t0 + t1 + k[2 * r + 10]) & 0xFFFFFFFF), 1)
            x1 = _rol32(x1, 1) ^ ((t0 + 2 * t1 + k[2 * r + 11]) & 0xFFFFFFFF)
        return struct.pack("<4I", x2 ^ k[4], x3 ^ k[5], x0 ^ k[6], x1 ^ k[7])


_TTA_KEY = bytes.fromhex("5038435454414040424548585a5631fed0400330205f54544140404532000024")
_TTA_IV = bytes.fromhex("989cda11054892104a0f9510fc7c9310")
_TTA_TF = Twofish(_TTA_KEY)


def decrypt_tta_file(raw_bytes: bytes) -> bytes:
    """Decrypt a TTA `.info` or `.v2` file (Twofish-256 CFB + zlib)."""
    state = bytearray(_TTA_IV)
    out = bytearray(len(raw_bytes))
    pos = 0
    for i, c in enumerate(raw_bytes):
        if pos == 0:
            state = bytearray(_TTA_TF.encrypt_block(bytes(state)))
        out[i] = c ^ state[pos]
        state[pos] = c
        pos = (pos + 1) & 15
    return zlib.decompress(bytes(out))


def parse_param_tree(data: bytes, offset: int = 0) -> Tuple[Any, int]:
    """Parse binary ParamTree inside a decrypted `.info` or `.v2` file."""
    if offset == 0:
        offset = 4  # skip 4-byte version header (0x00000001)
    t = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    if t == 0:
        return None, offset
    elif t in (1, 9):
        return bool(data[offset]), offset + 1
    elif t == 2:
        cnt = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        arr = []
        for _ in range(cnt):
            item, offset = parse_param_tree(data, offset)
            arr.append(item)
        return arr, offset
    elif t == 3:
        cnt = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        obj = {}
        for _ in range(cnt):
            klen = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            key = data[offset : offset + klen].decode("utf-8", errors="replace")
            offset += klen
            val, offset = parse_param_tree(data, offset)
            obj[key] = val
        return obj, offset
    elif t == 4:
        slen = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        s = data[offset : offset + slen].decode("utf-8", errors="replace")
        offset += slen
        return s, offset
    elif t == 5:
        v = struct.unpack_from("<i", data, offset)[0]
        return v, offset + 4
    elif t == 6:
        v = struct.unpack_from("<q", data, offset)[0]
        return v, offset + 8
    elif t == 7:
        v = struct.unpack_from("<d", data, offset)[0]
        return v, offset + 8
    else:
        raise ValueError(f"Unknown ParamTree node type {t} at offset {offset - 4}")


def parse_info_file(info_path: str) -> Dict[str, Any]:
    with open(info_path, "rb") as f:
        raw = f.read()
    dec = decrypt_tta_file(raw)
    tree, _ = parse_param_tree(dec)
    return tree


# ============================================================================
# 3. Binary `.events` File Parser (`autosavemultiplayer_finished.events`)
# ============================================================================

EVENT_NAMES = {
    400002: "EventUndo",
    400003: "EventRedo",
    400004: "EventEndTurn",
    400005: "EventTakeCard",
    400006: "EventPlayTechnology",
    400007: "EventPlayLeader",
    400008: "EventPlayActionCard",
    400009: "EventBuildWonder",
    400010: "EventBuildStuff",
    400011: "EventIncreasePopulation",
    400012: "EventDestroyStuff",
    400013: "EventUpgradeStuff",
    400014: "EventBarbarossa",
    400015: "EventLiner",
    400016: "EventChurchill",
    400017: "EventDiscardMilitaryCards",
    400018: "EventPlayTactic",
    400019: "EventChangeTactic",
    400020: "EventPlayEvent",
    400021: "EventPassPolitic",
    400022: "EventSendAnswer",
    400023: "EventSacrificeUnits",
    400024: "EventPlayAggressionOrWar",
    400025: "EventStealFoodAndResources",
    400026: "EventSetSacrificeUnitState",
    400027: "EventAnex",
    400028: "EventDestroyOpponentBuildings",
    400029: "EventOfferPact",
    400030: "EventRespondPact",
    400031: "EventColonisationBid",
    400032: "EventEditCard",
    400033: "EventEditCubes",
    400034: "EventEditValues",
    400035: "EventAggresionDefense",
    400036: "EventStealBlueTechs",
    400037: "EventColonizeWithColumbus",
    400038: "EventConquerWithAlexander",
    400039: "EventCancelPact",
    400040: "EventTutorialCommand",
    400041: "EventResign",
    400042: "EventGutenbergTake",
    400043: "EventGutenbergDevelop",
    400044: "EventGutenbergBuild",
    400045: "EventGutenbergUpgrade",
    400046: "EventPlayAggressionWithSacrifice",
    400047: "EventAggresionDefenseWithSacrifice",
    400048: "EventSacrificeUnitInWar",
    400049: "EventEndTurnWithSaladin",
    400050: "EventCatherinesExpansion",
    400051: "EventUseLouvre",
    400052: "EventPlayTechnologyJobs",
    400053: "EventCoubertinOlympic",
    400054: "EventBuildWonderOtherPl",
    400055: "EventDevelopGutenberg",
    400056: "EventBuildGutenberg",
    400057: "EventUpgradeGutenberg",
    400058: "EventUseLouvreN",
    400059: "EventChangeTacticNew",
}


def parse_events_file(events_path: str) -> List[Dict[str, Any]]:
    """Parse all binary event packets from a TTA `.events` file."""
    with open(events_path, "rb") as f:
        data = f.read()
    return parse_events_bytes(data)


def parse_param_tree_raw(data: bytes, offset: int) -> Tuple[Any, int]:
    """Like `parse_param_tree` but keeps type-4 string values as raw `bytes` (needed for binary event blobs)."""
    t = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    if t == 0:
        return None, offset
    if t in (1, 9):
        return bool(data[offset]), offset + 1
    if t == 2:
        cnt = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        arr = []
        for _ in range(cnt):
            item, offset = parse_param_tree_raw(data, offset)
            arr.append(item)
        return arr, offset
    if t == 3:
        cnt = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        obj = {}
        for _ in range(cnt):
            klen = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            key = data[offset : offset + klen].decode("utf-8", errors="replace")
            offset += klen
            val, offset = parse_param_tree_raw(data, offset)
            obj[key] = val
        return obj, offset
    if t == 4:
        slen = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if slen > len(data) - offset:
            raise ValueError("string overflow")
        return data[offset : offset + slen], offset + slen
    if t == 5:
        return struct.unpack_from("<i", data, offset)[0], offset + 4
    if t == 6:
        return struct.unpack_from("<q", data, offset)[0], offset + 8
    if t == 7:
        return struct.unpack_from("<d", data, offset)[0], offset + 8
    raise ValueError(f"Unknown ParamTree node type {t} at offset {offset - 4}")


def parse_events_bytes(data: bytes) -> List[Dict[str, Any]]:
    """Parse all binary event packets from a raw TTA event byte stream."""
    data = b"\x00" * 24 + data + b"\x00" * 32

    raw_records = []
    for i in range(24, len(data) - 22):
        eid1, eid2 = struct.unpack_from("<II", data, i)
        if 400002 <= eid1 <= 400059 and eid1 == eid2:
            ts = struct.unpack_from("<Q", data, i - 12)[0]
            if 1500000000000 <= ts <= 2500000000000:
                slot1, uid = struct.unpack_from("<II", data, i + 9)
                slot2 = struct.unpack_from("<I", data, i + 18)[0]
                raw_records.append((i - 12, ts, eid1, slot1, uid, slot2, i + 22))

    events = []
    for idx, (start_off, ts, eid, slot1, uid, slot2, payload_start) in enumerate(raw_records):
        end_off = raw_records[idx + 1][0] if idx + 1 < len(raw_records) else len(data)
        payload = data[payload_start:end_off]
        ename = EVENT_NAMES.get(eid, f"Event_{eid}")
        strs = []
        pos = 0
        while pos <= len(payload) - 4:
            slen = struct.unpack_from("<I", payload, pos)[0]
            if 2 <= slen <= 40 and pos + 4 + slen <= len(payload):
                s_bytes = payload[pos + 4 : pos + 4 + slen]
                if all(32 <= b < 127 for b in s_bytes):
                    strs.append(s_bytes.decode("ascii"))
                    pos += 4 + slen
                    continue
            pos += 1
        events.append({
            "index": idx,
            "timestamp": ts,
            "event_id": eid,
            "event_name": ename,
            "player_slot": slot1,
            "user_id": uid,
            "active_slot": slot2,
            "session_id": struct.unpack_from("<I", data, start_off + 8)[0],
            "strings": strs,
            "payload_hex": payload.hex(),
        })
    return events


# ============================================================================
# 4. Live Memory Scanner for `ThroughTheAges_steam.exe`
# ============================================================================

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
MEM_COMMIT = 0x1000
PAGE_READWRITE = 0x04
PAGE_READONLY = 0x02
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

# RVAs of target vtables from ThroughTheAges_steam.exe (ImageBase = 0x00400000)
RVA_VTABLE_GAMESTATS = 0x00A02DE0
RVA_VTABLE_GAMESTATS_ITEM = 0x00A02DD8
RVA_VTABLE_GRAPHSTATS = 0x00A02FE8
RVA_VTABLE_PLAYER_GRAPHSTATS = 0x00A0AEBC
RVA_VTABLE_NET_PLAYER = 0x00A1C44C
RVA_VTABLE_USER_PROFILE = 0x00A1C0F8
RVA_VTABLE_EVENT_CANCEL_PACT = 0x009FBBAC  # TTA::EventCancelPact (player actively cancels a pact = 毀約)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.POINTER(wt.BYTE)),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * 260),
    ]


class MEMORY_BASIC_INFORMATION32(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", wt.DWORD),
        ("AllocationBase", wt.DWORD),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", wt.DWORD),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


class TTAMemoryReader:
    """Reads `TTA::GameStats` and `TTA::PlayerGraphStats` directly from `ThroughTheAges_steam.exe` RAM."""

    def __init__(self, exe_name: str = "ThroughTheAges_steam.exe"):
        self.exe_name = exe_name
        self.k32 = ctypes.windll.kernel32
        self.pid: Optional[int] = None
        self.h_proc: Optional[int] = None
        self.module_base: Optional[int] = None

    def attach(self, writable: bool = True) -> bool:
        self.pid = self._find_pid()
        if not self.pid:
            return False
        self.module_base = self._find_module_base(self.pid)
        if not self.module_base:
            return False
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.h_proc = self.k32.OpenProcess(access, False, self.pid)
        if not self.h_proc and writable:
            self.h_proc = self.k32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, self.pid
            )
        return bool(self.h_proc)

    def close(self):
        if self.h_proc:
            self.k32.CloseHandle(self.h_proc)
            self.h_proc = None

    def _find_pid(self) -> Optional[int]:
        snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return None
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        found_pid = None
        if self.k32.Process32First(snap, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode("utf-8", errors="ignore")
                if name.lower() == self.exe_name.lower():
                    found_pid = pe.th32ProcessID
                    break
                if not self.k32.Process32Next(snap, ctypes.byref(pe)):
                    break
        self.k32.CloseHandle(snap)
        return found_pid

    def _find_module_base(self, pid: int) -> Optional[int]:
        snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        if snap == -1:
            return None
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(MODULEENTRY32)
        base = None
        if self.k32.Module32First(snap, ctypes.byref(me)):
            while True:
                name = me.szModule.decode("utf-8", errors="ignore")
                if name.lower() == self.exe_name.lower():
                    base = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value
                    break
                if not self.k32.Module32Next(snap, ctypes.byref(me)):
                    break
        self.k32.CloseHandle(snap)
        return base

    def read_bytes(self, addr: int, size: int) -> Optional[bytes]:
        if not self.h_proc or addr <= 0x10000 or addr > 0x7FFFFFFF:
            return None
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        ok = self.k32.ReadProcessMemory(
            self.h_proc, ctypes.c_void_p(addr), buf, size, ctypes.byref(bytes_read)
        )
        if not ok or bytes_read.value != size:
            return None
        return buf.raw

    def read_u32(self, addr: int) -> Optional[int]:
        b = self.read_bytes(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def write_u32(self, addr: int, val: int) -> bool:
        if not self.h_proc or addr <= 0x10000 or addr > 0x7FFFFFFF:
            return False
        buf = struct.pack("<I", val & 0xFFFFFFFF)
        bytes_written = ctypes.c_size_t(0)
        ok = self.k32.WriteProcessMemory(
            self.h_proc, ctypes.c_void_p(addr), buf, 4, ctypes.byref(bytes_written)
        )
        return bool(ok and bytes_written.value == 4)

    def read_msvc_string(self, addr: int, max_len: int = 512) -> Optional[str]:
        """
        Read 24-byte TTA/STLPort std::string (`ThroughTheAges_steam.exe` RVA 0x5815c6):
        - Inline SSO (`raw[23] <= 23`): length = `23 - raw[23]`, UTF-8 bytes at `raw[0:length]`.
        - Heap string (`raw[23] == 0x80`): `ptr = u32(raw, 0)`, `size = u32(raw, 8)`, `cap = u32(raw, 16)`.
        """
        raw = self.read_bytes(addr, 24)
        if not raw:
            return None
        tag = raw[23]
        if tag <= 23:
            size = 23 - tag
            if size == 0:
                return ""
            s_bytes = raw[:size]
            if b"\x00" in s_bytes:
                return None
        elif tag == 0x80:
            ptr, _, size, _, cap = struct.unpack_from("<IIIII", raw, 0)
            if size == 0:
                return ""
            if size > max_len or cap < size or cap > 1048576 or ptr <= 0x10000 or ptr >= 0x7FFF0000:
                return None
            s_bytes = self.read_bytes(ptr, size)
            if not s_bytes or b"\x00" in s_bytes:
                return None
        else:
            return None
        try:
            return s_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def read_int_vector(self, addr: int, max_elems: int = 200) -> Optional[List[int]]:
        """Read 32-bit MSVC std::vector<int> (begin at +0, end at +4, cap at +8)."""
        raw = self.read_bytes(addr, 12)
        if not raw:
            return None
        begin, end, cap = struct.unpack("<III", raw)
        if begin == 0 and end == 0:
            return []
        if end < begin or cap < end or (end - begin) % 4 != 0:
            return None
        count = (end - begin) // 4
        if count > max_elems:
            return None
        vec_bytes = self.read_bytes(begin, count * 4)
        if not vec_bytes:
            return None
        return list(struct.unpack(f"<{count}i", vec_bytes))

    def extract_session_events(self, target_session_id: int) -> Optional[Dict[str, Any]]:
        """
        Find the in-memory `THOR_NetMessageConfig` (`query = session_loaded`) ParamTree whose
        `data.sessionID == target_session_id` and return its settings + parsed event list.
        Event blobs carry card NAMES (e.g. `ARISTOTLE`, `PYRAMIDS`) per player slot, so no manual
        card-ID mapping is needed.
        """
        if not self.h_proc or not target_session_id:
            return None
        marker = b"\x0e\x00\x00\x00message_number"
        sid_bytes = struct.pack("<I", target_session_id)
        is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
        mbi = MEMORY_BASIC_INFORMATION64() if is_64 else MEMORY_BASIC_INFORMATION32()
        mbi_size = ctypes.sizeof(mbi)
        best: Optional[Dict[str, Any]] = None
        addr = 0x10000
        while addr < 0x7FFF0000:
            if not self.k32.VirtualQueryEx(self.h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size):
                break
            base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
            if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READWRITE) and size <= 64 * 1024 * 1024:
                chunk = self.read_bytes(base, size)
                if chunk and sid_bytes in chunk:
                    pos = 0
                    while True:
                        pos = chunk.find(marker, pos)
                        if pos == -1:
                            break
                        start = pos - 12  # [version=1][type=3 dict][count] precede first key
                        pos += 4
                        if start < 0 or b"session_loaded" not in chunk[pos : pos + 200]:
                            continue
                        try:
                            tree, _ = parse_param_tree_raw(chunk, start + 4)
                        except Exception:
                            tree = None
                        data = tree.get("data") if isinstance(tree, dict) else None
                        if not isinstance(data, dict) or data.get("sessionID") != target_session_id:
                            continue
                        # Locate the raw `events` array and scan its binary records directly
                        ev_key = chunk.find(b"\x06\x00\x00\x00events\x02\x00\x00\x00", start)
                        if ev_key == -1:
                            continue
                        ev_count = struct.unpack_from("<I", chunk, ev_key + 14)[0]
                        # Walk the array strictly: each item = [type=4][len][blob]; stop at the first
                        # corrupted item (several in-memory copies are partially overwritten).
                        blobs: List[bytes] = []
                        off = ev_key + 18
                        while len(blobs) < ev_count and off + 8 <= len(chunk):
                            typ, blen = struct.unpack_from("<II", chunk, off)
                            if typ != 4 or not (21 <= blen <= 4096) or off + 8 + blen > len(chunk):
                                break
                            blob = chunk[off + 8 : off + 8 + blen]
                            if struct.unpack_from("<I", blob, 13)[0] != target_session_id:
                                break
                            blobs.append(blob)
                            off += 8 + blen
                        events = parse_events_bytes(b"".join(blobs))
                        if best is None or len(events) > best["event_count"]:
                            settings = data.get("settings") or {}
                            settings = {
                                k: (v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v)
                                for k, v in settings.items()
                            } if isinstance(settings, dict) else {}
                            name = data.get("sessionName")
                            best = {
                                "sessionID": target_session_id,
                                "sessionName": name.decode("utf-8", errors="replace") if isinstance(name, bytes) else name,
                                "settings": settings,
                                "event_count": len(events),
                                "expected_event_count": ev_count,
                                "events": events,
                            }
            addr = base + size
        return best

    def scan_event_objects(self, rva_vtable: int) -> List[Dict[str, Any]]:
        """
        Find replayed TTA::BaseGameEvent objects of one class (e.g. `EventCancelPact`, vtable RVA 0x9fbbac).
        Layout (verified on TELUNIDUMA): +0x2c event id, +0x34 player slot, +0x3c userID, +0x40 timestamp (ms).
        """
        if not self.h_proc or not self.module_base:
            return []
        vt = struct.pack("<I", self.module_base + rva_vtable)
        is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
        mbi = MEMORY_BASIC_INFORMATION64() if is_64 else MEMORY_BASIC_INFORMATION32()
        mbi_size = ctypes.sizeof(mbi)
        out: List[Dict[str, Any]] = []
        addr = 0x10000
        while addr < 0x7FFF0000:
            if not self.k32.VirtualQueryEx(self.h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size):
                break
            base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
            if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READWRITE) and size <= 64 * 1024 * 1024:
                chunk = self.read_bytes(base, size)
                if chunk:
                    pos = 0
                    while True:
                        pos = chunk.find(vt, pos)
                        if pos == -1:
                            break
                        if pos % 4 == 0 and pos + 0x48 <= len(chunk):
                            eid, = struct.unpack_from("<I", chunk, pos + 0x2C)
                            slot, = struct.unpack_from("<i", chunk, pos + 0x34)
                            uid, = struct.unpack_from("<I", chunk, pos + 0x3C)
                            ts, = struct.unpack_from("<Q", chunk, pos + 0x40)
                            if 400002 <= eid <= 400059 and 0 <= slot <= 3 and 1500000000000 <= ts <= 2500000000000:
                                out.append({"addr": base + pos, "event_id": eid, "slot": slot, "user_id": uid, "ts": ts})
                        pos += 4
            addr = base + size
        return out

    def scan_gamestats_and_graphs(self, target_session_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Scan process heap for:
        1. `TTA::GameStats` (`vtable = module_base + 0x00a02de0`) -> all `TTA::GameStatsItem` lines
        2. `TTA::Player` (`vtable = module_base + 0x00a1c44c`) -> `(slot, login, userID, rank)`
        3. `User` (`vtable = module_base + 0x00a1c0f8`) -> `(userID -> login)`
        4. `TTA::PlayerGraphStats` (`vtable = module_base + 0x00a0aebc`)
        5. In-memory `.events` matching `target_session_id` -> `(slot -> userID)`
        """
        if not self.h_proc or not self.module_base:
            raise RuntimeError("Not attached to ThroughTheAges_steam.exe")

        vt_gamestats = struct.pack("<I", self.module_base + RVA_VTABLE_GAMESTATS)
        vt_item = self.module_base + RVA_VTABLE_GAMESTATS_ITEM
        vt_pgs = struct.pack("<I", self.module_base + RVA_VTABLE_PLAYER_GRAPHSTATS)
        vt_net_player = struct.pack("<I", self.module_base + RVA_VTABLE_NET_PLAYER)
        vt_user = struct.pack("<I", self.module_base + RVA_VTABLE_USER_PROFILE)
        session_id_bytes = (
            struct.pack("<I", target_session_id) if target_session_id else None
        )

        best_lines: List[str] = []
        best_gs_addr = 0
        pgs_list: List[Dict[str, Any]] = []
        net_players_by_slot: Dict[int, Dict[str, Any]] = {}
        uid_to_login: Dict[int, str] = dict(KNOWN_USER_ID_TO_LOGIN)
        session_slot_to_uid: Dict[int, int] = {}

        is_64bit_python = ctypes.sizeof(ctypes.c_void_p) == 8
        mbi = MEMORY_BASIC_INFORMATION64() if is_64bit_python else MEMORY_BASIC_INFORMATION32()
        mbi_size = ctypes.sizeof(mbi)

        addr = 0x10000
        while addr < 0x7FFF0000:
            res = self.k32.VirtualQueryEx(self.h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size)
            if not res:
                break
            base = int(mbi.BaseAddress)
            size = int(mbi.RegionSize)
            if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READWRITE) and size <= 64 * 1024 * 1024:
                chunk = self.read_bytes(base, size)
                if chunk:
                    # 1. Search for TTA::GameStats instances
                    pos = 0
                    while True:
                        pos = chunk.find(vt_gamestats, pos)
                        if pos == -1:
                            break
                        if pos + 20 <= len(chunk):
                            vec_begin, vec_end, vec_cap = struct.unpack_from("<III", chunk, pos + 8)
                            if (
                                vec_begin > 0x10000
                                and vec_end >= vec_begin
                                and vec_cap >= vec_end
                                and (vec_end - vec_begin) % 4 == 0
                            ):
                                count = (vec_end - vec_begin) // 4
                                if 0 < count <= 20000:
                                    ptrs_raw = self.read_bytes(vec_begin, count * 4)
                                    if ptrs_raw:
                                        ptrs = struct.unpack(f"<{count}I", ptrs_raw)
                                        lines = []
                                        for p in ptrs:
                                            item_vt = self.read_u32(p)
                                            if item_vt == vt_item:
                                                s = self.read_msvc_string(p + 8)
                                                if s is not None:
                                                    lines.append(s)
                                        if len(lines) > len(best_lines):
                                            best_lines = lines
                                            best_gs_addr = base + pos
                        pos += 4

                    # 2. Search for User profile objects (userID -> login)
                    pos = 0
                    while True:
                        pos = chunk.find(vt_user, pos)
                        if pos == -1:
                            break
                        if pos + 0xF0 <= len(chunk):
                            uid = struct.unpack_from("<I", chunk, pos + 0xE8)[0]
                            if 100 <= uid <= 10000000:
                                login = self.read_msvc_string(base + pos + 0x10, max_len=64)
                                if login and all(32 < ord(c) < 127 for c in login):
                                    uid_to_login[uid] = login
                        pos += 4

                    # 3. Search for TTA::Player (NetPlayer) instances (slot -> login, userID, rank)
                    pos = 0
                    while True:
                        pos = chunk.find(vt_net_player, pos)
                        if pos == -1:
                            break
                        if pos + 0x58 <= len(chunk):
                            slot = struct.unpack_from("<i", chunk, pos + 0x2C)[0]
                            uid = struct.unpack_from("<I", chunk, pos + 0x34)[0]
                            rank = struct.unpack_from("<i", chunk, pos + 0x50)[0]
                            if 0 <= slot <= 3 and 100 <= uid <= 10000000:
                                login = self.read_msvc_string(base + pos + 0x10, max_len=64)
                                if login and all(32 < ord(c) < 127 for c in login):
                                    uid_to_login[uid] = login
                                    net_players_by_slot[slot] = {
                                        "slot": slot,
                                        "login": login,
                                        "userID": uid,
                                        "rank": rank if 1 <= rank <= 4 else 0,
                                    }
                        pos += 4

                    # 4. If target_session_id is given, match in-memory event records for that sessionID
                    if session_id_bytes and len(session_slot_to_uid) < 4:
                        s_pos = 0
                        while len(session_slot_to_uid) < 4:
                            s_pos = chunk.find(session_id_bytes, s_pos)
                            if s_pos == -1 or s_pos + 26 > len(chunk):
                                break
                            eid1, eid2 = struct.unpack_from("<II", chunk, s_pos + 4)
                            if 400002 <= eid1 <= 400059 and eid1 == eid2:
                                slot1, uid = struct.unpack_from("<II", chunk, s_pos + 13)
                                if 0 <= slot1 <= 3 and 100 <= uid <= 10000000:
                                    session_slot_to_uid[slot1] = uid
                            s_pos += 4

                    # 5. Search for TTA::PlayerGraphStats instances
                    pos = 0
                    while True:
                        pos = chunk.find(vt_pgs, pos)
                        if pos == -1:
                            break
                        obj_addr = base + pos
                        login = self.read_msvc_string(obj_addr + 0x20, max_len=64)
                        if login:
                            scalars_raw = self.read_bytes(obj_addr + 0x58, 0x30)
                            if scalars_raw:
                                scalars = struct.unpack("<12i", scalars_raw)
                                pgs_list.append({
                                    "address": hex(obj_addr),
                                    "login": login,
                                    "total_culture": scalars[0],
                                })
                        pos += 4

            addr = base + size

        # Merge session_slot_to_uid with uid_to_login if available
        # Prefer the slot -> userID mapping taken from THIS session's event records: TTA::Player objects
        # of a previously watched replay may still linger in memory (batch mode).
        memory_players: Dict[int, str] = {}
        for slot, uid in session_slot_to_uid.items():
            if uid in uid_to_login:
                memory_players[slot] = uid_to_login[uid]
        if not memory_players:
            for slot, pinfo in net_players_by_slot.items():
                memory_players[slot] = pinfo["login"]

        return {
            "gamestats_lines": best_lines,
            "gamestats_addr": best_gs_addr,
            "player_graph_stats": pgs_list,
            "memory_players": memory_players,
            "net_players": net_players_by_slot,
        }


# ============================================================================
# 5. GameStats Log & Event Stream Analyzer (Computes All 27 Fun Stats + Rankings)
# ============================================================================

def parse_gamestats_lines(
    lines: List[str],
    info_data: Optional[Dict[str, Any]] = None,
    pgs_list: Optional[List[Dict[str, Any]]] = None,
    memory_players: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """
    Parse `TTA::GameStats` tagged lines (plus optional `.info` and `PlayerGraphStats`)
    into a comprehensive per-game structured JSON dictionary containing:
    - rankings (`r1`, `r2`, `r3`, `r4`) mapped to tournament player names
    - per-player detailed stats for all 27 Fun Awards & Personal Dashboard records
    - per-Age Leader & Wonder usage
    """
    slot_to_login: Dict[int, str] = {}
    slot_to_player: Dict[int, str] = {}

    if memory_players:
        for slot_id, login in memory_players.items():
            slot_to_login[int(slot_id)] = login
            slot_to_player[int(slot_id)] = map_login_to_player(login)

    if not slot_to_login and info_data:
        if "netPlayers" in info_data and info_data["netPlayers"]:
            for idx, p in enumerate(info_data["netPlayers"]):
                slot_id = p.get("slot", {}).get("id", idx) if isinstance(p.get("slot"), dict) else idx
                login = p.get("login") or p.get("name") or f"Player{slot_id}"
                slot_to_login[slot_id] = login
                slot_to_player[slot_id] = map_login_to_player(login)
        elif "players" in info_data and info_data["players"]:
            for idx, p in enumerate(info_data["players"]):
                login = p.get("name", f"Player{idx}") if isinstance(p, dict) else str(p)
                slot_to_login[idx] = login
                slot_to_player[idx] = map_login_to_player(login)

    # First pass on PLAYER lines if slot_to_login not populated
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 6 and parts[4] == "PLAYER":
            slot = int(parts[3])
            if slot not in slot_to_login:
                slot_to_login[slot] = f"Slot{slot}"
                slot_to_player[slot] = f"Slot{slot}"

    # Initialize per-player game stats structure
    def make_empty_pstat(slot: int) -> Dict[str, Any]:
        login = slot_to_login.get(slot, f"Slot{slot}")
        pname = slot_to_player.get(slot, login)
        return {
            "slot": slot,
            "login": login,
            "player": pname,
            "rank": 0,  # 1..4
            "final_culture": 0,
            "leaders_played": [],          # list of {"age": "A".."III", "card": card_id, "name_zh": str, "is_female": bool}
            "female_leaders_count": 0,
            "wonders_completed": [],       # list of {"age": "A".."III", "card": card_id, "name_zh": str, "turn": int, "points": int}
            "wonders_count": 0,
            "max_single_wonder_score": 0,
            "max_single_wonder_name": "",
            "wars_declared": 0,
            "wars_declared_on_me": 0,
            "aggressions_declared": 0,
            "aggressions_succeeded": 0,
            "aggressions_on_me": 0,
            "aggressions_succeeded_on_me": 0,
            "pacts_signed": 0,
            "pacts_broken": 0,
            "colonies_won": 0,
            "revolutions": 0,
            "peaceful_gov_changes": 0,
            "governments_used": ["DESPOTISM"],
            "uprisings": 0,
            "resigned": False,
            "events_played": 0,            # political-phase events played (`P_EVENT`)
            "three_ca_takes": 0,           # cards taken paying exactly 3 civil actions (`A_TAKE ... 3`)
            "aggressions_defended": 0,     # aggressions against me that were repelled
            "wars_won_attacking": 0,
            "wars_lost_attacking": 0,
            "wars_won_defending": 0,       # 反打: war declared on me, but I won
            "wars_lost_defending": 0,
            "war_demolitions": 0,          # farms / mines / urban buildings destroyed right after declaring war
            "total_food_net": 0,           # sum over production phases of (food produced - food consumed)
            "total_resource": 0,           # sum of resource production
            "total_science": 0,            # sum of science production
            "rats_food_lost": 0,
            "dark_ages_science_lost": 0,
            "max_war_culture_gained": 0,
            "max_war_culture_lost": 0,
            "max_impact_culture_gained": 0,
            "max_impact_card_name": "",
            "max_food_rate": 0,
            "max_resource_rate": 0,
            "max_science_rate": 0,
            "max_culture_rate": 0,
            "max_strength": 0,
            "max_ca": 4,
            "max_ma": 2,
            "max_food_stock": 0,
            "max_resource_stock": 0,
            "max_science_stock": 0,
            "max_urban_workers": 0,
            "urban_buildings_built": 0,
            "red_cross_points": 0,
            "culture_by_turn": {},         # round -> culture after the last player of that round ended
        }

    pstats: Dict[int, Dict[str, Any]] = {s: make_empty_pstat(s) for s in range(max(4, len(slot_to_player)))}
    max_turn_seen = 0
    pending_aggr: Optional[Tuple[int, int, str]] = None  # (attacker_slot, defender_slot, card_id)
    wonder_extra_points: Dict[Tuple[int, str], int] = defaultdict(int)
    # Per-turn working state: last ET_RATES / ET_STUFF of each slot (before production)
    turn_rates: Dict[int, Tuple[int, int]] = {}            # slot -> (science_rate, culture_rate)
    turn_stock: Dict[int, List[int]] = {}                   # slot -> [science, culture, food, resources]
    urban_workers: Dict[Tuple[int, int], int] = defaultdict(int)  # (slot, round) -> workers on urban buildings
    URBAN_TAGS = {"ET_LAB", "ET_TMPL", "ET_THTR", "ET_ARN", "ET_LIB"}
    resign_order: List[int] = []
    pending_wars: List[Dict[str, int]] = []           # declared but not yet resolved wars
    war_turn: Dict[int, Tuple[int, int]] = {}         # slot -> (round, turn) in which it declared a war
    DEMOLISH_TYPES = {"farm", "mine", "lab", "temple", "library", "theater", "arena"}
    WAR_RESULT_TAGS = {"WAR_CULT", "WAR_TER", "WAR_SCI", "WAR_HYB"}

    def _upd(ps_: Dict[str, Any], key: str, val: int, rnd: int) -> None:
        """Keep the single-game maximum of `key` and remember the round it happened (`<key>_round`)."""
        if val > ps_[key] or (key + "_round") not in ps_:
            if val >= ps_[key]:
                ps_[key] = val
                ps_[key + "_round"] = rnd
    pact_pairs: List[Tuple[str, int, int]] = []        # (pact card, slot a, slot b) of accepted pacts
    pact_break_slots: set = set()                       # slots involved in an attack-induced pact break

    for line in lines:
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            age_num = int(parts[0])
            round_num = int(parts[1])
            turn_num = int(parts[2])
            slot = int(parts[3])
        except ValueError:
            continue

        tag = parts[4]
        args = parts[5:]
        if turn_num > max_turn_seen:
            max_turn_seen = turn_num
        if slot >= 0 and slot not in pstats:
            pstats[slot] = make_empty_pstat(slot)

        # 0. Resignation (`P_RESIGN ...`): the resigning player is ranked below everyone still playing
        if tag == "P_RESIGN" and slot >= 0:
            pstats[slot]["resigned"] = True
            pstats[slot]["resigned_round"] = round_num
            resign_order.append(slot)
            continue

        # ---- v3 counters (independent of the handler chain below) ----
        if tag == "P_EVENT" and slot >= 0:
            pstats[slot]["events_played"] += 1
        elif tag == "A_TAKE" and len(args) >= 4 and slot >= 0 and args[3] == "3":
            # `A_TAKE <card> <row position> <base cost> <civil actions actually paid>`
            pstats[slot]["three_ca_takes"] += 1
        elif tag == "P_WAR" and len(args) >= 2 and slot >= 0 and args[1].lstrip("-").isdigit():
            pending_wars.append({"att": slot, "tgt": int(args[1])})
            war_turn[slot] = (round_num, turn_num)
        elif tag in WAR_RESULT_TAGS and len(args) >= 1 and slot >= 0 and args[0].lstrip("-").isdigit():
            # War resolution: the line's slot is the WINNER, args[0] the loser
            # (verified: GEZITELUMA R17 DHYellow declared at -7 strength and lost -> line slot = defender)
            loser = int(args[0])
            for w in pending_wars:
                if {w["att"], w["tgt"]} == {slot, loser}:
                    pending_wars.remove(w)
                    if slot == w["att"]:
                        pstats[w["att"]]["wars_won_attacking"] += 1
                        if w["tgt"] in pstats:
                            pstats[w["tgt"]]["wars_lost_defending"] += 1
                    else:
                        pstats[w["att"]]["wars_lost_attacking"] += 1
                        if w["tgt"] in pstats:
                            pstats[w["tgt"]]["wars_won_defending"] += 1
                    break
        elif tag == "RES_CANCELW" and len(args) >= 2 and args[1].lstrip("-").isdigit():
            # war against a resigning player is cancelled -> no result
            att = int(args[1])
            pending_wars[:] = [w for w in pending_wars if not (w["att"] == att and w["tgt"] == slot)]
        elif tag == "A_DST" and len(args) >= 1 and slot >= 0 and war_turn.get(slot) == (round_num, turn_num):
            info = load_card_db().get(int(args[0])) if args[0].isdigit() else None
            if info and info.get("type") in DEMOLISH_TYPES:
                pstats[slot]["war_demolitions"] += 1

        # 1. Final Ranking (`EOG_ORDER <rank_1_to_4> ... <final_culture>`, e.g. `5 19 3 0 EOG_ORDER 1 0 0 231`)
        if tag == "EOG_ORDER" and len(args) >= 2 and slot >= 0:
            rank_val = int(args[0])
            final_cult = int(args[-1])
            pstats[slot]["rank"] = rank_val if 1 <= rank_val <= 4 else rank_val + 1
            pstats[slot]["final_culture"] = final_cult

        # 2. End-of-turn Rates (`ET_RATES <science_rate> <culture_rate> <strength> <happy>`)
        #    Verified: round 2 start position = `ET_RATES 1 0 1 0` (1 science, 0 culture, 1 strength),
        #    and culture-per-turn (args[1]) matches the round-to-round culture increase.
        elif tag == "ET_RATES" and len(args) >= 3 and slot >= 0:
            s_rate, c_rate, strength = int(args[0]), int(args[1]), int(args[2])
            _upd(pstats[slot], "max_culture_rate", c_rate, round_num)
            _upd(pstats[slot], "max_science_rate", s_rate, round_num)
            _upd(pstats[slot], "max_strength", strength, round_num)
            turn_rates[slot] = (s_rate, c_rate)

        # 3. End-of-turn Stuff, logged BEFORE the production phase:
        #    `ET_STUFF <science_stock> <culture_total> <food_stock> <resource_stock>`
        #    Verified with the in-game log (bala R19: 0/135/3/1 + production -> 8 sci / 152 culture / 1 food / 7 res).
        elif tag == "ET_STUFF" and len(args) >= 4 and slot >= 0:
            sci, cult, food, res = (int(a) for a in args[:4])
            turn_stock[slot] = [sci, cult, food, res]
            _, c_rate = turn_rates.get(slot, (0, 0))
            # Culture at the end of this round = culture before production + culture production
            pstats[slot]["culture_by_turn"][round_num] = cult + c_rate
            ps = pstats[slot]
            _upd(ps, "max_food_stock", food, round_num)
            _upd(ps, "max_resource_stock", res, round_num)
            _upd(ps, "max_science_stock", sci, round_num)

        # 3b. Production phase (game-computed values incl. leader / wonder / pact bonuses):
        #     `PR_FOOD <production> <production> <consumption> <consumption> <?>`
        #     `PR_RES <production> <production>`, `PR_COR <?> <corruption> <corruption> <?>`
        elif tag == "PR_FOOD" and len(args) >= 3 and slot >= 0:
            prod, cons = int(args[0]), int(args[2])
            ps = pstats[slot]
            _upd(ps, "max_food_rate", prod, round_num)
            ps["total_food_net"] += prod - cons
            if slot in turn_stock:
                turn_stock[slot][2] += prod - cons
                _upd(ps, "max_food_stock", turn_stock[slot][2], round_num)
        elif tag == "PR_COR" and len(args) >= 3 and slot >= 0:
            if slot in turn_stock:
                turn_stock[slot][3] -= int(args[2])
        elif tag == "PR_RES" and len(args) >= 1 and slot >= 0:
            prod = int(args[0])
            ps = pstats[slot]
            _upd(ps, "max_resource_rate", prod, round_num)
            ps["total_resource"] += prod
            ps["total_science"] += turn_rates.get(slot, (0, 0))[0]
            if slot in turn_stock:
                turn_stock[slot][3] += int(args[1]) if len(args) >= 2 else prod
                _upd(ps, "max_resource_stock", turn_stock[slot][3], round_num)
                s_rate, _ = turn_rates.get(slot, (0, 0))
                _upd(ps, "max_science_stock", turn_stock[slot][0] + s_rate, round_num)

        # 3c. Workers on urban buildings at the same time (labs, temples, theaters, arenas, libraries)
        elif tag in URBAN_TAGS and slot >= 0:
            ukey = (slot, age_num, round_num)
            urban_workers[ukey] += sum(max(0, int(a)) for a in args[:4] if a.lstrip("-").isdigit())
            _upd(pstats[slot], "max_urban_workers", urban_workers[ukey], round_num)

        # 4. End-of-turn Actions (`ET_ACT <ca_rem> <ma_rem> <ca_total> <ma_total>`)
        elif tag == "ET_ACT" and len(args) >= 2 and slot >= 0:
            vals = [int(x) for x in args if x.lstrip("-").isdigit()]
            if len(vals) >= 4:
                ca_val, ma_val = max(vals[0], vals[2]), max(vals[1], vals[3])
            else:
                ca_val, ma_val = vals[0], vals[1]
            _upd(pstats[slot], "max_ca", ca_val, round_num)
            _upd(pstats[slot], "max_ma", ma_val, round_num)

        # 5. Leader Played (`A_LDR <leader_card_id> ...`)
        elif tag == "A_LDR" and len(args) >= 1 and slot >= 0:
            raw_card = resolve_card_token(args[0])
            norm_card = normalize_card_id(raw_card)
            c_age = card_age_label(raw_card) or CARD_TO_AGE.get(raw_card, CARD_TO_AGE.get(norm_card, ["A", "I", "II", "III"][min(age_num, 3)]))
            is_fem = norm_card in FEMALE_LEADERS or raw_card in FEMALE_LEADERS
            pstats[slot]["leaders_played"].append({
                "age": c_age,
                "card": norm_card,
                "name_zh": get_card_zh(norm_card),
                "is_female": is_fem,
            })
            if is_fem:
                pstats[slot]["female_leaders_count"] += 1

        # 6. Wonder Completed (`WND <wonder_card_id> <immediate_culture>`)
        elif tag == "WND" and len(args) >= 1 and slot >= 0:
            raw_card = resolve_card_token(args[0])
            norm_card = normalize_card_id(raw_card)
            imm_cult = int(args[1]) if len(args) >= 2 and args[1].lstrip("-").isdigit() else 0
            c_age = card_age_label(raw_card) or CARD_TO_AGE.get(raw_card, CARD_TO_AGE.get(norm_card, ["A", "I", "II", "III"][min(age_num, 3)]))
            pstats[slot]["wonders_completed"].append({
                "age": c_age,
                "card": norm_card,
                "name_zh": get_card_zh(norm_card),
                "round": round_num,
                "immediate_points": imm_cult,
                "points": imm_cult,
            })
            pstats[slot]["wonders_count"] += 1

        # Special Wonder Culture bonus tags
        elif tag == "LOUV_CULT" and len(args) >= 1 and slot >= 0:
            pts = int(args[0]) if args[0].lstrip("-").isdigit() else 0
            wonder_extra_points[(slot, "LOUVRE")] += pts
        elif tag == "STATL_CULT" and len(args) >= 1 and slot >= 0:
            pts = int(args[0]) if args[0].lstrip("-").isdigit() else 0
            wonder_extra_points[(slot, "STATUE_OF_LIBERTY")] += pts
        elif tag == "REDC_CULT" and len(args) >= 3 and slot >= 0:
            # `REDC_CULT <?> <steps_built> <culture>`: every player who helps build the Red Cross
            # scores 6 culture per step; the line's slot is the receiving player.
            pts = int(args[2]) if args[2].lstrip("-").isdigit() else 0
            wonder_extra_points[(slot, "RED_CROSS")] += pts
            pstats[slot]["red_cross_points"] += pts
            pstats[slot]["red_cross_round"] = round_num
        elif tag == "CLSSM_CULT" and len(args) >= 1 and slot >= 0:
            pts = int(args[-1]) if args[-1].lstrip("-").isdigit() else 0
            wonder_extra_points[(slot, "COLOSSEUM")] += pts

        # 7. Urban Building Built (`A_BLD` / `GUT_BLD`)
        elif tag in ("A_BLD", "GUT_BLD") and len(args) >= 1 and slot >= 0:
            raw_card = args[0]
            norm_card = normalize_card_id(raw_card)
            if norm_card in URBAN_BUILDINGS or raw_card in URBAN_BUILDINGS:
                pstats[slot]["urban_buildings_built"] += 1

        # 8. Government Changed (`A_REV` = violent revolution, `A_DEV` = peaceful change)
        elif tag == "A_REV" and len(args) >= 1 and slot >= 0:
            gov_card = normalize_card_id(args[0])
            pstats[slot]["revolutions"] += 1
            if gov_card in GOVERNMENTS:
                pstats[slot]["governments_used"].append(gov_card)
        elif tag == "A_DEV" and len(args) >= 1 and slot >= 0:
            gov_card = normalize_card_id(args[0])
            if gov_card in GOVERNMENTS:
                pstats[slot]["peaceful_gov_changes"] += 1
                pstats[slot]["governments_used"].append(gov_card)

        # 9. Wars Declared (`P_WAR` / `P_WAR_HYB`)
        # (`P_WAR_HYB` is a detail line of the same hybrid war card already logged by `P_WAR` -> not counted)
        elif tag == "P_WAR" and len(args) >= 2 and slot >= 0:
            opp_slot = int(args[1]) if args[1].lstrip("-").isdigit() else -1
            pstats[slot]["wars_declared"] += 1
            if opp_slot in pstats:
                pstats[opp_slot]["wars_declared_on_me"] += 1

        # 10. Aggressions Declared & Resolved (`P_AGGR`, `DEFEND`, `AGGR_*`)
        # `P_AGGR <card> <target_slot> ...` declares an aggression. Its outcome is the NEXT line:
        # an `AGGR_*` result line (success) or the defender's `DEFEND` line (repelled).
        # (Verified on BEGUMELUMA / KIHEBILUMA / ZACOCALUMA / TELUNIDUMA.)
        elif tag == "P_AGGR" and len(args) >= 2 and slot >= 0:
            opp_slot = int(args[1]) if args[1].lstrip("-").isdigit() else -1
            pstats[slot]["aggressions_declared"] += 1
            if opp_slot in pstats:
                pstats[opp_slot]["aggressions_on_me"] += 1
            pending_aggr = (slot, opp_slot, args[0])
        elif tag.startswith("AGGR_") and pending_aggr and slot == pending_aggr[0]:
            att_s, def_s, _ = pending_aggr
            pstats[att_s]["aggressions_succeeded"] += 1
            if def_s in pstats:
                pstats[def_s]["aggressions_succeeded_on_me"] += 1
            pending_aggr = None
        elif tag == "DEFEND" and pending_aggr:
            pending_aggr = None

        # 11. Pacts Signed & Broken (`P_PACT`, `PACT_BREAK`, `RES_CANCELP`)
        elif tag == "P_PACT" and len(args) >= 4 and slot >= 0:
            partner_slot = int(args[1]) if args[1].lstrip("-").isdigit() else -1
            accepted = int(args[3]) if args[3].lstrip("-").isdigit() else 0
            if accepted == 1:
                pact_pairs.append((args[0], slot, partner_slot))
                pstats[slot]["pacts_signed"] += 1
                if partner_slot in pstats:
                    pstats[partner_slot]["pacts_signed"] += 1
        # `RES_CANCELP` = pacts auto-cancelled because a player resigned -> passive, not counted as 毀約
        # `PACT_BREAK <pact> ...`: the player attacked (war / aggression) their own pact partner -> 毀約
        elif tag == "PACT_BREAK" and slot >= 0:
            pact_break_slots.add(slot)
            for card, a_, b_ in pact_pairs:
                if card == args[0] and slot in (a_, b_):
                    pact_break_slots.update((a_, b_))
            pstats[slot]["pacts_broken"] += 1

        # 12. Colonies Won (`COL_FWIN` / `COL_SWIN`; `ISA_COL` is Isabella's bonus on an already-won colony)
        elif tag in ("COL_FWIN", "COL_SWIN") and slot >= 0:
            pstats[slot]["colonies_won"] += 1

        # 13. Uprising (`UPRISING`)
        elif tag == "UPRISING" and slot >= 0:
            pstats[slot]["uprisings"] += 1

        # 14. Rats (`E1_RATS <flag> <food_lost>`) -> 老鼠王  (verified: R13 bala 2 / duck 3 / DH 5 / bob 13)
        elif tag == "E1_RATS" and len(args) >= 2 and slot >= 0:
            food_lost = abs(int(args[1])) if args[1].lstrip("-").isdigit() else 0
            pstats[slot]["rats_food_lost"] += food_lost
            if food_lost:
                pstats[slot]["rats_food_lost_round"] = round_num

        # 15. Dark Ages (`E1_DA <sci_lost> <sci_rem>`) -> 焚書王
        elif tag == "E1_DA" and len(args) >= 2 and slot >= 0:
            sci_lost = abs(int(args[1])) if args[1].lstrip("-").isdigit() else 0
            pstats[slot]["dark_ages_science_lost"] += sci_lost
            if sci_lost:
                pstats[slot]["dark_ages_science_lost_round"] = round_num

        # 16. War Over Culture (`WAR_CULT <loser_slot> <culture_won> <str_diff>`) -> 提款王 / 提款機王
        #     (format not yet verified against a real war — flagged for user confirmation)
        # `WAR_HYB` (綜合戰爭) also takes culture from the loser (verified: ZACOCALUMA R19 Neil -11)
        elif tag in ("WAR_CULT", "WAR_HYB") and len(args) >= 2 and slot >= 0:
            cult_won = int(args[1]) if args[1].lstrip("-").isdigit() else 0
            if cult_won > pstats[slot]["max_war_culture_gained"]:
                pstats[slot]["max_war_culture_gained"] = cult_won
                pstats[slot]["max_war_culture_gained_round"] = round_num
            loser = int(args[0]) if args[0].lstrip("-").isdigit() else -1
            if loser in pstats and loser != slot and cult_won > pstats[loser]["max_war_culture_lost"]:
                pstats[loser]["max_war_culture_lost"] = cult_won
                pstats[loser]["max_war_culture_lost_round"] = round_num

        # 17. Age III Impact Event (`E3_IMP <card_statId> <flag> <culture_gained>`), the line's slot is the
        #     receiving player (verified: bala 24 / duck 20 from the in-game log)
        elif tag == "E3_IMP" and len(args) >= 3 and slot >= 0:
            imp_card = args[0]
            cult_gained = int(args[2]) if args[2].lstrip("-").isdigit() else 0
            if cult_gained > pstats[slot]["max_impact_culture_gained"]:
                pstats[slot]["max_impact_culture_gained"] = cult_gained
                pstats[slot]["max_impact_culture_gained_round"] = round_num
                pstats[slot]["max_impact_card_name"] = get_card_zh(resolve_card_token(imp_card, military=True))

    # Wonder score: only Age III wonders score culture on completion (`WND <card> <culture>`);
    # Red Cross additionally gives 6 culture per built step to every participant.
    for slot, ps in pstats.items():
        for w in ps["wonders_completed"]:
            if w["age"] == "III":
                w["points"] = w["immediate_points"] + wonder_extra_points.get((slot, w["card"]), 0)
            else:
                w["points"] = 0
            if w["points"] > ps["max_single_wonder_score"]:
                ps["max_single_wonder_score"] = w["points"]
                ps["max_single_wonder_name"] = w["name_zh"]
                ps["max_single_wonder_score_round"] = w.get("round", 0)
        owns_red_cross = any(w["card"] == "RED_CROSS" for w in ps["wonders_completed"])
        if not owns_red_cross and ps["red_cross_points"] > ps["max_single_wonder_score"]:
            ps["max_single_wonder_score"] = ps["red_cross_points"]
            ps["max_single_wonder_name"] = get_card_zh("RED_CROSS")
            ps["max_single_wonder_score_round"] = ps.get("red_cross_round", 0)
        ps["urban_buildings_built"] = ps["max_urban_workers"]
        ps["aggressions_defended"] = max(0, ps["aggressions_on_me"] - ps["aggressions_succeeded_on_me"])

    # Ties: EOG_ORDER gives tied players the same rank (2nd arg = 1). Tournament rule: among tied
    # players, the one who acts LATER in turn order (higher slot) ranks higher.
    ranked = [s_ for s_ in sorted(pstats) if s_ < 4 and pstats[s_]["rank"] > 0 and s_ not in resign_order]
    ranked.sort(key=lambda s_: (pstats[s_]["rank"], -s_))
    for idx, s_ in enumerate(ranked):
        pstats[s_]["rank"] = idx + 1

    # Resigned players: EOG_ORDER gives them a duplicate rank (e.g. two players at rank 3) -> put them last
    if resign_order:
        active = [s_ for s_ in sorted(pstats) if s_ < 4 and pstats[s_]["login"] and s_ not in resign_order]
        active.sort(key=lambda s_: pstats[s_]["rank"] if pstats[s_]["rank"] > 0 else 99)
        ordered = active + list(reversed(resign_order))
        for idx, s_ in enumerate(ordered):
            pstats[s_]["rank"] = idx + 1

    # Determine rankings if not already set by EOG_ORDER
    active_slots = [s for s in sorted(pstats.keys()) if s < 4]
    if all(pstats[s]["rank"] == 0 for s in active_slots):
        sorted_by_cult = sorted(active_slots, key=lambda s: pstats[s]["final_culture"], reverse=True)
        for rk_idx, s in enumerate(sorted_by_cult):
            pstats[s]["rank"] = rk_idx + 1

    # Compute 5-turns-before-end Culture leader & trailer (被逆轉王 & 逆轉勝王)
    all_rounds = sorted({r for s in active_slots for r in pstats[s]["culture_by_turn"].keys()})
    target_round_minus_5 = all_rounds[-6] if len(all_rounds) >= 6 else (all_rounds[0] if all_rounds else None)

    for s in active_slots:
        ps = pstats[s]
        ps["was_leading_5_turns_before_end"] = False
        ps["was_choked"] = False       # Had highest culture 5 turns before end, lost game (rank != 1)
        ps["was_trailing_5_turns_before_end"] = False
        ps["was_comeback_win"] = False  # Had lowest culture 5 turns before end, won game (rank == 1)

    if target_round_minus_5 is not None:
        scores_m5 = {
            s: pstats[s]["culture_by_turn"].get(target_round_minus_5, 0)
            for s in active_slots
        }
        if scores_m5 and max(scores_m5.values()) > 0:
            max_m5 = max(scores_m5.values())
            min_m5 = min(scores_m5.values())
            for s in active_slots:
                if scores_m5[s] == max_m5:
                    pstats[s]["was_leading_5_turns_before_end"] = True
                    if pstats[s]["rank"] != 1:
                        pstats[s]["was_choked"] = True
                if scores_m5[s] == min_m5:
                    pstats[s]["was_trailing_5_turns_before_end"] = True
                    if pstats[s]["rank"] == 1:
                        pstats[s]["was_comeback_win"] = True

    rank_order = sorted(active_slots, key=lambda s: pstats[s]["rank"] if pstats[s]["rank"] > 0 else 99)
    rankings = [pstats[s]["player"] for s in rank_order]

    return {
        "rankings": rankings,
        "r1": rankings[0] if len(rankings) > 0 else "",
        "r2": rankings[1] if len(rankings) > 1 else "",
        "r3": rankings[2] if len(rankings) > 2 else "",
        "r4": rankings[3] if len(rankings) > 3 else "",
        "players": {pstats[s]["player"]: pstats[s] for s in active_slots},
        "pact_break_slots": sorted(pact_break_slots),
    }


def parse_events_fallback(
    events: List[Dict[str, Any]],
    info_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fallback parser that extracts Leader, Wonder, War, Aggression, Pact, Colony,
    Revolution, and Urban Building counts directly from `.events` + `.info` on disk
    even if `ThroughTheAges_steam.exe` is not running.
    """
    synthetic_lines: List[str] = []
    cur_age = 0
    cur_round = 1
    cur_turn = 1

    pending_aggr_attacker: Optional[Tuple[int, int, str]] = None
    pending_pact_offer: Optional[Tuple[int, int, str]] = None

    for ev in events:
        ename = ev["event_name"]
        slot = ev["player_slot"]
        strs = ev["strings"]

        if ename in ("EventEndTurn", "EventEndTurnWithSaladin"):
            cur_turn += 1
            cur_round = (cur_turn - 1) // 4 + 1
            cur_age = 0 if cur_round <= 2 else (1 if cur_round <= 8 else (2 if cur_round <= 14 else 3))
        elif ename == "EventPlayLeader" and strs:
            synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} A_LDR {strs[0]}")
        elif ename == "EventBuildWonder" and strs:
            # EventBuildWonder is emitted per step; count unique wonder completions in post-processing
            synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} STEP_WND {strs[0]}")
        elif ename in ("EventBuildStuff", "EventBuildGutenberg") and strs:
            synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} A_BLD {strs[0]}")
        elif ename in ("EventPlayTechnology", "EventPlayTechnologyJobs", "EventDevelopGutenberg") and strs:
            card = normalize_card_id(strs[0])
            if card in GOVERNMENTS:
                # Check payload byte after card string for revolution flag
                synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} A_DEV {card}")
        elif ename in ("EventPlayAggressionOrWar", "EventPlayAggressionWithSacrifice") and strs:
            card = strs[0]
            # Extract target player slot from payload after string
            phex = bytes.fromhex(ev["payload_hex"])
            s_bytes = card.encode("ascii")
            idx = phex.find(s_bytes)
            opp_slot = (slot + 1) % 4
            if idx != -1 and idx + len(s_bytes) + 4 <= len(phex):
                cand = struct.unpack_from("<I", phex, idx + len(s_bytes))[0]
                if 0 <= cand <= 3:
                    opp_slot = cand
            if card.startswith("WAR_"):
                synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} P_WAR {card} {opp_slot} 0 0 0")
            else:
                synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} P_AGGR {card} {opp_slot} 0 0 1 0")
                pending_aggr_attacker = (slot, opp_slot, card)
        elif ename in ("EventStealFoodAndResources", "EventAnex", "EventDestroyOpponentBuildings", "EventStealBlueTechs"):
            if pending_aggr_attacker:
                att_s, def_s, card = pending_aggr_attacker
                synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {def_s} P_AGGR {card} {att_s} 10 0 10 0")
                pending_aggr_attacker = None
        elif ename == "EventOfferPact" and strs:
            phex = bytes.fromhex(ev["payload_hex"])
            s_bytes = strs[0].encode("ascii")
            idx = phex.find(s_bytes)
            opp_slot = (slot + 1) % 4
            if idx != -1 and idx + len(s_bytes) + 4 <= len(phex):
                cand = struct.unpack_from("<I", phex, idx + len(s_bytes))[0]
                if 0 <= cand <= 3:
                    opp_slot = cand
            pending_pact_offer = (slot, opp_slot, strs[0])
        elif ename == "EventRespondPact" and pending_pact_offer:
            att_s, def_s, card = pending_pact_offer
            synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {def_s} P_PACT {card} {att_s} 0 1")
            pending_pact_offer = None
        elif ename == "EventCancelPact" and strs:
            synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} RES_CANCELP {strs[0]} 0 0")
        elif ename == "EventColonizeWithColumbus" and strs:
            synthetic_lines.append(f"{cur_age} {cur_round} {cur_turn} {slot} COL_FWIN {strs[0]} 0 0")

    # Convert STEP_WND (multi-stage building) into WND when a wonder reaches its final stage
    wonder_steps: Counter = Counter()
    final_lines: List[str] = []
    for line in synthetic_lines:
        parts = line.split()
        if parts[4] == "STEP_WND":
            slot = int(parts[3])
            wcard = parts[5]
            wonder_steps[(slot, wcard)] += 1
            if wonder_steps[(slot, wcard)] == 3:
                final_lines.append(f"{parts[0]} {parts[1]} {parts[2]} {slot} WND {wcard} 4")
        else:
            final_lines.append(line)

    return parse_gamestats_lines(final_lines, info_data=info_data)


# ============================================================================
# 6. CLI & Self-Test Entrypoint
# ============================================================================

def run_self_test():
    saves_dir = r"E:\steam\steamapps\common\Through the Ages\data_ram\profiles\GGLab\saves"
    info_path = os.path.join(saves_dir, "autosavemultiplayer_finished.info")
    events_path = os.path.join(saves_dir, "autosavemultiplayer_finished.events")

    print("=== [1/2] Testing .info + .events disk parser ===")
    info_data = parse_info_file(info_path)
    events = parse_events_file(events_path)
    print(f"Decrypted sessionID: {info_data.get('sessionID')}, numEvents: {len(events)}")
    disk_res = parse_events_fallback(events, info_data)
    for p, st in disk_res["players"].items():
        print(
            f"  Player {p} (login={st['login']}): "
            f"leaders={[x['name_zh'] for x in st['leaders_played']]}, "
            f"female={st['female_leaders_count']}, "
            f"wars={st['wars_declared']}, aggr={st['aggressions_declared']}"
        )

    print("\n=== [2/2] Testing Full TTA::GameStats Tag Parser (All 27 Metrics) ===")
    sample_lines = [
        "0 1 1 0 A_LDR BOUDICA 0",
        "0 1 2 1 A_LDR JULIUS_CAESAR 0",
        "0 1 3 2 A_LDR CLEOPATRA 0",
        "0 1 4 3 A_LDR HOMER 0",
        "0 2 5 0 WND PYRAMIDS 0",
        "1 4 13 0 A_LDR JOAN_OF_ARC 0",
        "1 5 17 0 A_REV MONARCHY DESPOTISM 0",
        "1 6 21 1 E1_RATS 5 2",
        "1 7 25 2 E1_DA 6 1",
        "2 10 37 1 P_WAR WAR_OVER_CULTURE 2 25 10 15",
        "2 11 41 1 WAR_CULT 2 15 15",
        "2 12 45 0 P_PACT TRADE_ROUTES_AGREEMENT 3 0 1",
        "2 13 49 3 PACT_BREAK TRADE_ROUTES_AGREEMENT 0 0 0",
        "2 14 53 0 COL_FWIN VAST_TERRITORY_1 5 0",
        "3 15 57 3 UPRISING 0 0 0 0",
        "3 16 61 0 WND HOLLYWOOD 22",
        "3 17 65 0 E3_IMP IMPACT_OF_WONDERS 0 16",
        "2 13 49 0 ET_STUFF 20 80 12 10",
        "2 13 50 1 ET_STUFF 18 110 15 14",
        "2 13 51 2 ET_STUFF 15 75 9 8",
        "2 13 52 3 ET_STUFF 10 60 8 7",
        "3 18 69 0 ET_RATES 38 22 45 8",
        "3 18 69 0 ET_STUFF 40 245 26 20",
        "3 18 69 0 ET_ACT 2 1 8 5",
        "3 18 70 1 ET_STUFF 35 230 20 18",
        "3 18 71 2 ET_STUFF 30 195 15 12",
        "3 18 72 3 ET_STUFF 25 180 14 11",
        "3 18 72 0 EOG_ORDER 0 245 0 0",
        "3 18 72 1 EOG_ORDER 1 230 0 0",
        "3 18 72 2 EOG_ORDER 2 195 0 0",
        "3 18 72 3 EOG_ORDER 3 180 0 0",
    ]
    full_res = parse_gamestats_lines(sample_lines, info_data=info_data)
    print(f"Rankings: {full_res['rankings']}")
    for p, st in full_res["players"].items():
        print(
            f"  {p} (#{st['rank']}, {st['final_culture']} pts): "
            f"fem={st['female_leaders_count']}/{len(st['leaders_played'])}, "
            f"max_wonder={st['max_single_wonder_score']} ({st['max_single_wonder_name']}), "
            f"rats={st['rats_food_lost']}, da={st['dark_ages_science_lost']}, "
            f"war_cult={st['max_war_culture_gained']}, imp={st['max_impact_culture_gained']}, "
            f"choked={st['was_choked']}"
        )


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_self_test()
    else:
        reader = TTAMemoryReader()
        if reader.attach():
            print(f"Attached to ThroughTheAges_steam.exe (PID={reader.pid}, Base=0x{reader.module_base:08x})")
            dump = reader.scan_gamestats_and_graphs()
            reader.close()
            print(f"Extracted {len(dump['gamestats_lines'])} GameStats lines and {len(dump['player_graph_stats'])} PlayerGraphStats objects.")
        else:
            print("ThroughTheAges_steam.exe is not currently running. Run with `--test` to test parser.")
