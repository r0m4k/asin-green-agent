import math
import os

# ==========================================
# CONFIGURATION
# ==========================================
# IMPORTANT (Competition/Security):
# Never hardcode API keys in the repository. The Green Agent should be configured
# via environment variables (e.g., GOOGLE_MAPS_API_KEY) by the runner/platform.

# Movement settings
STEP_SIZE_METERS = 15  # Distance moved per "move forward" command
# SAFE MANHATTAN BOUNDS (Chelsea to UES/UWS, avoiding rivers)
NYC_BOUNDS = {
    "lat_min": 40.7300, "lat_max": 40.7900,
    "lon_min": -74.0000, "lon_max": -73.9600
}

# Level Configuration
LEVEL_CONFIG = {
    1:  {"dist": 200,  "waypoints": 0, "weight": 1},
    2:  {"dist": 400,  "waypoints": 0, "weight": 2},
    3:  {"dist": 600,  "waypoints": 0, "weight": 3},
    4:  {"dist": 800,  "waypoints": 0, "weight": 4},
    5:  {"dist": 1000, "waypoints": 0, "weight": 5},
    6:  {"dist": 1200, "waypoints": 1, "weight": 6},
    7:  {"dist": 1500, "waypoints": 1, "weight": 7},
    8:  {"dist": 1800, "waypoints": 1, "weight": 8},
    9:  {"dist": 2000, "waypoints": 1, "weight": 9},
    10: {"dist": 2500, "waypoints": 1, "weight": 10},
}

# ==========================================
# GEOMETRY HELPERS
# ==========================================
def haversine_distance(lat1, lon1, lat2, lon2):
    """Returns distance in meters between two lat/lon points."""
    R = 6371000  # Radius of Earth in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def point_to_segment_distance(px, py, x1, y1, x2, y2):
    """
    Calculates min distance from point (px, py) to line segment (x1, y1)-(x2, y2).
    """
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return haversine_distance(px, py, x1, y1)

    # Project point onto line (parameter t)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx*dx + dy*dy)
    
    # Clamp t to segment [0, 1]
    t = max(0, min(1, t))
    
    # Nearest point on segment
    nx = x1 + t * dx
    ny = y1 + t * dy
    
    return haversine_distance(px, py, nx, ny)

def get_destination_point(lat, lon, bearing, distance_meters):
    """Calculates new lat/lon given a starting point, bearing, and distance."""
    R = 6378.1 # Radius of the Earth in km
    brng = math.radians(bearing)
    d = distance_meters / 1000.0 # Distance in km

    lat1 = math.radians(lat)
    lon1 = math.radians(lon)

    lat2 = math.asin(math.sin(lat1) * math.cos(d / R) +
                     math.cos(lat1) * math.sin(d / R) * math.cos(brng))
    lon2 = lon1 + math.atan2(math.sin(brng) * math.sin(d / R) * math.cos(lat1),
                             math.cos(d / R) - math.sin(lat1) * math.sin(lat2))

    return math.degrees(lat2), math.degrees(lon2)

def calculate_initial_bearing(start_lat, start_lon, end_lat, end_lon):
    """Calculates bearing between two points."""
    start_lat = math.radians(start_lat)
    start_lon = math.radians(start_lon)
    end_lat = math.radians(end_lat)
    end_lon = math.radians(end_lon)

    d_lon = end_lon - start_lon
    x = math.sin(d_lon) * math.cos(end_lat)
    y = math.cos(start_lat) * math.sin(end_lat) - (math.sin(start_lat) * math.cos(end_lat) * math.cos(d_lon))
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    return (initial_bearing + 360) % 360

