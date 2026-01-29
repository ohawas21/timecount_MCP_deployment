"""
Employee MCP Server - SSE Version for Cloud Deployment
This version uses Server-Sent Events (SSE) for cloud hosting on Render.com

Author: Your deployment package
FastMCP Version: 2.11.3+
"""

import json
import os
import asyncio
import httpx
import logging
from pathlib import Path
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================
# Sets up logging so we can see what's happening in the Render logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION FROM ENVIRONMENT VARIABLES
# =============================================================================
# IMPORTANT: API_TOKEN comes from environment variable (set in Render)
# This keeps it secret and out of the code!

BASE_URL = os.environ.get("BASE_URL", "https://tutorial.formatgold.de/api")
API_TOKEN = os.environ.get("API_TOKEN")  # Will be set in Render dashboard
PORT = int(os.environ.get("PORT", 8888))

# Check that API_TOKEN exists - fail early if not set
if not API_TOKEN:
    logger.error("❌ API_TOKEN environment variable is required!")
    raise ValueError("API_TOKEN environment variable must be set")

logger.info(f"✅ Configuration loaded - API Base URL: {BASE_URL}")

# =============================================================================
# GLOBAL VARIABLES
# =============================================================================
# These will be initialized when the server starts
mcp_server = None
http_client = None

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def load_openapi_spec() -> dict:
    """
    Load the OpenAPI specification from the JSON file.
    This file describes all the employee API endpoints.
    """
    try:
        # Get the directory where this script is located
        script_dir = Path(__file__).parent
        schema_path = script_dir / "Employee_schema_3.0.json"
        
        # Try alternative naming if not found
        if not schema_path.exists():
            schema_path = script_dir / "Employee_schema_3_0.json"
        
        if not schema_path.exists():
            raise FileNotFoundError(
                f"❌ OpenAPI schema not found at {schema_path}"
            )
        
        with open(schema_path, "r", encoding="utf-8") as f:
            spec = json.load(f)
            logger.info(f"✅ OpenAPI spec loaded successfully")
            return spec
            
    except Exception as e:
        logger.error(f"❌ Error loading OpenAPI spec: {e}")
        raise

def make_async_client() -> httpx.AsyncClient:
    """
    Create an async HTTP client that will make requests to the Timecount API.
    It's configured with:
    - Base URL: so we don't repeat it in every request
    - Authorization header: with your API token
    - Timeout: 30 seconds for requests
    """
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )

# =============================================================================
# LIFECYCLE HANDLERS
# =============================================================================

async def startup():
    """
    Called when the server starts up.
    This initializes the MCP server and HTTP client.
    """
    global mcp_server, http_client
    
    logger.info("🚀 Starting Employee MCP Server...")
    logger.info(f"📡 Port: {PORT}")
    logger.info(f"🌐 API Base URL: {BASE_URL}")
    
    try:
        # Load the OpenAPI specification
        openapi_spec = load_openapi_spec()
        
        # Create HTTP client for making API requests
        http_client = make_async_client()
        logger.info("✅ HTTP client created")
        
        # Create the MCP server from OpenAPI spec
        mcp_server = FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=http_client,
            timeout=30.0,
        )
        logger.info("✅ FastMCP server initialized")
        
        # Test the API connection
        try:
            test_response = await http_client.get(
                "/employees",
                params={"filter[employee_visibility]": "all"}
            )
            if test_response.status_code == 200:
                logger.info("✅ API connection test successful")
            else:
                logger.warning(f"⚠️  API test returned status {test_response.status_code}")
        except Exception as e:
            logger.error(f"❌ API connection test failed: {e}")
            logger.warning("⚠️  Server will start anyway, but API calls may fail")
        
        logger.info("=" * 60)
        logger.info("🎉 Server is ready to accept connections!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Error during startup: {e}")
        raise

async def shutdown():
    """
    Called when the server shuts down.
    This cleans up resources properly.
    """
    global http_client
    
    logger.info("🛑 Shutting down server...")
    
    if http_client:
        await http_client.aclose()
        logger.info("✅ HTTP client closed")
    
    logger.info("👋 Shutdown complete")

