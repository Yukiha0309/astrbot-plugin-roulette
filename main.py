import json
import os
import random
import re
import time
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path


NORMAL_ITEMS = ["魔镜", "止痛片", "肘击", "改装工具", "束线带", "反转器", "测弹仪", "顺手牵羊", "怪味蘑菇"]
ISEKAI_ITEMS = ["凶吉签", "绷带", "酒狐委托", "封膛符", "锈蚀枪管", "加压弹簧", "隙间之手"]
SPECIAL_ITEMS = ["梓的不死图腾", "星之加护", "替罪签"]
NORMAL_ITEM_ALIASES = {
    "放大镜": "魔镜",
    "香烟": "止痛片",
    "啤酒": "肘击",
    "手铐": "束线带",
    "短刀": "改装工具",
    "手锯": "改装工具",
    "逆转器": "反转器",
    "一次性手机": "测弹仪",
    "手机": "测弹仪",
    "肾上腺素": "顺手牵羊",
    "过期药": "怪味蘑菇",
}
MODE_NORMAL = "normal"
MODE_ISEKAI = "isekai"


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取简单的轮盘赌数据失败: {e}")
        return default


def save_json(path: str, data: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存简单的轮盘赌数据失败: {e}")


def normalize_ids(values: Any) -> set[str]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(v).strip() for v in values if str(v).strip()}


def strip_command(text: str, command_names: list[str]) -> str:
    raw = (text or "").strip()
    for name in command_names:
        for prefix in ("/", "!", ""):
            token = f"{prefix}{name}"
            if raw == token:
                return ""
            if raw.startswith(token + " "):
                return raw[len(token):].strip()
    return raw


def extract_target_id(event: AstrMessageEvent, fallback_text: str = "") -> str | None:
    self_id = str(event.get_self_id())
    for component in getattr(event.message_obj, "message", []):
        if isinstance(component, Comp.At) and str(component.qq) != self_id:
            return str(component.qq)

    text = fallback_text or str(getattr(event, "message_str", "") or "")
    for marker in ("qq=", "@"):
        rest = text
        while marker in rest:
            after = rest.split(marker, 1)[1]
            digits = ""
            for ch in after:
                if ch.isdigit():
                    digits += ch
                elif digits:
                    break
            if digits and digits != self_id:
                return digits
            rest = after
    return None


class RoulettePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = os.path.join(get_astrbot_plugin_data_path(), "roulette_game")
        self.rooms_file = os.path.join(self.data_dir, "rooms.json")
        self.death_stats_file = os.path.join(self.data_dir, "death_stats.json")
        self.rooms: dict[str, dict] = load_json(self.rooms_file, {})
        self.death_stats: dict[str, dict] = load_json(self.death_stats_file, {})
        logger.info(f"简单的轮盘赌插件已加载，数据目录: {self.data_dir}")

    def _save(self) -> None:
        save_json(self.rooms_file, self.rooms)

    def _save_death_stats(self) -> None:
        save_json(self.death_stats_file, self.death_stats)

    def _is_group_allowed(self, group_id: str) -> bool:
        whitelist = normalize_ids(self.config.get("whitelist_groups", []))
        blacklist = normalize_ids(self.config.get("blacklist_groups", []))
        if group_id in blacklist:
            return False
        return not whitelist or group_id in whitelist

    def _super_admins(self) -> set[str]:
        return normalize_ids(self.config.get("super_admins", []))

    def _is_manager(self, room: dict, user_id: str) -> bool:
        return user_id == str(room.get("owner_id")) or user_id in self._super_admins()

    def _is_iseikai(self, room: dict) -> bool:
        return room.get("mode") == MODE_ISEKAI

    def _short_name(self, name: str) -> str:
        limit = int(self.config.get("player_name_max_length", 8) or 8)
        limit = max(4, min(20, limit))
        name = str(name or "")
        return name if len(name) <= limit else name[:limit] + "..."

    def _jiuhu_event_enabled(self) -> bool:
        return bool(self.config.get("jiuhu_event_enabled", True))

    def _jiuhu_event_chance(self) -> int:
        try:
            raw = int(self.config.get("jiuhu_event_chance", 30) or 30)
        except Exception:
            raw = 30
        return max(0, min(100, raw))

    def _max_players(self) -> int:
        try:
            raw = int(self.config.get("max_players", 6) or 6)
        except Exception:
            raw = 6
        return max(2, raw)

    def _group_id_or_reply(self, event: AstrMessageEvent) -> tuple[str | None, str | None]:
        if event.is_private_chat():
            return None, "此游戏仅支持群聊。"
        group_id = str(event.get_group_id())
        if not self._is_group_allowed(group_id):
            return None, None
        return group_id, None

    def _player_profile(self, count: int) -> dict:
        if count == 2:
            return {
                "hp": 5,
                "item_start_round": 3,
                "item_count": 2,
                "max_items": 6,
                "early_rounds": 2,
                "early_max_bullets": 4,
            }
        if count <= 4:
            return {
                "hp": 3,
                "item_start_round": 3,
                "item_count": 2,
                "max_items": 4,
                "early_rounds": 3,
                "early_max_bullets": 6,
            }
        return {
            "hp": 2,
            "item_start_round": 2,
            "item_count": 1,
            "max_items": 3,
            "early_rounds": 0,
            "early_max_bullets": 10,
        }

    def _isekai_player_profile(self, count: int) -> dict:
        if count == 2:
            return {
                "hp": 5,
                "item_start_round": 3,
                "item_count_min": 2,
                "item_count_max": 4,
                "max_items": 8,
                "early_rounds": 3,
                "early_max_bullets": 4,
            }
        if count <= 4:
            return {
                "hp": 4,
                "item_start_round": 2,
                "item_count_min": 2,
                "item_count_max": 3,
                "max_items": 6,
                "early_rounds": 2,
                "early_max_bullets": 5,
            }
        if count <= 6:
            return {
                "hp": 3,
                "item_start_round": 1,
                "item_count_min": 1,
                "item_count_max": 2,
                "max_items": 4,
                "early_rounds": 2,
                "early_max_bullets": 7,
            }
        return {
            "hp": 2,
            "item_start_round": 1,
            "item_count_min": 1,
            "item_count_max": 1,
            "max_items": 3,
            "early_rounds": 0,
            "early_max_bullets": 10,
        }

    def _new_player(self, event: AstrMessageEvent) -> dict:
        user_id = str(event.get_sender_id())
        name = event.get_sender_name() or f"玩家{user_id}"
        return {
            "id": user_id,
            "name": name,
            "hp": 1,
            "max_hp": 1,
            "items": [],
            "alive": True,
            "skipped": False,
            "damage_bonus": 0,
            "code": "",
            "bleeding": False,
            "sealed": False,
            "pressure": False,
            "rusty_barrel": False,
            "special_item": "",
            "pending_special_item": "",
        }

    def _alive_ids(self, room: dict) -> list[str]:
        players = room["players"]
        return [pid for pid in players if room["player_map"][pid].get("alive")]

    def _player_name(self, room: dict, user_id: str) -> str:
        player = room["player_map"].get(str(user_id))
        if not player:
            return f"玩家{user_id}"
        if bool(self.config.get("use_player_codes", True)) and player.get("code"):
            return str(player["code"])
        return self._short_name(player.get("name", f"玩家{user_id}"))

    def _turn_line(self, room: dict, user_id: str) -> str:
        return f"__TURN_AT__{user_id}\x1f{self._player_name(room, user_id)}"

    def _at_line(self, user_id: str, text: str) -> str:
        return f"__AT_LINE__{user_id}\x1f{text}"

    def _lines_result(self, event: AstrMessageEvent, lines: list[str]):
        chain = []
        for index, line in enumerate(lines):
            if index:
                chain.append(Comp.Plain("\n"))
            if isinstance(line, str) and line.startswith("__TURN_AT__"):
                payload = line.removeprefix("__TURN_AT__")
                user_id, name = payload.split("\x1f", 1)
                chain.append(Comp.Plain("轮到 "))
                chain.append(Comp.At(qq=user_id))
                chain.append(Comp.Plain(f" {name} 行动。"))
            elif isinstance(line, str) and line.startswith("__AT_LINE__"):
                payload = line.removeprefix("__AT_LINE__")
                user_id, text = payload.split("\x1f", 1)
                chain.append(Comp.At(qq=user_id))
                chain.append(Comp.Plain(f" {text}"))
            else:
                chain.append(Comp.Plain(str(line)))
        return event.chain_result(chain)

    def _record_death(self, group_id: str, player: dict) -> None:
        group_stats = self.death_stats.setdefault(str(group_id), {})
        user_id = str(player["id"])
        record = group_stats.setdefault(user_id, {"name": player["name"], "count": 0})
        record["name"] = player["name"]
        record["count"] = int(record.get("count", 0)) + 1
        self._save_death_stats()

    def _current_id(self, room: dict) -> str | None:
        alive = self._alive_ids(room)
        if not alive:
            return None
        players = room["players"]
        idx = int(room.get("turn_index", 0)) % len(players)
        for offset in range(len(players)):
            pid = players[(idx + offset) % len(players)]
            if pid in alive:
                room["turn_index"] = (idx + offset) % len(players)
                return pid
        return None

    def _refill_item_bag(self, room: dict) -> None:
        bag = (ISEKAI_ITEMS if self._is_iseikai(room) else NORMAL_ITEMS)[:]
        random.shuffle(bag)
        room["item_bag"] = bag

    def _draw_item(self, room: dict) -> str:
        if not room.get("item_bag"):
            self._refill_item_bag(room)
        return room["item_bag"].pop()

    def _offer_special_item(self, room: dict, player_id: str, item: str, lines: list[str]) -> None:
        player = room["player_map"][player_id]
        if not player.get("special_item"):
            player["special_item"] = item
            lines.append(f"- {self._player_name(room, player_id)} 获得特殊道具：{item}")
            return

        player["pending_special_item"] = item
        lines.append(
            self._at_line(
                player_id,
                f"你抽到了特殊道具：{item}\n当前特殊道具：{player['special_item']}\n输入 /替换特殊道具 或 /放弃特殊道具",
            )
        )

    def _grant_start_special_items(self, room: dict) -> list[str]:
        alive = self._alive_ids(room)
        if not alive:
            return []
        count = min(len(alive), random.randint(2, 4))
        chosen = random.sample(alive, count)
        lines = ["异界战争开局特殊补给："]
        for pid in chosen:
            self._offer_special_item(room, pid, "梓的不死图腾", lines)
        return lines

    def _maybe_grant_special_item(self, room: dict, lines: list[str]) -> None:
        if not self._is_iseikai(room):
            return
        if random.randint(1, 100) > 20:
            return
        alive = self._alive_ids(room)
        if not alive:
            return
        item_pool = SPECIAL_ITEMS[:]
        item = random.choice(item_pool)
        pid = random.choice(alive)
        lines.append("异界特殊补给出现。")
        self._offer_special_item(room, pid, item, lines)

    def _maybe_trigger_jiuhu_event(self, room: dict) -> list[str]:
        if not self._jiuhu_event_enabled():
            return []
        if random.randint(1, 100) > self._jiuhu_event_chance():
            return []
        alive = self._alive_ids(room)
        if not alive:
            return []

        event_name = random.choice(
            ["酒狐打工中", "狐火乱流", "拔刀剑支援", "魔法屏障", "异界搬运", "酒狐摸鱼"]
        )
        lines = [f"酒狐事件：{event_name}"]

        if event_name == "酒狐打工中":
            pid = random.choice(alive)
            item = self._draw_item(room)
            room["player_map"][pid]["items"].append(item)
            lines.append(f"{self._player_name(room, pid)} 获得普通道具：{item}")

        elif event_name == "狐火乱流":
            bullet = random.choice([True, False])
            chamber = room.setdefault("chamber", [])
            insert_at = random.randint(0, len(chamber))
            chamber.insert(insert_at, bullet)
            lines.append(f"狐火扰动弹仓，额外加入 1 发{'实弹' if bullet else '空弹'}。")

        elif event_name == "拔刀剑支援":
            pid = random.choice(alive)
            room["player_map"][pid]["pressure"] = True
            lines.append(f"{self._player_name(room, pid)} 获得一次加压效果。")

        elif event_name == "魔法屏障":
            pid = random.choice(alive)
            self._offer_special_item(room, pid, "星之加护", lines)

        elif event_name == "异界搬运":
            candidates = [pid for pid in alive if room["player_map"][pid].get("items")]
            if len(candidates) >= 2:
                a, b = random.sample(candidates, 2)
                room["player_map"][a]["items"], room["player_map"][b]["items"] = (
                    room["player_map"][b]["items"],
                    room["player_map"][a]["items"],
                )
                lines.append(f"{self._player_name(room, a)} 与 {self._player_name(room, b)} 的普通道具被交换。")
            else:
                lines.append("酒狐看了看大家的背包，发现没什么好搬的。")

        else:
            lines.append("酒狐摸鱼了，什么都没有发生。")

        return lines

    def _reload_chamber(self, room: dict) -> list[str]:
        room["round_no"] = int(room.get("round_no", 0)) + 1
        profile = room["rules"]
        max_bullets = 10
        if room["round_no"] <= profile["early_rounds"]:
            max_bullets = profile["early_max_bullets"]

        total = random.randint(3, max_bullets)
        live_count = random.randint(1, total - 1)
        chamber = [True] * live_count + [False] * (total - live_count)
        random.shuffle(chamber)

        room["chamber"] = chamber
        room["known_live"] = live_count
        room["known_blank"] = total - live_count

        lines = [
            f"第 {room['round_no']} 个弹仓轮开始。",
            f"本轮装填 {total} 发：实弹 {live_count} 发，空弹 {total - live_count} 发。",
        ]

        if room["round_no"] >= profile["item_start_round"]:
            lines.extend(self._deal_items(room))
        self._maybe_grant_special_item(room, lines)
        if self._is_iseikai(room):
            lines.extend(self._maybe_trigger_jiuhu_event(room))
        return lines

    def _deal_items(self, room: dict) -> list[str]:
        profile = room["rules"]
        lines = ["开始发放道具："]
        any_dealt = False
        for pid in self._alive_ids(room):
            player = room["player_map"][pid]
            gained = []
            item_count = random.randint(
                int(profile.get("item_count_min", profile.get("item_count", 1))),
                int(profile.get("item_count_max", profile.get("item_count", 1))),
            )
            for _ in range(item_count):
                if len(player["items"]) >= profile["max_items"]:
                    break
                item = self._draw_item(room)
                player["items"].append(item)
                gained.append(item)
            if gained:
                any_dealt = True
                lines.append(f"- {self._player_name(room, pid)} 获得：{'、'.join(gained)}")
            else:
                lines.append(f"- {self._player_name(room, pid)} 背包已满，未获得道具")
        return lines if any_dealt else ["所有存活玩家背包已满，本轮不发放道具。"]

    def _advance_turn(self, room: dict) -> list[str]:
        lines = []
        alive = self._alive_ids(room)
        if len(alive) <= 1:
            return lines

        players = room["players"]
        current_index = int(room.get("turn_index", 0)) % len(players)
        for step in range(1, len(players) + 1):
            idx = (current_index + step) % len(players)
            pid = players[idx]
            player = room["player_map"][pid]
            if not player.get("alive"):
                continue
            if player.get("skipped"):
                player["skipped"] = False
                player["damage_bonus"] = 0
                lines.append(f"{self._player_name(room, pid)} 被跳过本回合，无法行动。")
                continue
            room["turn_index"] = idx
            lines.append(self._turn_line(room, pid))
            return lines
        return lines

    def _finish_if_needed(self, group_id: str, room: dict) -> list[str]:
        alive = self._alive_ids(room)
        if len(alive) == 1:
            winner = self._player_name(room, alive[0])
            self.rooms.pop(group_id, None)
            self._save()
            return [f"游戏结束，胜者是：{winner}。"]
        if len(alive) == 0:
            self.rooms.pop(group_id, None)
            self._save()
            return ["游戏结束，无人生还。"]
        return []

    def _apply_damage_to_player(
        self, group_id: str, room: dict, target_id: str, damage: int, lines: list[str], *, reason: str
    ) -> None:
        target = room["player_map"][target_id]
        if damage > 1 and target.get("special_item") == "星之加护":
            target["special_item"] = ""
            lines.append(f"{self._player_name(room, target_id)} 的星之加护抵消了额外伤害。")
            damage = 1

        target["hp"] -= damage
        lines.append(f"{self._player_name(room, target_id)} {reason}，受到 {damage} 点伤害。")
        if target["hp"] <= 0:
            if target.get("special_item") == "梓的不死图腾":
                target["special_item"] = ""
                target["hp"] = 1
                target["bleeding"] = False
                lines.append(f"{self._player_name(room, target_id)} 的梓的不死图腾触发，保留 1 血并解除流血。")
                return
            target["hp"] = 0
            target["alive"] = False
            target["skipped"] = False
            target["sealed"] = False
            target["damage_bonus"] = 0
            target["pressure"] = False
            target["rusty_barrel"] = False
            self._record_death(group_id, target)
            lines.append(f"{self._player_name(room, target_id)} 出局。")
        else:
            lines.append(f"{self._player_name(room, target_id)} 剩余生命：{target['hp']}/{target['max_hp']}。")

    def _apply_end_of_action_effects(self, group_id: str, room: dict, user_id: str, lines: list[str]) -> None:
        player = room["player_map"].get(user_id)
        if not player or not player.get("alive"):
            return
        if player.get("bleeding"):
            self._apply_damage_to_player(group_id, room, user_id, 1, lines, reason="流血发作")

    def _ensure_playing_turn(self, event: AstrMessageEvent, room: dict) -> str | None:
        if room.get("status") != "playing":
            return "游戏还没有开始。"
        current_id = self._current_id(room)
        user_id = str(event.get_sender_id())
        if current_id != user_id:
            return self._turn_line(room, current_id)
        return None

    def _consume_item(self, player: dict, item: str) -> bool:
        if item not in player["items"]:
            return False
        player["items"].remove(item)
        return True

    def _status_text(self, room: dict) -> str:
        lines = [
            f"简单的轮盘赌：{room['status']}",
            f"房主：{self._player_name(room, room['owner_id'])}",
        ]
        if room.get("status") == "playing":
            current = self._current_id(room)
            lines.append(f"当前行动：{self._player_name(room, current)}")
            lines.append(f"弹仓轮：{room.get('round_no', 0)}")
        lines.append("玩家：")
        for pid in room["players"]:
            player = room["player_map"][pid]
            state = "存活" if player.get("alive") else "出局"
            skip = "，跳过待触发" if player.get("skipped") else ""
            bonus = "，短刀已准备" if player.get("damage_bonus") else ""
            extra = []
            if player.get("bleeding"):
                extra.append("流血")
            if player.get("sealed"):
                extra.append("封膛")
            if player.get("pressure"):
                extra.append("加压")
            if player.get("rusty_barrel"):
                extra.append("锈蚀枪管")
            if player.get("special_item"):
                extra.append(f"特殊:{player['special_item']}")
            extra_text = "，" + "，".join(extra) if extra else ""
            lines.append(f"- {self._player_name(room, pid)}：{player['hp']}/{player['max_hp']} 血，{state}{skip}{bonus}{extra_text}")
        return "\n".join(lines)

    @filter.command("轮盘创建", alias={"轮盘创建", "创建轮盘", "drcreate"})
    async def create_room(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if room:
            yield event.plain_result("本群已经有轮盘赌房间了。")
            return

        args = strip_command(event.message_str, ["轮盘创建", "创建轮盘", "drcreate"])
        mode = MODE_ISEKAI if "异界战争" in args else MODE_NORMAL
        owner = self._new_player(event)
        self.rooms[group_id] = {
            "group_id": group_id,
            "owner_id": owner["id"],
            "mode": mode,
            "status": "waiting",
            "created_at": int(time.time()),
            "players": [owner["id"]],
            "player_map": {owner["id"]: owner},
            "turn_index": 0,
            "round_no": 0,
            "chamber": [],
            "item_bag": [],
            "rules": {},
        }
        self._save()
        mode_text = "异界战争" if mode == MODE_ISEKAI else "普通"
        yield event.plain_result(
            f"{owner['name']} 创建了轮盘赌房间。\n模式：{mode_text}\n"
            "发送 /轮盘加入 加入游戏，人数满足后由房主发送 /轮盘开始。"
        )

    @filter.command("轮盘加入", alias={"加入轮盘", "drjoin"})
    async def join_room(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群还没有房间，请先发送 /轮盘创建。")
            return
        if room.get("status") != "waiting":
            yield event.plain_result("游戏已经开始，不能中途加入。")
            return

        user_id = str(event.get_sender_id())
        if user_id in room["player_map"]:
            yield event.plain_result("你已经在房间里了。")
            return

        max_players = self._max_players()
        if len(room["players"]) >= max_players:
            yield event.plain_result(f"房间已满，最多 {max_players} 人。")
            return

        player = self._new_player(event)
        room["players"].append(player["id"])
        room["player_map"][player["id"]] = player
        self._save()
        yield event.plain_result(
            f"{player['name']} 加入了房间。\n当前人数：{len(room['players'])}/{max_players}"
        )

    @filter.command("退出房间", alias={"轮盘退出", "退出轮盘", "drleave"})
    async def leave_room(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        if room.get("status") != "waiting":
            yield event.plain_result("游戏已经开始，不能退出房间。")
            return

        user_id = str(event.get_sender_id())
        if user_id not in room["player_map"]:
            yield event.plain_result("你不在当前房间里。")
            return

        name = room["player_map"][user_id].get("name", f"玩家{user_id}")
        room["players"] = [pid for pid in room["players"] if pid != user_id]
        room["player_map"].pop(user_id, None)
        if not room["players"]:
            self.rooms.pop(group_id, None)
            self._save()
            yield event.plain_result(f"{name} 退出了房间，房间已自动解散。")
            return

        if str(room.get("owner_id")) == user_id:
            room["owner_id"] = room["players"][0]
            self._save()
            yield event.plain_result(
                f"{name} 退出了房间。\n房主已转移给：{self._player_name(room, room['owner_id'])}"
            )
            return

        self._save()
        yield event.plain_result(f"{name} 退出了房间。当前人数：{len(room['players'])}")

    @filter.command("轮盘开始", alias={"开始轮盘", "drstart"})
    async def start_room(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群还没有房间。")
            return
        user_id = str(event.get_sender_id())
        if not self._is_manager(room, user_id):
            yield event.plain_result("只有房主或超级管理员可以开始游戏。")
            return
        if room.get("status") != "waiting":
            yield event.plain_result("游戏已经开始。")
            return
        count = len(room["players"])
        if count < 2:
            yield event.plain_result("至少需要 2 名玩家才能开始。")
            return
        max_players = self._max_players()
        if count > max_players:
            yield event.plain_result(f"当前配置最多支持 {max_players} 名玩家。")
            return

        profile = self._isekai_player_profile(count) if self._is_iseikai(room) else self._player_profile(count)
        room["rules"] = profile
        for player in room["player_map"].values():
            player["hp"] = profile["hp"]
            player["max_hp"] = profile["hp"]
            player["alive"] = True
            player["items"] = []
            player["skipped"] = False
            player["damage_bonus"] = 0
            player["bleeding"] = False
            player["sealed"] = False
            player["pressure"] = False
            player["rusty_barrel"] = False
            player["special_item"] = ""
            player["pending_special_item"] = ""

        random.shuffle(room["players"])
        for index, pid in enumerate(room["players"], 1):
            room["player_map"][pid]["code"] = f"P{index}"
        room["turn_index"] = 0
        room["status"] = "playing"
        self._refill_item_bag(room)
        lines = [
            f"游戏开始，共 {count} 名玩家。",
            f"模式：{'异界战争' if self._is_iseikai(room) else '普通'}",
            f"本局每人 {profile['hp']} 血，最多持有 {profile['max_items']} 个道具。",
            "",
            "玩家代号：",
        ]
        lines.extend(
            f"{self._player_name(room, pid)}={self._short_name(room['player_map'][pid]['name'])}"
            for pid in room["players"]
        )
        lines.extend([
            "",
            "行动顺序：",
            " -> ".join(self._player_name(room, pid) for pid in room["players"]),
            "",
        ])
        if self._is_iseikai(room) and count >= 5:
            lines.extend(self._grant_start_special_items(room))
        lines.extend(self._reload_chamber(room))
        lines.append(self._turn_line(room, self._current_id(room)))
        self._save()
        yield self._lines_result(event, lines)

    @filter.command("开自己", alias={"轮盘开自己", "drself"})
    async def shoot_self(self, event: AstrMessageEvent):
        async for result in self._shoot(event, target_self=True):
            yield result

    @filter.command("开", alias={"开枪", "轮盘开枪", "drshoot"})
    async def shoot_target(self, event: AstrMessageEvent):
        async for result in self._shoot(event, target_self=False):
            yield result

    @filter.command("听天由命", alias={"命运开火", "fatefire"})
    async def fate_fire(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有进行中的房间。")
            return
        turn_error = self._ensure_playing_turn(event, room)
        if turn_error:
            yield self._lines_result(event, [turn_error])
            return

        alive = self._alive_ids(room)
        if len(alive) <= 2:
            yield event.plain_result("听天由命仅在 3 名及以上存活玩家时可用。")
            return

        shooter_id = str(event.get_sender_id())
        target_id = random.choice(alive)
        lines = [f"{self._player_name(room, shooter_id)} 选择听天由命。"]
        if room["player_map"][target_id].get("special_item") == "替罪签":
            room["player_map"][target_id]["special_item"] = ""
            lines.append(f"{self._player_name(room, target_id)} 的替罪签触发，命运重新选择目标。")
            target_id = random.choice(alive)
        lines.append(f"命运指向：{self._player_name(room, target_id)}。")

        if not room.get("chamber"):
            lines.extend(self._reload_chamber(room))

        shooter = room["player_map"][shooter_id]
        target = room["player_map"][target_id]
        if shooter.get("sealed"):
            shooter["sealed"] = False
            lines.append(f"{self._player_name(room, shooter_id)} 被封膛符限制，无法真正开枪，行动结束。")
            self._apply_end_of_action_effects(group_id, room, shooter_id, lines)
            finish_lines = self._finish_if_needed(group_id, room)
            if finish_lines:
                lines.extend(finish_lines)
            else:
                lines.extend(self._advance_turn(room))
            self._save()
            yield self._lines_result(event, lines)
            return

        bullet = room["chamber"].pop(0)
        base_bonus = int(shooter.get("damage_bonus", 0))
        pressure_bonus = 1 if shooter.get("pressure") else 0
        rusty_ready = bool(shooter.get("rusty_barrel"))
        damage = 1 + base_bonus + pressure_bonus
        shooter["damage_bonus"] = 0
        shooter["pressure"] = False
        shooter["rusty_barrel"] = False

        if bullet:
            lines.append(
                f"{self._player_name(room, shooter_id)} 对 {self._player_name(room, target_id)} 开枪：实弹，造成 {damage} 点伤害。"
            )
            self._apply_damage_to_player(group_id, room, target_id, damage, lines, reason="被实弹命中")
            if rusty_ready and target.get("alive"):
                if target.get("bleeding"):
                    lines.append(f"{self._player_name(room, target_id)} 已经处于流血状态。")
                else:
                    target["bleeding"] = True
                    lines.append(f"{self._player_name(room, target_id)} 被锈蚀枪管附加流血。")
        else:
            lines.append(
                f"{self._player_name(room, shooter_id)} 对 {self._player_name(room, target_id)} 开枪：空弹。"
            )
            if pressure_bonus:
                lines.append("加压弹簧炸膛。")
                self._apply_damage_to_player(group_id, room, shooter_id, 1, lines, reason="被炸膛反噬")

        finish_lines = self._finish_if_needed(group_id, room)
        if finish_lines:
            lines.extend(finish_lines)
            yield self._lines_result(event, lines)
            return

        if not room.get("chamber"):
            lines.extend(self._reload_chamber(room))

        if target_id == shooter_id and not bullet and shooter.get("alive"):
            lines.append(f"{self._player_name(room, shooter_id)} 对自己打出空弹，继续行动。")
        else:
            self._apply_end_of_action_effects(group_id, room, shooter_id, lines)
            finish_lines = self._finish_if_needed(group_id, room)
            if finish_lines:
                lines.extend(finish_lines)
                self._save()
                yield self._lines_result(event, lines)
                return
            lines.extend(self._advance_turn(room))

        self._save()
        yield self._lines_result(event, lines)

    async def _shoot(self, event: AstrMessageEvent, target_self: bool):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有进行中的房间。")
            return
        turn_error = self._ensure_playing_turn(event, room)
        if turn_error:
            yield self._lines_result(event, [turn_error])
            return

        shooter_id = str(event.get_sender_id())
        if target_self:
            target_id = shooter_id
        else:
            target_id = extract_target_id(event)
            if not target_id:
                yield event.plain_result("请 @ 一名要射击的玩家。")
                return

        if target_id not in room["player_map"] or not room["player_map"][target_id].get("alive"):
            yield event.plain_result("目标不在本局游戏中，或已经出局。")
            return

        if not room.get("chamber"):
            lines = self._reload_chamber(room)
        else:
            lines = []

        shooter = room["player_map"][shooter_id]
        target = room["player_map"][target_id]
        if shooter.get("sealed"):
            shooter["sealed"] = False
            lines.append(f"{self._player_name(room, shooter_id)} 被封膛符限制，无法真正开枪，行动结束。")
            self._apply_end_of_action_effects(group_id, room, shooter_id, lines)
            finish_lines = self._finish_if_needed(group_id, room)
            if finish_lines:
                lines.extend(finish_lines)
            else:
                lines.extend(self._advance_turn(room))
            self._save()
            yield self._lines_result(event, lines)
            return

        bullet = room["chamber"].pop(0)
        base_bonus = int(shooter.get("damage_bonus", 0))
        pressure_bonus = 1 if shooter.get("pressure") else 0
        rusty_ready = bool(shooter.get("rusty_barrel"))
        damage = 1 + base_bonus + pressure_bonus
        shooter["damage_bonus"] = 0
        shooter["pressure"] = False
        shooter["rusty_barrel"] = False

        if bullet:
            lines.append(
                f"{self._player_name(room, shooter_id)} 对 {self._player_name(room, target_id)} 开枪：实弹，造成 {damage} 点伤害。"
            )
            self._apply_damage_to_player(group_id, room, target_id, damage, lines, reason="被实弹命中")
            if rusty_ready and target.get("alive"):
                if target.get("bleeding"):
                    lines.append(f"{self._player_name(room, target_id)} 已经处于流血状态。")
                else:
                    target["bleeding"] = True
                    lines.append(f"{self._player_name(room, target_id)} 被锈蚀枪管附加流血。")
        else:
            lines.append(
                f"{self._player_name(room, shooter_id)} 对 {self._player_name(room, target_id)} 开枪：空弹。"
            )
            if pressure_bonus:
                lines.append("加压弹簧炸膛。")
                self._apply_damage_to_player(group_id, room, shooter_id, 1, lines, reason="被炸膛反噬")

        finish_lines = self._finish_if_needed(group_id, room)
        if finish_lines:
            lines.extend(finish_lines)
            yield self._lines_result(event, lines)
            return

        if not room.get("chamber"):
            lines.extend(self._reload_chamber(room))

        if target_self and not bullet and shooter.get("alive"):
            lines.append(f"{self._player_name(room, shooter_id)} 对自己打出空弹，继续行动。")
        else:
            self._apply_end_of_action_effects(group_id, room, shooter_id, lines)
            finish_lines = self._finish_if_needed(group_id, room)
            if finish_lines:
                lines.extend(finish_lines)
                self._save()
                yield self._lines_result(event, lines)
                return
            lines.extend(self._advance_turn(room))

        self._save()
        yield self._lines_result(event, lines)

    @filter.command("梭哈", alias={"轮盘梭哈", "drallin"})
    async def all_in(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有进行中的房间。")
            return
        turn_error = self._ensure_playing_turn(event, room)
        if turn_error:
            yield self._lines_result(event, [turn_error])
            return

        user_id = str(event.get_sender_id())
        shooter = room["player_map"][user_id]
        args = strip_command(event.message_str, ["梭哈", "轮盘梭哈", "drallin"])
        normalized_args = args.strip().lower()
        count_match = re.search(r"\d+", args)
        requested_shots = None
        if count_match:
            requested_shots = max(1, int(count_match.group(0)))

        if not normalized_args:
            yield event.plain_result(
                "梭哈前想清楚。\n"
                "惜命：/梭哈 数量\n"
                "英雄：/梭哈 all"
            )
            return

        if not room.get("chamber"):
            lines = self._reload_chamber(room)
        else:
            lines = []

        remaining_in_chamber = len(room.get("chamber", []))
        if "all" in normalized_args:
            max_shots = remaining_in_chamber
            lines.append(f"{self._player_name(room, user_id)} 选择梭哈 all，对自己连续开枪。")
        elif requested_shots is not None:
            max_shots = min(requested_shots, remaining_in_chamber)
            lines.append(f"{self._player_name(room, user_id)} 选择梭哈 {requested_shots}，对自己连续开枪。")
        else:
            yield event.plain_result("请输入 /梭哈 数量 或 /梭哈 all。")
            return

        if shooter.get("sealed"):
            shooter["sealed"] = False
            lines.append(f"{self._player_name(room, user_id)} 被封膛符限制，无法真正开枪，行动结束。")
            self._apply_end_of_action_effects(group_id, room, user_id, lines)
            finish_lines = self._finish_if_needed(group_id, room)
            if finish_lines:
                lines.extend(finish_lines)
            else:
                lines.extend(self._advance_turn(room))
            self._save()
            yield self._lines_result(event, lines)
            return

        pressure_ready = bool(shooter.get("pressure"))
        rusty_ready = bool(shooter.get("rusty_barrel"))
        first_shot_bonus = int(shooter.get("damage_bonus", 0)) + (1 if pressure_ready else 0)
        shooter["damage_bonus"] = 0
        shooter["pressure"] = False
        shooter["rusty_barrel"] = False
        blanks = 0
        shot_index = 0
        hit_live = False

        while shot_index < max_shots and room.get("chamber") and shooter.get("alive"):
            bullet = room["chamber"].pop(0)
            shot_index += 1
            if not bullet:
                blanks += 1
                if shot_index == 1 and pressure_ready:
                    lines.append("加压弹簧炸膛。")
                    self._apply_damage_to_player(group_id, room, user_id, 1, lines, reason="被炸膛反噬")
                continue

            hit_live = True
            damage = 1 + (first_shot_bonus if shot_index == 1 else 0)
            if blanks:
                lines.append(f"连续打出 {blanks} 发空弹。")
            lines.append("随后打出实弹。")
            self._apply_damage_to_player(group_id, room, user_id, damage, lines, reason="被实弹命中")
            if rusty_ready and shooter.get("alive"):
                if shooter.get("bleeding"):
                    lines.append(f"{self._player_name(room, user_id)} 已经处于流血状态。")
                else:
                    shooter["bleeding"] = True
                    lines.append(f"{self._player_name(room, user_id)} 被锈蚀枪管附加流血。")
            break

        if not hit_live and blanks:
            lines.append(f"连续打出 {blanks} 发空弹，未触发实弹。")

        finish_lines = self._finish_if_needed(group_id, room)
        if finish_lines:
            lines.extend(finish_lines)
            yield self._lines_result(event, lines)
            return

        chamber_emptied = not room.get("chamber")
        if hit_live or chamber_emptied:
            if chamber_emptied and not hit_live:
                lines.append("弹仓已清空，进入下一轮。")
                lines.extend(self._reload_chamber(room))
                lines.append(f"{self._player_name(room, user_id)} 梭哈未中实弹，继续行动。")
            else:
                self._apply_end_of_action_effects(group_id, room, user_id, lines)
                finish_lines = self._finish_if_needed(group_id, room)
                if finish_lines:
                    lines.extend(finish_lines)
                    self._save()
                    yield self._lines_result(event, lines)
                    return
                lines.extend(self._advance_turn(room))
        else:
            lines.append(f"{self._player_name(room, user_id)} 梭哈未中实弹，继续行动。")

        self._save()
        yield self._lines_result(event, lines)

    @filter.command("使用道具", alias={"用道具", "使用", "dritem"})
    async def use_item(self, event: AstrMessageEvent):
        async for result in self._use_item(event):
            yield result

    @filter.command("使用放大镜", alias={"用放大镜"})
    async def use_magnifier(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "魔镜"):
            yield result

    @filter.command("使用香烟", alias={"用香烟"})
    async def use_cigarette(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "止痛片"):
            yield result

    @filter.command("使用啤酒", alias={"用啤酒"})
    async def use_beer(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "肘击"):
            yield result

    @filter.command("使用手铐", alias={"用手铐"})
    async def use_handcuffs(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "束线带"):
            yield result

    @filter.command("使用短刀", alias={"用短刀"})
    async def use_knife(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "改装工具"):
            yield result

    @filter.command("使用魔镜", alias={"用魔镜"})
    async def use_magic_mirror(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "魔镜"):
            yield result

    @filter.command("使用止痛片", alias={"用止痛片"})
    async def use_painkiller(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "止痛片"):
            yield result

    @filter.command("使用肘击", alias={"用肘击"})
    async def use_elbow(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "肘击"):
            yield result

    @filter.command("使用改装工具", alias={"用改装工具", "使用手锯", "用手锯"})
    async def use_mod_tool(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "改装工具"):
            yield result

    @filter.command("使用束线带", alias={"用束线带"})
    async def use_zip_tie(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "束线带"):
            yield result

    @filter.command("使用反转器", alias={"用反转器", "使用逆转器", "用逆转器"})
    async def use_inverter(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "反转器"):
            yield result

    @filter.command("使用测弹仪", alias={"用测弹仪", "使用一次性手机", "用一次性手机", "使用手机", "用手机"})
    async def use_bullet_detector(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "测弹仪"):
            yield result

    @filter.command("使用顺手牵羊", alias={"用顺手牵羊", "使用肾上腺素", "用肾上腺素"})
    async def use_lift_item(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "顺手牵羊"):
            yield result

    @filter.command("使用怪味蘑菇", alias={"用怪味蘑菇", "使用过期药", "用过期药"})
    async def use_weird_mushroom(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "怪味蘑菇"):
            yield result

    @filter.command("使用凶吉签", alias={"用凶吉签"})
    async def use_omen_lot(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "凶吉签"):
            yield result

    @filter.command("使用绷带", alias={"用绷带"})
    async def use_bandage(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "绷带"):
            yield result

    @filter.command("使用酒狐委托", alias={"用酒狐委托"})
    async def use_jiuhu_commission(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "酒狐委托"):
            yield result

    @filter.command("使用封膛符", alias={"用封膛符"})
    async def use_seal_chamber(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "封膛符"):
            yield result

    @filter.command("使用锈蚀枪管", alias={"用锈蚀枪管"})
    async def use_rusty_barrel(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "锈蚀枪管"):
            yield result

    @filter.command("使用加压弹簧", alias={"用加压弹簧"})
    async def use_pressure_spring(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "加压弹簧"):
            yield result

    @filter.command("使用隙间之手", alias={"用隙间之手"})
    async def use_gap_hand(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "隙间之手"):
            yield result

    @filter.command("使用梓的不死图腾", alias={"用梓的不死图腾", "使用不死图腾", "用不死图腾"})
    async def use_azusa_totem(self, event: AstrMessageEvent):
        async for result in self._use_item(event, "梓的不死图腾"):
            yield result

    async def _use_item(self, event: AstrMessageEvent, forced_item: str | None = None):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有进行中的房间。")
            return
        turn_error = self._ensure_playing_turn(event, room)
        if turn_error:
            yield self._lines_result(event, [turn_error])
            return

        args = strip_command(
            event.message_str,
            [
                "使用道具",
                "用道具",
                "使用",
                "dritem",
                "使用放大镜",
                "用放大镜",
                "使用香烟",
                "用香烟",
                "使用啤酒",
                "用啤酒",
                "使用手铐",
                "用手铐",
                "使用短刀",
                "用短刀",
                "使用魔镜",
                "用魔镜",
                "使用止痛片",
                "用止痛片",
                "使用肘击",
                "用肘击",
                "使用改装工具",
                "用改装工具",
                "使用手锯",
                "用手锯",
                "使用束线带",
                "用束线带",
                "使用反转器",
                "用反转器",
                "使用逆转器",
                "用逆转器",
                "使用测弹仪",
                "用测弹仪",
                "使用一次性手机",
                "用一次性手机",
                "使用手机",
                "用手机",
                "使用顺手牵羊",
                "用顺手牵羊",
                "使用肾上腺素",
                "用肾上腺素",
                "使用怪味蘑菇",
                "用怪味蘑菇",
                "使用过期药",
                "用过期药",
                "使用凶吉签",
                "用凶吉签",
                "使用绷带",
                "用绷带",
                "使用酒狐委托",
                "用酒狐委托",
                "使用封膛符",
                "用封膛符",
                "使用锈蚀枪管",
                "用锈蚀枪管",
                "使用加压弹簧",
                "用加压弹簧",
                "使用隙间之手",
                "用隙间之手",
                "使用梓的不死图腾",
                "用梓的不死图腾",
                "使用不死图腾",
                "用不死图腾",
            ],
        )
        item = forced_item
        if not item:
            for candidate in (ISEKAI_ITEMS + ["梓的不死图腾"] if self._is_iseikai(room) else NORMAL_ITEMS):
                if candidate in args:
                    item = candidate
                    break
        if not item and not self._is_iseikai(room):
            for alias, canonical in NORMAL_ITEM_ALIASES.items():
                if alias in args:
                    item = canonical
                    break
        if not item:
            item_text = "、".join(ISEKAI_ITEMS + ["梓的不死图腾"] if self._is_iseikai(room) else NORMAL_ITEMS)
            yield event.plain_result(f"请指定道具：{item_text}。")
            return

        user_id = str(event.get_sender_id())
        player = room["player_map"][user_id]
        lines = []

        if item == "梓的不死图腾":
            if player.get("special_item") != "梓的不死图腾":
                yield event.plain_result("你没有这个特殊道具。")
                return
            target_id = extract_target_id(event, args) or user_id
            target = room["player_map"].get(target_id)
            if not target or not target.get("alive"):
                yield event.plain_result("目标不在本局游戏中，或已经出局。")
                return
            if target["hp"] >= target["max_hp"] and not target.get("bleeding"):
                yield event.plain_result("目标不需要治疗。")
                return
            player["special_item"] = ""
            target["hp"] = min(target["max_hp"], target["hp"] + 1)
            target["bleeding"] = False
            lines.append(
                f"{self._player_name(room, user_id)} 使用梓的不死图腾，"
                f"{self._player_name(room, target_id)} 回复 1 血并解除流血。"
            )

        elif item in ("香烟", "止痛片", "绷带", "怪味蘑菇"):
            if item == "怪味蘑菇":
                if not self._consume_item(player, item):
                    yield event.plain_result("你没有这个道具。")
                    return
                if random.choice([True, False]):
                    player["hp"] = min(player["max_hp"], player["hp"] + 2)
                    lines.append(f"{self._player_name(room, user_id)} 吃下怪味蘑菇，回复 2 血。当前 {player['hp']}/{player['max_hp']} 血。")
                else:
                    self._apply_damage_to_player(group_id, room, user_id, 1, lines, reason="被怪味蘑菇反噬")
                finish_lines = self._finish_if_needed(group_id, room)
                if finish_lines:
                    lines.extend(finish_lines)
                self._save()
                yield self._lines_result(event, lines)
                return
            if player["hp"] >= player["max_hp"]:
                if item == "绷带" and player.get("bleeding"):
                    pass
                else:
                    yield event.plain_result(f"你的血量已满，不能使用{item}。")
                    return
            if item == "绷带" and not player.get("bleeding") and player["hp"] >= player["max_hp"]:
                yield event.plain_result("你不需要使用绷带。")
                return
            if not self._consume_item(player, item):
                yield event.plain_result("你没有这个道具。")
                return
            player["hp"] = min(player["max_hp"], player["hp"] + 1)
            if item == "绷带":
                player["bleeding"] = False
                lines.append(f"{self._player_name(room, user_id)} 使用绷带，回复 1 血并解除流血。当前 {player['hp']}/{player['max_hp']} 血。")
            elif item == "止痛片":
                lines.append(f"{self._player_name(room, user_id)} 使用止痛片，回复 1 血。当前 {player['hp']}/{player['max_hp']} 血。")
            else:
                lines.append(f"{self._player_name(room, user_id)} 使用香烟，回复 1 血。当前 {player['hp']}/{player['max_hp']} 血。")

        elif item in ("放大镜", "魔镜", "凶吉签", "测弹仪"):
            if not room.get("chamber"):
                lines.extend(self._reload_chamber(room))
            if not self._consume_item(player, item):
                yield event.plain_result("你没有这个道具。")
                return
            if item == "测弹仪":
                idx = random.randint(0, len(room["chamber"]) - 1)
                bullet_text = "实弹" if room["chamber"][idx] else "空弹"
                lines.append(f"{self._player_name(room, user_id)} 使用测弹仪。第 {idx + 1} 发是：{bullet_text}。")
            else:
                bullet_text = "实弹" if room["chamber"][0] else "空弹"
            if item == "凶吉签":
                lines.append(f"{self._player_name(room, user_id)} 使用凶吉签。签象：{'凶' if room['chamber'][0] else '吉'}（{bullet_text}）。")
            elif item == "魔镜":
                lines.append(f"{self._player_name(room, user_id)} 使用魔镜。当前子弹是：{bullet_text}。")
            elif item == "测弹仪":
                pass
            else:
                lines.append(f"{self._player_name(room, user_id)} 使用放大镜。当前子弹是：{bullet_text}。")

        elif item in ("啤酒", "肘击", "酒狐委托"):
            if not room.get("chamber"):
                lines.extend(self._reload_chamber(room))
            if not self._consume_item(player, item):
                yield event.plain_result("你没有这个道具。")
                return
            if item == "酒狐委托":
                insert_at = random.randint(0, len(room["chamber"]))
                room["chamber"].insert(insert_at, random.choice([True, False]))
                lines.append(f"{self._player_name(room, user_id)} 使用了酒狐委托。")
                lines.append("酒狐打工中！她往弹仓里塞了一发子弹。")
                lines.append("至于是真还是假，她说她忘了。")
            elif item == "肘击":
                bullet = room["chamber"].pop(0)
                lines.append(f"{self._player_name(room, user_id)} 被牢大肘击了一下，子弹突然掉出来了一颗。")
                lines.append(f"是{'实弹' if bullet else '空弹'}。")
                if not room.get("chamber"):
                    lines.extend(self._reload_chamber(room))
            else:
                bullet = room["chamber"].pop(0)
                lines.append(
                    f"{self._player_name(room, user_id)} 使用啤酒，退掉了一发{'实弹' if bullet else '空弹'}。"
                )
                if not room.get("chamber"):
                    lines.extend(self._reload_chamber(room))

        elif item in ("短刀", "改装工具", "锈蚀枪管", "加压弹簧"):
            state_key = "damage_bonus" if item in ("短刀", "改装工具") else ("rusty_barrel" if item == "锈蚀枪管" else "pressure")
            if player.get(state_key):
                yield event.plain_result(f"你已经准备了{item}，不能重复使用。")
                return
            if not self._consume_item(player, item):
                yield event.plain_result("你没有这个道具。")
                return
            if item in ("短刀", "改装工具"):
                player["damage_bonus"] = 1
                lines.append(f"{self._player_name(room, user_id)} 使用{item}，下一枪若为实弹则伤害 +1。")
            elif item == "锈蚀枪管":
                player["rusty_barrel"] = True
                lines.append(f"{self._player_name(room, user_id)} 装上锈蚀枪管，下一次实弹命中玩家时附加流血。")
            else:
                player["pressure"] = True
                lines.append(f"{self._player_name(room, user_id)} 装上加压弹簧，下一枪高风险高收益。")

        elif item in ("手铐", "束线带", "封膛符", "隙间之手", "顺手牵羊"):
            target_id = extract_target_id(event, args)
            if not target_id:
                yield event.plain_result(f"使用{item}需要 @ 一名存活玩家。")
                return
            if target_id == user_id:
                yield event.plain_result(f"不能对自己使用{item}。")
                return
            target = room["player_map"].get(target_id)
            if not target or not target.get("alive"):
                yield event.plain_result("目标不在本局游戏中，或已经出局。")
                return
            if item in ("隙间之手", "顺手牵羊"):
                if not target.get("items"):
                    yield event.plain_result(f"目标没有普通道具，{item}使用失败。")
                    return
                if not self._consume_item(player, item):
                    yield event.plain_result("你没有这个道具。")
                    return
                stolen = random.choice(target["items"])
                target["items"].remove(stolen)
                if item == "顺手牵羊":
                    lines.append(f"{self._player_name(room, user_id)} 顺手牵羊，从 {self._player_name(room, target_id)} 那里偷到了：{stolen}。")
                    if stolen == "魔镜":
                        if not room.get("chamber"):
                            lines.extend(self._reload_chamber(room))
                        bullet_text = "实弹" if room["chamber"][0] else "空弹"
                        lines.append(f"顺手牵羊立即使用魔镜。当前子弹是：{bullet_text}。")
                    elif stolen == "止痛片":
                        player["hp"] = min(player["max_hp"], player["hp"] + 1)
                        lines.append(f"顺手牵羊立即使用止痛片。当前 {player['hp']}/{player['max_hp']} 血。")
                    elif stolen == "肘击":
                        if not room.get("chamber"):
                            lines.extend(self._reload_chamber(room))
                        bullet = room["chamber"].pop(0)
                        lines.append(f"{self._player_name(room, user_id)} 被牢大肘击了一下，子弹突然掉出来了一颗。")
                        lines.append(f"是{'实弹' if bullet else '空弹'}。")
                        if not room.get("chamber"):
                            lines.extend(self._reload_chamber(room))
                    elif stolen == "改装工具":
                        player["damage_bonus"] = 1
                        lines.append("顺手牵羊立即使用改装工具，下一枪若为实弹则伤害 +1。")
                    elif stolen == "束线带":
                        target["skipped"] = True
                        lines.append(f"顺手牵羊立即使用束线带，{self._player_name(room, target_id)} 的下一次行动将被完全跳过。")
                    elif stolen == "反转器":
                        if not room.get("chamber"):
                            lines.extend(self._reload_chamber(room))
                        room["chamber"][0] = not room["chamber"][0]
                        lines.append("顺手牵羊立即使用反转器，当前子弹被反转。")
                    elif stolen == "测弹仪":
                        if not room.get("chamber"):
                            lines.extend(self._reload_chamber(room))
                        idx = random.randint(0, len(room["chamber"]) - 1)
                        bullet_text = "实弹" if room["chamber"][idx] else "空弹"
                        lines.append(f"顺手牵羊立即使用测弹仪。第 {idx + 1} 发是：{bullet_text}。")
                    elif stolen == "怪味蘑菇":
                        if random.choice([True, False]):
                            player["hp"] = min(player["max_hp"], player["hp"] + 2)
                            lines.append(f"顺手牵羊立即吃下怪味蘑菇，回复 2 血。当前 {player['hp']}/{player['max_hp']} 血。")
                        else:
                            self._apply_damage_to_player(group_id, room, user_id, 1, lines, reason="被怪味蘑菇反噬")
                            finish_lines = self._finish_if_needed(group_id, room)
                            if finish_lines:
                                lines.extend(finish_lines)
                    else:
                        player["items"].append(stolen)
                        lines.append("偷到的道具暂时无法立即使用，已放入背包。")
                else:
                    player["items"].append(stolen)
                    lines.append(f"{self._player_name(room, user_id)} 使用隙间之手，从 {self._player_name(room, target_id)} 那里偷到了：{stolen}。")
            elif item == "封膛符":
                if target.get("sealed"):
                    yield event.plain_result("目标已经被封膛，不能叠加。")
                    return
                if not self._consume_item(player, item):
                    yield event.plain_result("你没有这个道具。")
                    return
                target["sealed"] = True
                lines.append(f"{self._player_name(room, user_id)} 对 {self._player_name(room, target_id)} 使用封膛符。目标下一次行动无法真正开枪。")
            elif target.get("skipped"):
                yield event.plain_result("目标已经被限制，不能叠加。")
                return
            else:
                if not self._consume_item(player, item):
                    yield event.plain_result("你没有这个道具。")
                    return
                target["skipped"] = True
                lines.append(
                    f"{self._player_name(room, user_id)} 对 {self._player_name(room, target_id)} 使用{item}。"
                    f"{self._player_name(room, target_id)} 的下一次行动将被完全跳过。"
                )

        elif item == "反转器":
            if not room.get("chamber"):
                lines.extend(self._reload_chamber(room))
            if not self._consume_item(player, item):
                yield event.plain_result("你没有这个道具。")
                return
            room["chamber"][0] = not room["chamber"][0]
            lines.append(f"{self._player_name(room, user_id)} 使用反转器，当前子弹被反转。")

        self._save()
        yield self._lines_result(event, lines)

    @filter.command("轮盘状态", alias={"轮盘状态", "drstatus"})
    async def room_status(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return
        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        yield event.plain_result(self._status_text(room))

    @filter.command("查看道具", alias={"我的道具", "轮盘道具", "dritems"})
    async def show_items(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return
        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        user_id = str(event.get_sender_id())
        player = room["player_map"].get(user_id)
        if not player:
            yield event.plain_result("你不在本局游戏中。")
            return
        items = "、".join(player.get("items", [])) or "无"
        special = player.get("special_item") or "无"
        pending = player.get("pending_special_item") or "无"
        yield event.plain_result(
            f"{self._player_name(room, user_id)} 当前道具：{items}\n特殊道具：{special}\n待确认特殊道具：{pending}"
        )

    @filter.command("替换特殊道具", alias={"替换特殊", "specialreplace"})
    async def replace_special_item(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return
        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        user_id = str(event.get_sender_id())
        player = room["player_map"].get(user_id)
        if not player or not player.get("pending_special_item"):
            yield event.plain_result("你没有待确认的特殊道具。")
            return
        old_item = player.get("special_item") or "无"
        player["special_item"] = player["pending_special_item"]
        player["pending_special_item"] = ""
        self._save()
        yield event.plain_result(f"{self._player_name(room, user_id)} 已将特殊道具 {old_item} 替换为 {player['special_item']}。")

    @filter.command("放弃特殊道具", alias={"放弃特殊", "specialdrop"})
    async def drop_pending_special_item(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return
        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        user_id = str(event.get_sender_id())
        player = room["player_map"].get(user_id)
        if not player or not player.get("pending_special_item"):
            yield event.plain_result("你没有待确认的特殊道具。")
            return
        dropped = player["pending_special_item"]
        player["pending_special_item"] = ""
        self._save()
        yield event.plain_result(f"{self._player_name(room, user_id)} 放弃了特殊道具：{dropped}。")

    @filter.command("死亡榜", alias={"轮盘死亡榜", "drdeath"})
    async def death_ranking(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return
        group_stats = self.death_stats.get(group_id, {})
        if not group_stats:
            yield event.plain_result("本群还没有死亡记录。")
            return
        ranking = sorted(
            group_stats.values(),
            key=lambda item: int(item.get("count", 0)),
            reverse=True,
        )[:10]
        lines = ["轮盘死亡榜："]
        for index, item in enumerate(ranking, 1):
            lines.append(f"{index}. {self._short_name(item.get('name', '未知玩家'))}：{int(item.get('count', 0))} 次")
        yield self._lines_result(event, lines)

    @filter.command("轮盘处决", alias={"轮盘淘汰", "处决", "drexecute"})
    async def execute_player(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return

        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        user_id = str(event.get_sender_id())
        if not self._is_manager(room, user_id):
            yield event.plain_result("只有房主或超级管理员可以处决挂机玩家。")
            return
        target_id = extract_target_id(event)
        if not target_id:
            yield event.plain_result("请 @ 一名要处决的玩家。")
            return
        target = room["player_map"].get(target_id)
        if not target or not target.get("alive"):
            yield event.plain_result("目标不在本局游戏中，或已经出局。")
            return

        target["hp"] = 0
        target["alive"] = False
        target["skipped"] = False
        target["damage_bonus"] = 0
        self._record_death(group_id, target)
        lines = [f"{self._player_name(room, target_id)} 被判定为挂机，已被处决。"]

        finish_lines = self._finish_if_needed(group_id, room)
        if finish_lines:
            lines.extend(finish_lines)
            yield self._lines_result(event, lines)
            return

        current = self._current_id(room)
        if current == target_id:
            lines.extend(self._advance_turn(room))
        else:
            lines.append(self._turn_line(room, self._current_id(room)))
        self._save()
        yield self._lines_result(event, lines)

    @filter.command("轮盘结束", alias={"结束轮盘", "drend"})
    async def end_room(self, event: AstrMessageEvent):
        group_id, error = self._group_id_or_reply(event)
        if error:
            yield event.plain_result(error)
            return
        if not group_id:
            return
        room = self.rooms.get(group_id)
        if not room:
            yield event.plain_result("本群没有轮盘赌房间。")
            return
        user_id = str(event.get_sender_id())
        if not self._is_manager(room, user_id):
            yield event.plain_result("只有房主或超级管理员可以结束游戏。")
            return
        self.rooms.pop(group_id, None)
        self._save()
        yield event.plain_result("本群轮盘赌房间已结束。")

    @filter.command("轮盘帮助", alias={"轮盘帮助", "drhelp"})
    async def help(self, event: AstrMessageEvent):
        text = (
            "简单的轮盘赌 v0.3.2\n"
            "指令：\n"
            "/轮盘创建 - 创建房间\n"
            "/轮盘创建 异界战争 - 创建异界战争模式\n"
            "/退出房间 - 游戏开始前退出房间\n"
            "/轮盘加入 - 加入房间\n"
            "/轮盘开始 - 开始游戏\n"
            "/开自己 - 对自己开枪\n"
            "/开 @玩家 - 对指定玩家开枪\n"
            "/听天由命 - 多人局随机命运目标\n"
            "/梭哈 - 查看梭哈确认提示\n"
            "/梭哈 数量 - 最多使用指定数量，不跨弹仓\n"
            "/梭哈 all - 梭哈当前弹仓剩余全部子弹\n"
            "/使用道具 道具名 - 使用道具\n"
            "/使用束线带 @玩家、/使用肘击 - 普通模式道具短指令\n"
            "原道具名也可作为输入别名使用\n"
            "/替换特殊道具、/放弃特殊道具 - 处理待确认特殊道具\n"
            "/查看道具 - 查看自己的道具\n"
            "/轮盘状态 - 查看状态\n"
            "/死亡榜 - 查看本群死亡排行\n"
            "/轮盘处决 @玩家 - 房主/超级管理员处决挂机玩家\n"
            "/轮盘结束 - 房主/超级管理员结束房间\n\n"
            "道具：放大镜、香烟、啤酒、手铐、短刀。\n"
            "放大镜结果公开；被手铐跳过时不能使用道具或开枪。"
        )
        yield event.plain_result(text)
