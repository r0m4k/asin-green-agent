import random
import requests
import googlemaps
import polyline
import base64
import io
from PIL import Image, ImageDraw
from .utils import *

class NavigationLogic:
    def __init__(self, api_key):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("GOOGLE_MAPS_API_KEY is required")
        self.client = googlemaps.Client(key=api_key)
        self.api_key = api_key
        # Generous timeouts to avoid failing due to transient slowness.
        # requests timeout supports (connect_timeout, read_timeout)
        self.http_timeout = (20, 120)

    def _placeholder_png_b64(self, width, height, label):
        """Always return a valid PNG (base64) as a safe fallback."""
        img = Image.new("RGB", (width, height), color=(30, 30, 30))
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), str(label), fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _handle_api_error(self, e):
        """Helper to handle API errors."""
        print(f"API Error: {e}")

    def _polyline_length_meters(self, pts):
        if not pts or len(pts) < 2:
            return 0.0
        total = 0.0
        for i in range(len(pts) - 1):
            total += haversine_distance(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        return total

    def _truncate_polyline_to_meters(self, pts, target_meters):
        """
        Return a new polyline whose length is ~target_meters.
        The last point is interpolated on the segment where we cross target_meters.
        """
        if not pts or len(pts) < 2:
            return pts, 0.0
        if target_meters <= 0:
            return [pts[0]], 0.0

        out = [pts[0]]
        traveled = 0.0
        for i in range(len(pts) - 1):
            p1 = pts[i]
            p2 = pts[i + 1]
            seg = haversine_distance(p1[0], p1[1], p2[0], p2[1])
            if seg <= 0:
                continue

            if traveled + seg >= target_meters:
                remaining = target_meters - traveled
                t = max(0.0, min(1.0, remaining / seg))
                lat = p1[0] + (p2[0] - p1[0]) * t
                lon = p1[1] + (p2[1] - p1[1]) * t
                out.append((lat, lon))
                traveled = target_meters
                break

            out.append(p2)
            traveled += seg

        # Recompute for a stable reported length
        return out, self._polyline_length_meters(out)

    def generate_route(self, level=1, seed=None):
        """
        Generates a route for the given level.
        Returns: {
            'route_poly': list of (lat, lon),
            'generated_waypoints': list of (lat, lon),
            'start_pos': (lat, lon),
            'start_heading': float
        }
        """
        # Determinism guarantee:
        # Use a local RNG so the generated origin/destination/waypoints depend ONLY on `seed`
        # (not on global random state, concurrency, or other imports touching `random`).
        rng = random.Random(seed) if seed is not None else random.Random()
            
        config = LEVEL_CONFIG.get(level, LEVEL_CONFIG[1])
        target_dist = config["dist"]
        num_waypoints = config["waypoints"]
        
        # Add safeguard for strictly increasing distance
        # Note: In stateless server, we can't easily check "previous level" without passing it in.
        # Ideally, the seed ensures determinism, so Level 2 seed X is always harder than Level 1 seed X.
        
        # High enough to be extremely likely to find a valid route,
        # without risking an unbounded/billing runaway.
        max_attempts = 100
        for attempt in range(max_attempts):
            # 1. Random Start
            current_lat = rng.uniform(NYC_BOUNDS["lat_min"], NYC_BOUNDS["lat_max"])
            current_lon = rng.uniform(NYC_BOUNDS["lon_min"], NYC_BOUNDS["lon_max"])
            origin = (current_lat, current_lon)
            
            generated_waypoints_raw = [origin]
            waypoints_coords = [] 
            
            # 2. Random Walk Waypoints
            segment_len = target_dist / (num_waypoints + 1)
            deg_offset = (segment_len / 111000) 
            
            valid_generation = True
            temp_lat, temp_lon = current_lat, current_lon

            for _ in range(num_waypoints + 1): 
                lat_off = rng.uniform(-deg_offset, deg_offset)
                lon_off = rng.uniform(-deg_offset, deg_offset)
                
                next_lat = temp_lat + lat_off
                next_lon = temp_lon + lon_off
                
                if not (NYC_BOUNDS["lat_min"] < next_lat < NYC_BOUNDS["lat_max"] and
                        NYC_BOUNDS["lon_min"] < next_lon < NYC_BOUNDS["lon_max"]):
                    valid_generation = False
                    break
                    
                waypoints_coords.append((next_lat, next_lon))
                generated_waypoints_raw.append((next_lat, next_lon))
                temp_lat, temp_lon = next_lat, next_lon
            
            if not valid_generation: continue

            destination = waypoints_coords.pop()
            
            # 3. Get Directions
            try:
                directions = self.client.directions(
                    origin=origin,
                    destination=destination,
                    waypoints=waypoints_coords,
                    mode="driving",
                    avoid=["highways"]
                )
            except Exception as e:
                self._handle_api_error(e)
                continue

            if not directions: continue

            # Validate Manhattan
            route = directions[0]
            legs = route['legs']
            
            def is_manhattan(addr):
                return "Manhattan" in addr or "New York, NY" in addr

            start_addr = legs[0]['start_address']
            end_addr = legs[-1]['end_address']
            
            if not is_manhattan(start_addr) or not is_manhattan(end_addr): continue
            
            intermediate_ok = True
            for leg in legs[:-1]:
                if not is_manhattan(leg['end_address']):
                    intermediate_ok = False; break
            if not intermediate_ok: continue

            # Validate Distance
            route_dist_meters = sum(leg['distance']['value'] for leg in legs)
            # NOTE:
            # We don't require Google-reported distance to match exactly, because we will
            # *truncate the polyline* to match target_dist precisely for evaluation.
            # We only require the candidate route to be long enough to cut down to target_dist.
            if route_dist_meters < target_dist:
                continue
            
            # Extract Snapped Waypoints
            snapped_waypoints = []
            start_loc = legs[0]['start_location']
            snapped_waypoints.append((start_loc['lat'], start_loc['lng']))
            for leg in legs[:-1]:
                end_loc = leg['end_location']
                snapped_waypoints.append((end_loc['lat'], end_loc['lng']))
            final_loc = legs[-1]['end_location']
            snapped_waypoints.append((final_loc['lat'], final_loc['lng']))

            encoded_poly = route['overview_polyline']['points']
            route_poly = polyline.decode(encoded_poly)

            # Enforce that the evaluation route length matches LEVEL_CONFIG exactly:
            # truncate the polyline to `target_dist` meters and update the final waypoint accordingly.
            poly_len = self._polyline_length_meters(route_poly)
            if poly_len < target_dist:
                continue

            # If the level has intermediate waypoint(s), ensure the truncation doesn't stop before them.
            # For waypoints=1, the intermediate is at the end of the first leg.
            if num_waypoints >= 1 and len(legs) >= 2:
                first_leg_dist = legs[0]['distance']['value']
                if target_dist <= first_leg_dist + 25:
                    continue

            route_poly, eval_dist = self._truncate_polyline_to_meters(route_poly, float(target_dist))
            truncated_end = route_poly[-1]

            # Replace the final waypoint with the truncated endpoint (keeps intermediates, aligns length)
            snapped_waypoints[-1] = truncated_end
            
            start_pos = route_poly[0]
            start_heading = 0
            if len(route_poly) > 1:
                start_heading = calculate_initial_bearing(
                    route_poly[0][0], route_poly[0][1],
                    route_poly[1][0], route_poly[1][1]
                )

            return {
                'route_poly': route_poly,
                'generated_waypoints': snapped_waypoints,
                'start_pos': start_pos,
                'start_heading': start_heading,
                # Distance used by the benchmark (aligned with LEVEL_CONFIG)
                'total_dist': eval_dist,
                # Debug: original distance reported by Google (may differ)
                'original_total_dist': route_dist_meters,
            }
        print(f"Failed to generate a valid route after {max_attempts} attempts (level={level}).")
        return None

    def get_map_base64(self, route_poly, waypoints):
        """Generates 2D map and returns base64 string."""
        base_url = "https://maps.googleapis.com/maps/api/staticmap"
        styles = ["feature:all|element:labels|visibility:off"]
        
        params = {
            "size": "640x640",
            "scale": "2",
            "key": self.api_key,
            "style": styles,
        }
        
        query_parts = [
            f"path=color:0x0000ff|weight:5|enc:{polyline.encode(route_poly)}"
        ]
        
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        
        # Start (A)
        start = waypoints[0]
        query_parts.append(f"markers=color:green|label:A|{start[0]},{start[1]}")
        
        # Intermediates
        for i, pt in enumerate(waypoints[1:-1]):
            label_idx = i + 1 
            label = labels[label_idx] if label_idx < len(labels) else "W"
            query_parts.append(f"markers=color:orange|label:{label}|{pt[0]},{pt[1]}")
            
        # End (Z)
        end = waypoints[-1]
        last_label_idx = len(waypoints) - 1
        last_label = labels[last_label_idx] if last_label_idx < len(labels) else "Z"
        query_parts.append(f"markers=color:red|label:{last_label}|{end[0]},{end[1]}")
        
        req = requests.PreparedRequest()
        req.prepare_url(base_url, params)
        full_url = req.url + "&" + "&".join(query_parts)
        
        try:
            r = requests.get(full_url, timeout=self.http_timeout)
            if r.status_code == 200:
                return base64.b64encode(r.content).decode('utf-8')
            self._handle_api_error(f"HTTP {r.status_code} fetching static map")
        except Exception as e:
            self._handle_api_error(e)
        return None

    def get_view_base64(self, lat, lon, heading):
        """Generates Street View and returns base64 string."""
        base_url = "https://maps.googleapis.com/maps/api/streetview"
        params = {
            "size": "640x400",
            "location": f"{lat},{lon}",
            "heading": heading,
            "pitch": 0,
            "fov": 90,
            "source": "outdoor",
            "key": self.api_key
        }
        
        try:
            r = requests.get(base_url, params=params, timeout=self.http_timeout)
            if r.status_code == 200:
                return base64.b64encode(r.content).decode('utf-8')
            self._handle_api_error(f"HTTP {r.status_code} fetching street view")
        except Exception as e:
            self._handle_api_error(e)
        return None

    def get_final_map_base64(self, route_poly, waypoints, walked_path):
        """Generates 2D map with both reference route and walked path."""
        base_url = "https://maps.googleapis.com/maps/api/staticmap"
        styles = ["feature:all|element:labels|visibility:off"]
        
        params = {
            "size": "640x640",
            "scale": "2",
            "key": self.api_key,
            "style": styles,
        }
        
        query_parts = []
        
        # 1. Reference Route (Blue)
        query_parts.append(f"path=color:0x0000ff|weight:5|enc:{polyline.encode(route_poly)}")
        
        # 2. Walked Path (Magenta)
        if len(walked_path) > 1:
            query_parts.append(f"path=color:0xFF00FF|weight:4|enc:{polyline.encode(walked_path)}")
        
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        
        # Start (A)
        start = waypoints[0]
        query_parts.append(f"markers=color:green|label:A|{start[0]},{start[1]}")
        
        # Intermediates
        for i, pt in enumerate(waypoints[1:-1]):
            label_idx = i + 1 
            label = labels[label_idx] if label_idx < len(labels) else "W"
            query_parts.append(f"markers=color:orange|label:{label}|{pt[0]},{pt[1]}")
            
        # End (Z)
        end = waypoints[-1]
        last_label_idx = len(waypoints) - 1
        last_label = labels[last_label_idx] if last_label_idx < len(labels) else "Z"
        query_parts.append(f"markers=color:red|label:{last_label}|{end[0]},{end[1]}")
        
        req = requests.PreparedRequest()
        req.prepare_url(base_url, params)
        full_url = req.url + "&" + "&".join(query_parts)
        
        try:
            r = requests.get(full_url, timeout=self.http_timeout)
            if r.status_code == 200:
                return base64.b64encode(r.content).decode('utf-8')
            self._handle_api_error(f"HTTP {r.status_code} fetching final static map")
        except Exception as e:
            self._handle_api_error(e)
        return None

    def calculate_final_score(self, walked_path, route_poly):
        if not walked_path or not route_poly:
            return 0, 0, 0, 0, 0
            
        # 1. DESTINATION CHECK
        final_pos = walked_path[-1]
        target_pos = route_poly[-1]
        dist_to_finish = haversine_distance(final_pos[0], final_pos[1], 
                                          target_pos[0], target_pos[1])
        dest_score = 30 if dist_to_finish < 50 else 0
        
        # 2. ROUTE SIMILARITY
        total_error = 0
        for wp in walked_path:
            min_dist = float('inf')
            for i in range(len(route_poly) - 1):
                p1 = route_poly[i]
                p2 = route_poly[i+1]
                d = point_to_segment_distance(wp[0], wp[1], p1[0], p1[1], p2[0], p2[1])
                if d < min_dist: min_dist = d
            total_error += min_dist
            
        avg_deviation = total_error / len(walked_path)
        base_similarity_score = 70 * max(0, (1 - (avg_deviation / 100.0)))

        # 3. PROGRESS PENALTY (Projection Method)
        best_proj_dist = float('inf')
        best_seg_idx = -1
        best_t = 0.0
        
        final_lat, final_lon = walked_path[-1]

        for i in range(len(route_poly) - 1):
            p1 = route_poly[i]
            p2 = route_poly[i+1]
            
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            if dx == 0 and dy == 0: t = 0
            else:
                t = ((final_lat - p1[0]) * dx + (final_lon - p1[1]) * dy) / (dx*dx + dy*dy)
                t = max(0, min(1, t))
            
            nx = p1[0] + t * dx
            ny = p1[1] + t * dy
            d = haversine_distance(final_lat, final_lon, nx, ny)
            if d < best_proj_dist:
                best_proj_dist = d
                best_seg_idx = i
                best_t = t
        
        covered_distance = 0
        total_distance = 0
        for i in range(len(route_poly) - 1):
            p1 = route_poly[i]
            p2 = route_poly[i+1]
            seg_len = haversine_distance(p1[0], p1[1], p2[0], p2[1])
            
            if i < best_seg_idx:
                covered_distance += seg_len
            elif i == best_seg_idx:
                covered_distance += seg_len * best_t
            
            total_distance += seg_len
            
        if total_distance == 0: total_distance = 1
        progress_ratio = covered_distance / total_distance
        
        # Anti-Exploit: 0 progress if barely moved
        start_pos = route_poly[0]
        if haversine_distance(final_lat, final_lon, start_pos[0], start_pos[1]) < 5:
            progress_ratio = 0.0

        if dest_score > 0: progress_ratio = 1.0

        final_similarity_score = base_similarity_score * progress_ratio
        total_score = dest_score + final_similarity_score
        
        return total_score, dest_score, final_similarity_score, dist_to_finish, avg_deviation

