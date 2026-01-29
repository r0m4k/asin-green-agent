import argparse
import os
import json
from src.agent import ASINGreenAgent

# NOTE: In production AgentBeats environment, the agent card is usually mounted or provided.
# However, to ensure reliability across environments, we load it if present.

def create_app(host: str, port: int, card_url: str | None):
    agent = ASINGreenAgent(agent_host=host, agent_port=port)
    
    # Load Agent Card if available
    card_path = "agent-card.json"
    if os.path.exists(card_path):
        try:
            with open(card_path, "r") as f:
                agent.agent_card_json = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load agent-card.json: {e}")

    # Override advertised URL if provided by the runner
    if agent.agent_card_json and card_url:
        agent.agent_card_json["url"] = card_url
    
    # Initialize the AgentBeats app structure
    if hasattr(agent, "_make_app"):
        try:
            agent._make_app()
        except Exception as e:
            print(f"Warning: _make_app failed: {e}")
            
    # Get the ASGI app
    app = agent.get_app()
    if app is None:
        app = agent.app
        
    return app, agent

app = None
agent = None

def main():
    parser = argparse.ArgumentParser(description="Run the ASIN green agent (A2A server).")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9009, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, default=None, help="URL to advertise in the agent card")
    args = parser.parse_args()

    # Create app instance for Uvicorn to run
    global app, agent
    app, agent = create_app(args.host, args.port, args.card_url)

    import uvicorn
    print("Starting ASIN Green Agent Server...")
    uvicorn.run(app, host=args.host, port=args.port, timeout_keep_alive=300)

# Manually mount A2A endpoints to ensure compatibility if auto-discovery fails
from starlette.responses import JSONResponse
from starlette.requests import Request

async def start_endpoint(request: Request):
    data = await request.json()
    # AgentBeats might wrap this, but we call our agent directly for reliability
    res = agent.start(data.get("session_id"), data.get("task_config"))
    return JSONResponse(res)

async def act_endpoint(request: Request):
    data = await request.json()
    res = agent.act(data.get("session_id"), data.get("action"))
    return JSONResponse(res)

async def result_endpoint(request: Request):
    data = await request.json()
    res = agent.result(data.get("session_id"))
    return JSONResponse(res)

if app:
    # Use Starlette routing which works on both FastAPI and Starlette apps
    app.add_route("/start", start_endpoint, methods=["POST"])
    app.add_route("/act", act_endpoint, methods=["POST"])
    app.add_route("/result", result_endpoint, methods=["POST"])

if __name__ == "__main__":
    main()