# =============================================================================
# ROUTE HANDLERS
# =============================================================================

async def root_endpoint(request):
    """
    Root endpoint (/) - Shows basic info about the service.
    Helpful for checking if the server is running.
    """
    return JSONResponse({
        "status": "running",
        "service": "Employee MCP Server",
        "version": "1.0.0",
        "transport": "SSE",
        "endpoints": {
            "root": "/",
            "health": "/health",
            "sse": "/sse"
        },
        "documentation": "This is an MCP server for Timecount Employee API"
    })

async def health_check(request):
    """
    Health check endpoint - Render uses this to verify the app is healthy.
    Returns detailed status information.
    """
    try:
        # Check if MCP server is initialized
        mcp_initialized = mcp_server is not None
        
        # Try to ping the API
        api_healthy = False
        if http_client:
            try:
                response = await http_client.get(
                    "/employees",
                    params={"filter[employee_visibility]": "all"}
                )
                api_healthy = response.status_code == 200
            except Exception as e:
                logger.error(f"Health check API test failed: {e}")
        
        # Overall health status
        is_healthy = mcp_initialized and api_healthy
        
        return JSONResponse({
            "status": "healthy" if is_healthy else "degraded",
            "mcp_server": "initialized" if mcp_initialized else "not initialized",
            "api_connection": "ok" if api_healthy else "failed",
            "timestamp": asyncio.get_event_loop().time()
        })
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return JSONResponse({
            "status": "unhealthy",
            "error": str(e)
        }, status_code=503)

async def sse_endpoint(request):
    """
    SSE endpoint for MCP communication.
    This is where Claude Desktop will connect to communicate with your server.
    
    SSE (Server-Sent Events) allows the server to push updates to the client
    over a single HTTP connection.
    """
    if not mcp_server:
        logger.error("SSE endpoint called but MCP server not initialized")
        return JSONResponse(
            {"error": "MCP server not initialized"},
            status_code=503
        )
    
    logger.info("📡 New SSE connection established")
    
    async def event_generator():
        """
        Generates Server-Sent Events for the MCP protocol.
        This keeps the connection alive and handles MCP messages.
        """
        try:
            # Send initial connection message
            logger.info("Sending initial SSE connection message")
            yield {
                "event": "message",
                "data": json.dumps({
                    "type": "connection",
                    "status": "connected",
                    "server": "Employee MCP Server"
                })
            }
            
            # Keep connection alive with periodic pings
            # This prevents timeouts and shows the connection is active
            while True:
                await asyncio.sleep(30)  # Ping every 30 seconds
                yield {
                    "event": "ping",
                    "data": json.dumps({"type": "ping", "timestamp": asyncio.get_event_loop().time()})
                }
                logger.debug("Sent keepalive ping")
                
        except asyncio.CancelledError:
            logger.info("SSE connection closed by client")
            raise
        except Exception as e:
            logger.error(f"Error in SSE event generator: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"type": "error", "message": str(e)})
            }
    
    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable buffering in nginx
        }
    )

# =============================================================================
# APPLICATION SETUP
# =============================================================================

# Create the Starlette application
app = Starlette(
    debug=False,  # Set to False for production
    routes=[
        Route('/', root_endpoint),           # Root info endpoint
        Route('/health', health_check),      # Health check for Render
        Route('/sse', sse_endpoint),         # SSE endpoint for MCP
    ],
    on_startup=[startup],    # Run startup() when server starts
    on_shutdown=[shutdown],  # Run shutdown() when server stops
)

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 60)
    logger.info("Starting Employee MCP Server")
    logger.info(f"Listening on http://0.0.0.0:{PORT}")
    logger.info("=" * 60)
    
    # Start the server
    uvicorn.run(
        app,
        host="0.0.0.0",  # Listen on all network interfaces
        port=PORT,
        log_level="info",
        access_log=True,
    )
