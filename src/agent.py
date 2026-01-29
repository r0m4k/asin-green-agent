from agentbeats import BeatsAgent
from .logic import NavigationLogic
from .utils import get_destination_point, STEP_SIZE_METERS, LEVEL_CONFIG
import os
import json
import re
import math

import traceback

class ASINGreenAgent(BeatsAgent):
    def __init__(self, agent_host: str = "0.0.0.0", agent_port: int = 9009):
        super().__init__(
            name="ASIN-Green-Agent",
            agent_host=agent_host,
            agent_port=agent_port,
            model_type="green_agent", 
            model_name="asin-v1"
        )
        
        # Competition/security: NEVER ship a fallback API key.
        env_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
        self.api_key = env_key or None
        self.logic = NavigationLogic(self.api_key) if self.api_key else None
        
        # Simple In-Memory State
        self.sessions = {}
        
        # Task counter for auto-incrementing levels during benchmark
        self.task_counter = 0

    def _determine_level(self, task_config):
        """
        Determine which level to run.
        - Prefer explicit config (level/task_index/task_id).
        - Fall back to cycling 1..10 for local testing.
        """
        if task_config is not None and task_config != "":
            # AgentBeats/AgentX commonly passes task_config as a STRING (e.g. "0", "Task description: 0")
            # rather than a dict. Handle both robustly.

            # 1) Dict-style task_config
            if isinstance(task_config, dict):
                # Explicit level wins
                if "level" in task_config:
                    try:
                        lvl = int(task_config["level"])
                        if 1 <= lvl <= 10:
                            return lvl
                    except Exception:
                        pass

                # Common runner fields: task_index (0-based)
                for key in ("task_index", "index"):
                    if key in task_config:
                        try:
                            idx = int(task_config[key])
                            lvl = idx + 1
                            if 1 <= lvl <= 10:
                                return lvl
                        except Exception:
                            pass

                # task_id is often a UUID/string; do not rely on it for sequential levels.

            # 2) Scalar/string task_config (common in AgentBeats)
            else:
                s = str(task_config).strip()
                s_lower = s.lower()

                # If caller explicitly says "level", interpret the number as level (1..10).
                if re.search(r"\blevel\b", s_lower):
                    m = re.search(r"(-?\d+)", s_lower)
                    if m:
                        try:
                            lvl = int(m.group(1))
                            if 1 <= lvl <= 10:
                                return lvl
                        except Exception:
                            pass

                # Otherwise (including "Task description: <n>"), interpret the first integer as
                # task_index (0-based) and map deterministically to level = index + 1.
                m = re.search(r"(-?\d+)", s_lower)
                if m:
                    try:
                        idx = int(m.group(1))
                        lvl = idx + 1
                        if 1 <= lvl <= 10:
                            return lvl
                        # If the extracted integer looks like a 1-based level (1..10), accept it.
                        if 1 <= idx <= 10:
                            return idx
                    except Exception:
                        pass

        lvl = (self.task_counter % 10) + 1
        self.task_counter += 1
        return lvl

    def start(self, session_id, task_config=None):
        try:
            if not self.logic:
                return {
                    "error": "Missing GOOGLE_MAPS_API_KEY. Please provide it via environment variable.",
                    "done": True
                }

            # Determine Level
            level = self._determine_level(task_config)
            
            # Generate deterministic route based on Level
            # We use a fixed seed per level to ensure every agent faces the EXACT same route configuration.
            # This is critical for fair benchmarking.
            seed = 1000 + level
            
            print(f"[Session {session_id}] Starting Level {level} (Seed: {seed})")
            
            route_data = self.logic.generate_route(level=level, seed=seed)
            if not route_data:
                return {
                    "error": "Failed to generate route (API or logic error)",
                    "done": True
                }

            # Initialize State
            target_dist_m = int(LEVEL_CONFIG[level]["dist"])
            # Step limit policy:
            # - Base steps needed if perfectly moving forward: target_dist / STEP_SIZE_METERS
            # - Multiply by 3 to allow recovery from wrong turns
            # - Floor at 120 so short levels still have room for turns
            max_steps = max(120, int(math.ceil(target_dist_m / float(STEP_SIZE_METERS))) * 3)
            state = {
                "level": level,
                "route_poly": route_data['route_poly'],
                "generated_waypoints": route_data['generated_waypoints'],
                "current_pos": route_data['start_pos'],
                "current_heading": route_data['start_heading'],
                "walked_path": [route_data['start_pos']],
                "step_count": 0,
                "max_steps": max_steps,
                "target_dist_m": target_dist_m,
                "route_dist_m": route_data.get("total_dist"),
                "original_route_dist_m": route_data.get("original_total_dist"),
            }
            
            # Generate Initial Observations
            import time
            map_b64 = None
            view_b64 = None
            for attempt in range(5):
                if not map_b64:
                    map_b64 = self.logic.get_map_base64(state['route_poly'], state['generated_waypoints'])
                if not view_b64:
                    view_b64 = self.logic.get_view_base64(
                        state['current_pos'][0], state['current_pos'][1], state['current_heading']
                    )
                if map_b64 and view_b64:
                    break
                # Backoff: give the APIs a moment if they’re slow/throttling
                time.sleep(min(2 ** attempt, 8))

            # Hard requirement for A2A: always return 2 images
            if not map_b64:
                map_b64 = self.logic._placeholder_png_b64(640, 640, "Map unavailable (placeholder)")
            if not view_b64:
                view_b64 = self.logic._placeholder_png_b64(640, 400, "Street View unavailable (placeholder)")

            # Cache map to avoid repeated Static Maps calls every step
            state["map_b64"] = map_b64
            state["last_view_b64"] = view_b64
            self.sessions[session_id] = state
            
            return {
                "prompt": f"You are a spatial navigation agent in NYC. You have been dropped at Point A. Your goal is to reach the final red marker (Point {chr(65 + len(state['generated_waypoints']) - 1)}). Output ONE command: 'f' (move 15m), 'l <deg>', 'r <deg>', or 'q' (finish).",
                "images": [map_b64, view_b64], 
                "done": False,
                "info": {"level": level}
            }
        except Exception as e:
            print(f"ERROR inside start(): {traceback.format_exc()}")
            return {"error": f"Internal Error: {str(e)}", "done": True}

    def act(self, session_id, action):
        try:
            state = self.sessions.get(session_id)
            if not state:
                return {"error": "Session not found", "done": True}
                
            cmd = str(action).strip().lower()
            parts = cmd.split()
            base_cmd = parts[0] if parts else ""
            
            done = False
            info = {}
            
            if base_cmd == 'q':
                done = True
                info["reason"] = "Agent requested finish"
                
            elif base_cmd == 'l':
                try:
                    deg = float(parts[1]) if len(parts) > 1 else 90
                    state['current_heading'] = (state['current_heading'] - deg) % 360
                except: pass
                
            elif base_cmd == 'r':
                try:
                    deg = float(parts[1]) if len(parts) > 1 else 90
                    state['current_heading'] = (state['current_heading'] + deg) % 360
                except: pass
                
            elif base_cmd == 'f':
                new_lat, new_lon = get_destination_point(
                    state['current_pos'][0], state['current_pos'][1],
                    state['current_heading'], STEP_SIZE_METERS
                )
                state['current_pos'] = (new_lat, new_lon)
                state['walked_path'].append(state['current_pos'])
            
            state['step_count'] += 1
            if state['step_count'] >= state['max_steps']:
                done = True
                info["reason"] = "Max steps exceeded"
                
            # Regenerate Observations
            map_b64 = state.get("map_b64")
            if not map_b64:
                map_b64 = self.logic.get_map_base64(state['route_poly'], state['generated_waypoints'])
                state["map_b64"] = map_b64
            
            # Retry loop for Street View to handle API latency/throttling
            import time
            view_b64 = None
            for _ in range(5):
                view_b64 = self.logic.get_view_base64(state['current_pos'][0], state['current_pos'][1], state['current_heading'])
                if view_b64:
                    break
                print(f"Street View fetch failed, retrying... ({_+1}/5)")
                time.sleep(1)

            if not view_b64:
                # Do NOT fail the benchmark run due to transient network/API issues.
                # Fall back to last successful view (or a placeholder).
                info["warning"] = "Street View fetch failed; using fallback image"
                view_b64 = state.get("last_view_b64") or self.logic._placeholder_png_b64(
                    640, 400, "Street View unavailable (placeholder)"
                )
            else:
                state["last_view_b64"] = view_b64
            
            return {
                "prompt": f"Heading: {state['current_heading']:.0f}. Command?",
                "images": [map_b64, view_b64], # MUST return 2 images (Map, View)
                "done": done,
                "info": info
            }
        except Exception as e:
            print(f"ERROR inside act(): {traceback.format_exc()}")
            return {"error": f"Internal Error: {str(e)}", "done": True}

    def result(self, session_id):
        try:
            state = self.sessions.get(session_id)
            if not state:
                return {"score": 0, "error": "No state"}
                
            score, dest, sim, dist, dev = self.logic.calculate_final_score(
                state['walked_path'], state['route_poly']
            )
            
            # Generate Final Map with Path
            final_map_b64 = self.logic.get_final_map_base64(
                state['route_poly'],
                state['generated_waypoints'],
                state['walked_path']
            )
            
            # Apply Level Weight
            level = state['level']
            weight = LEVEL_CONFIG[level]["weight"]
            weighted_score = score * weight
            
            # Cleanup
            if session_id in self.sessions:
                del self.sessions[session_id]
            
            return {
                "score": weighted_score,
                "raw_score": score,
                "level": level,
                "weight": weight,
                "destination_reached": dest > 0,
                "distance_to_target": dist,
                "avg_deviation": dev,
                "final_map_b64": final_map_b64
            }
        except Exception as e:
            print(f"ERROR inside result(): {traceback.format_exc()}")
            return {"score": 0, "error": str(e)}


