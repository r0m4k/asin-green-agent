import math
import os
import re
import traceback

from .logic import NavigationLogic
from .utils import LEVEL_CONFIG, STEP_SIZE_METERS, get_destination_point


class ASINEnv:
    """
    Deterministic navigation environment + scoring for ASIN.

    This is the same core logic previously exposed via /start, /act, /result,
    but as a plain in-memory environment that can be orchestrated via A2A.
    """

    def __init__(self):
        env_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
        if not env_key:
            raise ValueError("Missing GOOGLE_MAPS_API_KEY")

        self.api_key = env_key
        self.logic = NavigationLogic(self.api_key)

        # session_id -> state
        self.sessions: dict[str, dict] = {}

    def _determine_level(self, task_config):
        """
        Determine which level to run from task_config (deterministic).
        Supported:
        - dict: {"level": 1..10} OR {"task_index": 0..9} OR {"index": 0..9}
        - string: "0".."9" OR "Task description: 0".."9" OR "level 3"
        """
        if task_config is None or task_config == "":
            # If the runner doesn't provide task_config, default to level 1.
            return 1

        # Dict-style
        if isinstance(task_config, dict):
            if "level" in task_config:
                try:
                    lvl = int(task_config["level"])
                    if 1 <= lvl <= 10:
                        return lvl
                except Exception:
                    pass

            for key in ("task_index", "index"):
                if key in task_config:
                    try:
                        idx = int(task_config[key])
                        lvl = idx + 1
                        if 1 <= lvl <= 10:
                            return lvl
                    except Exception:
                        pass

            return 1

        # Scalar/string style
        s = str(task_config).strip()
        s_lower = s.lower()

        if re.search(r"\blevel\b", s_lower):
            m = re.search(r"(-?\d+)", s_lower)
            if m:
                try:
                    lvl = int(m.group(1))
                    if 1 <= lvl <= 10:
                        return lvl
                except Exception:
                    pass

        m = re.search(r"(-?\d+)", s_lower)
        if m:
            try:
                idx = int(m.group(1))
                lvl = idx + 1
                if 1 <= lvl <= 10:
                    return lvl
                if 1 <= idx <= 10:
                    return idx
            except Exception:
                pass

        return 1

    def start(self, session_id: str, task_config=None) -> dict:
        try:
            level = self._determine_level(task_config)
            seed = 1000 + level
            print(f"[Session {session_id}] Starting Level {level} (Seed: {seed})")

            route_data = self.logic.generate_route(level=level, seed=seed)
            if not route_data:
                return {"error": "Failed to generate route", "done": True}

            target_dist_m = int(LEVEL_CONFIG[level]["dist"])
            max_steps = max(
                120, int(math.ceil(target_dist_m / float(STEP_SIZE_METERS))) * 3
            )

            state = {
                "level": level,
                "route_poly": route_data["route_poly"],
                "generated_waypoints": route_data["generated_waypoints"],
                "current_pos": route_data["start_pos"],
                "current_heading": route_data["start_heading"],
                "walked_path": [route_data["start_pos"]],
                "step_count": 0,
                "max_steps": max_steps,
                "target_dist_m": target_dist_m,
                "route_dist_m": route_data.get("total_dist"),
                "original_route_dist_m": route_data.get("original_total_dist"),
            }

            map_b64 = self.logic.get_map_base64(
                state["route_poly"], state["generated_waypoints"]
            )
            view_b64 = self.logic.get_view_base64(
                state["current_pos"][0], state["current_pos"][1], state["current_heading"]
            )

            if not map_b64:
                map_b64 = self.logic._placeholder_png_b64(
                    640, 640, "Map unavailable (placeholder)"
                )
            if not view_b64:
                view_b64 = self.logic._placeholder_png_b64(
                    640, 400, "Street View unavailable (placeholder)"
                )

            state["map_b64"] = map_b64
            state["last_view_b64"] = view_b64
            self.sessions[session_id] = state

            return {
                "prompt": (
                    "You are a spatial navigation agent in NYC. You have been dropped at Point A. "
                    f"Your goal is to reach the final red marker (Point {chr(65 + len(state['generated_waypoints']) - 1)}). "
                    "Output ONE command: 'f' (move 15m), 'l <deg>', 'r <deg>', or 'q' (finish)."
                ),
                "images": [map_b64, view_b64],
                "done": False,
                "info": {"level": level},
            }
        except Exception:
            print(f"ERROR inside start(): {traceback.format_exc()}")
            return {"error": "Internal error in start()", "done": True}

    def act(self, session_id: str, action: str) -> dict:
        try:
            state = self.sessions.get(session_id)
            if not state:
                return {"error": "Session not found", "done": True}

            cmd = str(action).strip().lower()
            parts = cmd.split()
            base_cmd = parts[0] if parts else ""

            done = False
            info: dict = {}

            if base_cmd == "q":
                done = True
                info["reason"] = "Agent requested finish"
            elif base_cmd == "l":
                try:
                    deg = float(parts[1]) if len(parts) > 1 else 90
                    state["current_heading"] = (state["current_heading"] - deg) % 360
                except Exception:
                    pass
            elif base_cmd == "r":
                try:
                    deg = float(parts[1]) if len(parts) > 1 else 90
                    state["current_heading"] = (state["current_heading"] + deg) % 360
                except Exception:
                    pass
            elif base_cmd == "f":
                new_lat, new_lon = get_destination_point(
                    state["current_pos"][0],
                    state["current_pos"][1],
                    state["current_heading"],
                    STEP_SIZE_METERS,
                )
                state["current_pos"] = (new_lat, new_lon)
                state["walked_path"].append(state["current_pos"])

            state["step_count"] += 1
            if state["step_count"] >= state["max_steps"]:
                done = True
                info["reason"] = "Max steps exceeded"

            map_b64 = state.get("map_b64")
            if not map_b64:
                map_b64 = self.logic.get_map_base64(
                    state["route_poly"], state["generated_waypoints"]
                )
                state["map_b64"] = map_b64

            view_b64 = self.logic.get_view_base64(
                state["current_pos"][0], state["current_pos"][1], state["current_heading"]
            )
            if not view_b64:
                info["warning"] = "Street View fetch failed; using fallback image"
                view_b64 = state.get("last_view_b64") or self.logic._placeholder_png_b64(
                    640, 400, "Street View unavailable (placeholder)"
                )
            else:
                state["last_view_b64"] = view_b64

            return {
                "prompt": f"Heading: {state['current_heading']:.0f}. Command?",
                "images": [map_b64, view_b64],
                "done": done,
                "info": info,
            }
        except Exception:
            print(f"ERROR inside act(): {traceback.format_exc()}")
            return {"error": "Internal error in act()", "done": True}

    def result(self, session_id: str) -> dict:
        try:
            state = self.sessions.get(session_id)
            if not state:
                return {"score": 0, "error": "No state"}

            score, dest, sim, dist, dev = self.logic.calculate_final_score(
                state["walked_path"], state["route_poly"]
            )

            final_map_b64 = self.logic.get_final_map_base64(
                state["route_poly"],
                state["generated_waypoints"],
                state["walked_path"],
            )

            level = state["level"]
            weight = LEVEL_CONFIG[level]["weight"]
            weighted_score = score * weight

            # cleanup
            self.sessions.pop(session_id, None)

            return {
                "score": weighted_score,
                "raw_score": score,
                "level": level,
                "weight": weight,
                "destination_reached": dest > 0,
                "distance_to_target": dist,
                "avg_deviation": dev,
                "final_map_b64": final_map_b64,
            }
        except Exception:
            print(f"ERROR inside result(): {traceback.format_exc()}")
            return {"score": 0, "error": "Internal error in result()"}

