#!/usr/bin/env python3
"""
Price Adjustment Agent - Main Flask Application
Modular version with webhook handling and multi-agent integration
"""

import logging
from flask import Flask, request, jsonify, render_template
from flask import redirect
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import asyncio
import threading
from typing import Dict, Any
from dotenv import load_dotenv

# Import DocuSign MCP Client
from docusign_mcp_client import DocuSignMCPClient

# Import application components
from database import get_database_instance
from processors.adjustment_processor import PriceAdjustmentProcessor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask application
app = Flask(__name__)
app.config['SECRET_KEY'] = 'price_adjustment_secret_key'

# Initialize WebSocket support for real-time UI updates
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize components - simple version
def initialize_app():
    """Initialize application components"""
    try:
        logger.info("✅ Initializing price adjustment app components...")
        
        # Get singleton database instance
        db = get_database_instance()
        logger.info("✅ Database initialized successfully")
        
        # Initialize processor
        processor = PriceAdjustmentProcessor(db)
        
        return db, processor
        
    except Exception as e:
        logger.error(f"❌ Application initialization failed: {e}")
        raise

# Global components
db, processor = initialize_app()

# Initialize DocuSign MCP Client
docusign_client = DocuSignMCPClient()

# Register the DocuSign IAM + CLM integration at /api/v1/docusign. Registration
# performs no network call; a token is minted on the first request to it, so a
# deployment without DocuSign credentials still starts.
try:
    from docusign_iam.api import iam_clm_bp
    app.register_blueprint(iam_clm_bp)
    logger.info("✅ DocuSign IAM + CLM API registered at /api/v1/docusign")
except Exception as e:
    logger.error(f"❌ Could not register DocuSign IAM + CLM API: {e}")

# Real-time update broadcast function
def broadcast_update(request_id: str, agent_name: str, status: str, data: Dict[Any, Any] = None):
    """Broadcast real-time updates to connected dashboard clients"""
    try:
        update_data = {
            'request_id': request_id,
            'agent': agent_name,
            'status': status,
            'data': data or {},
            'timestamp': datetime.utcnow().isoformat()
        }
        socketio.emit('workflow_update', update_data)
        logger.info(f"📡 Broadcasted update: {agent_name} -> {status}")
    except Exception as e:
        logger.error(f"❌ Failed to broadcast update: {e}")

# Set the broadcast callback on the database after defining the function
db.set_broadcast_callback(broadcast_update)
logger.info("✅ Broadcast callback configured for database")

@app.route('/webhook/manual', methods=['POST'])
def webhook_manual():
    """Manual trigger endpoint for price adjustment workflow"""
    try:
        logger.info("📞 Manual webhook triggered")
        
        # Create a manual trigger request
        request_data = {
            "trigger_type": "manual",
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": request.json.get("user_id", "system") if request.json else "system"
        }
        
        # Get user_id for database storage
        user_id = request_data["user_id"]
        
        # Store the trigger request in database
        request_id = f"manual_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        db.store_adjustment_request(request_id, request_data, "manual", user_id)
        
        # Process in background thread
        def run_async_process():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(processor.process_price_adjustment_request(request_id))
            except Exception as e:
                logger.error(f"❌ Background processing failed: {e}")
        
        thread = threading.Thread(target=run_async_process)
        thread.start()
        
        # Get user_id for response
        user_id = request_data.get("user_id", "system")
        
        return jsonify({
            "status": "accepted",
            "message": f"Price adjustment workflow initiated by {user_id}",
            "request_id": request_id,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }), 202
        
    except Exception as e:
        logger.error(f"❌ Manual webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/scheduled', methods=['POST'])
def webhook_scheduled():
    """Scheduled trigger endpoint (called by scheduler at 7 PM daily)"""
    try:
        logger.info("⏰ Scheduled webhook triggered")
        
        # Create a scheduled trigger request
        request_data = {
            "trigger_type": "scheduled",
            "timestamp": datetime.utcnow().isoformat(),
            "scheduled_time": "19:00"  # 7 PM
        }
        
        # Store the trigger request in database
        request_id = f"scheduled_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        db.store_adjustment_request(request_id, request_data, "scheduled", "scheduler")
        
        # Process in background thread
        def run_async_process():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(processor.process_price_adjustment_request(request_id))
            except Exception as e:
                logger.error(f"❌ Background processing failed: {e}")
        
        thread = threading.Thread(target=run_async_process)
        thread.start()
        
        return jsonify({
            "status": "accepted",
            "message": "Scheduled price adjustment workflow initiated",
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }), 202
        
    except Exception as e:
        logger.error(f"❌ Scheduled webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status/<request_id>', methods=['GET'])
def get_status(request_id):
    """Get status of a price adjustment request"""
    try:
        request_data = db.get_adjustment_request(request_id)
        if not request_data:
            return jsonify({"error": "Request not found"}), 404
        
        return jsonify({
            "request_id": request_id,
            "status": request_data["status"],
            "created_at": request_data["timestamp"],
            "completed_at": request_data.get("completed_at"),
            "results": json.loads(request_data["adjustment_results"]) if request_data.get("adjustment_results") else None
        })
        
    except Exception as e:
        logger.error(f"❌ Status check error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status/recent', methods=['GET'])
def get_recent_requests():
    """Get recent price adjustment requests"""
    try:
        recent_requests = db.get_recent_adjustment_requests()
        return jsonify({
            "requests": recent_requests,
            "count": len(recent_requests)
        })
        
    except Exception as e:
        logger.error(f"❌ Recent requests error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "price-adjustment-agent",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0"
    })

# ========================================
# DASHBOARD UI ENDPOINTS
# ========================================

# DocuSign MCP OAuth endpoints
@app.route('/docusign/oauth/start')
def docusign_oauth_start():
    """Redirect user to DocuSign OAuth consent page"""
    url = docusign_client.get_authorization_url()
    logger.info("🔗 THIS IS USING MCP")
    return redirect(url)

@app.route('/oauth/callback')
def docusign_oauth_callback():
    """Handle DocuSign OAuth callback and fetch token"""
    code = request.args.get('code')
    if not code:
        return "Missing authorization code", 400
    try:
        tokens = docusign_client.fetch_token(code)
        # Return a friendly HTML response (minimal) and log success
        logger.info("✅ DocuSign OAuth completed and tokens saved to disk")
        try:
            return render_template('oauth_success.html', message='DocuSign OAuth completed successfully')
        except Exception:
            # If template rendering fails for any reason, return a minimal text response
            return 'DocuSign OAuth completed successfully - tokens saved', 200
    except Exception as e:
        logger.error(f"❌ DocuSign OAuth error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/docusign/token_status')
def docusign_token_status():
    """Return basic token status for debug (do NOT expose in prod)"""
    try:
        token_info = {
            'has_access_token': bool(docusign_client.access_token),
            'has_refresh_token': bool(docusign_client.refresh_token)
        }
        return jsonify(token_info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/docusign/discover')
def docusign_discover():
    """Call MCP server root to discover available tools and metadata (requires OAuth)."""
    try:
        info = docusign_client.get_server_info()
        return jsonify(info)
    except Exception as e:
        logger.error(f"❌ MCP discovery failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/')
@app.route('/dashboard')
def dashboard():
    """Serve the main dashboard UI"""
    try:
        return render_template('dashboard.html')
    except Exception as e:
        logger.error(f"❌ Dashboard error: {e}")
        return f"Dashboard temporarily unavailable: {e}", 500

@app.route('/api/requests/active', methods=['GET'])
def get_active_requests():
    """Get currently active/processing requests for dashboard"""
    try:
        active_requests = db.get_active_requests()
        return jsonify({
            "active_requests": active_requests,
            "count": len(active_requests)
        })
    except Exception as e:
        logger.error(f"❌ Failed to get active requests: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/requests/<request_id>/details', methods=['GET'])
def get_request_details(request_id):
    """Get detailed information about a specific request"""
    try:
        request_data = db.get_adjustment_request(request_id)
        if not request_data:
            return jsonify({"error": "Request not found"}), 404
        
        agent_logs = db.get_agent_logs(request_id)
        
        return jsonify({
            "request": request_data,
            "agent_logs": agent_logs,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Failed to get request details: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_dashboard_stats():
    """Get dashboard statistics"""
    try:
        stats = db.get_dashboard_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"❌ Failed to get dashboard stats: {e}")
        return jsonify({"error": str(e)}), 500

# ========================================
# WEBSOCKET HANDLERS
# ========================================

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket client connections"""
    logger.info("🔌 Dashboard client connected")
    emit('connected', {
        'message': 'Connected to Price Adjustment Agent Dashboard',
        'timestamp': datetime.utcnow().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket client disconnections"""
    logger.info("🔌 Dashboard client disconnected")

@socketio.on('request_status')
def handle_status_request(data):
    """Handle real-time status requests from dashboard"""
    try:
        request_id = data.get('request_id')
        if request_id:
            request_data = db.get_adjustment_request(request_id)
            agent_logs = db.get_agent_logs(request_id)
            
            emit('status_response', {
                'request_id': request_id,
                'request_data': request_data,
                'agent_logs': agent_logs,
                'timestamp': datetime.utcnow().isoformat()
            })
    except Exception as e:
        logger.error(f"❌ Status request error: {e}")
        emit('error', {'message': str(e)})

if __name__ == '__main__':
    from config import Config
    
    # Validate configuration
    Config.validate()
    
    # Ready to start server
    
    logger.info("🚀 Starting Price Adjustment Agent Flask server...")
    logger.info(f"🌐 Server will run on {Config.HOST}:{Config.PORT}")
    logger.info(f"🔧 Debug mode: {Config.DEBUG}")
    
    # Use socketio.run instead of app.run for WebSocket support
    socketio.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True  # Allow for development
    )
