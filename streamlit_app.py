"""
DocuSign MCP Tools - Streamlit Interface
A user-friendly web interface for interacting with DocuSign MCP tools.
"""

import streamlit as st
import json
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List
import os
import pandas as pd
from datetime import datetime

# Page configuration
st.set_page_config(
    page_title="DocuSign MCP Tools",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Constants
TOKEN_FILE = ".mcp_tokens.json"
MCP_SERVER_URL = os.getenv("DOCUSIGN_MCP_BASE_URL", "https://services.demo.docusign.net/docusign-mcp-server/v1.0/mcp")
ACCOUNT_ID = os.getenv("DOCUSIGN_ACCOUNT_ID", "999fac92-647f-4471-a16f-51f38abf2d83")
WORKFLOW_ID = os.getenv("DOCUSIGN_WORKFLOW_ID", "1fc6d7e9-613b-4843-8d79-29bbb07c015d")

# Tool categories and descriptions
TOOL_CATEGORIES = {
    "📋 Agreement Management": [
        ("getAllAgreements", "List all agreements in the account"),
        ("getAgreementDetails", "Get details of a specific agreement")
    ],
    "✉️ Envelope Management": [
        ("getEnvelopes", "Search and list envelopes"),
        ("getEnvelope", "Get details of a specific envelope"),
        ("createEnvelope", "Create a new envelope (draft or sent)"),
        ("updateEnvelope", "Update an existing envelope"),
        ("listRecipients", "Get recipients of an envelope")
    ],
    "🔄 Maestro Workflows": [
        ("getWorkflowsList", "List all available workflows"),
        ("getWorkflowTriggerRequirements", "Get trigger requirements for a workflow"),
        ("triggerWorkflow", "Trigger a workflow instance"),
        ("getWorkflowInstancesList", "List workflow instances"),
        ("getWorkflowInstance", "Get details of a workflow instance"),
        ("cancelWorkflowInstance", "Cancel a workflow instance"),
        ("pauseNewWorkflowInstances", "Pause new workflow instances"),
        ("resumeWorkflow", "Resume a paused workflow")
    ],
    "👤 Account & Users": [
        ("getAccount", "Get account information"),
        ("getUsers", "List users in the account"),
        ("getUser", "Get details of a specific user")
    ],
    "🎨 Branding": [
        ("getBrands", "List all brands"),
        ("getBrand", "Get details of a specific brand")
    ],
    "📑 Templates": [
        ("getTemplates", "List all templates")
    ],
    "🔗 Connected Fields": [
        ("getTabGroups", "Get tab groups for Connected Fields")
    ],
    "💳 Billing": [
        ("listBillingPlans", "List billing plans"),
        ("getBillingPlan", "Get details of a specific billing plan")
    ],
    "🤖 AI Assistant": [
        ("queryRAG", "Ask DocuSign AI assistant a question")
    ]
}

def load_token() -> Optional[str]:
    """Load the OAuth access token from the token file."""
    try:
        token_path = Path(TOKEN_FILE)
        if not token_path.exists():
            return None
        
        with open(token_path, 'r') as f:
            token_data = json.load(f)
            return token_data.get('access_token')
    except Exception as e:
        st.error(f"Error loading token: {str(e)}")
        return None

def call_mcp_tool(tool_name: str, params: Dict[str, Any]) -> Optional[Dict]:
    """Call an MCP tool and return the response."""
    token = load_token()
    if not token:
        st.error("No valid OAuth token found. Please authenticate first.")
        return None
    
    # Prepare the JSON-RPC request
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {
                "params": params
            }
        },
        "id": 1
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    
    try:
        with st.spinner(f"Calling {tool_name}..."):
            response = requests.post(MCP_SERVER_URL, json=payload, headers=headers)
            response.raise_for_status()
            
            # Parse SSE response
            response_text = response.text
            if response_text.startswith("event: message\ndata: "):
                json_str = response_text.replace("event: message\ndata: ", "").strip()
                result = json.loads(json_str)
                
                if "result" in result:
                    content = result["result"].get("content", [])
                    if content and len(content) > 0:
                        text_content = content[0].get("text", "")
                        return json.loads(text_content) if text_content else None
            
            return None
            
    except requests.exceptions.RequestException as e:
        st.error(f"API Error: {str(e)}")
        return None
    except json.JSONDecodeError as e:
        st.error(f"JSON parsing error: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {str(e)}")
        return None

def format_timestamp(timestamp_str: str) -> str:
    """Format ISO timestamp to readable format."""
    try:
        if timestamp_str:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except:
        pass
    return timestamp_str

def display_workflow_result(result: Dict[str, Any]):
    """Display workflow data in a readable format."""
    if "workflows" in result:
        workflows_data = result["workflows"]
        if "workflows" in workflows_data and workflows_data["workflows"]:
            workflows_list = workflows_data["workflows"]
            
            st.subheader(f"📊 Found {len(workflows_list)} Workflow(s)")
            
            for idx, workflow in enumerate(workflows_list, 1):
                st.markdown(f"### 🔄 {workflow.get('name', 'Unnamed Workflow')}")
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**Workflow Details**")
                    st.write(f"**ID:** `{workflow.get('id', 'N/A')}`")
                    st.write(f"**Status:** {workflow.get('status', 'N/A').upper()}")
                    
                with col2:
                    st.markdown("**Metadata**")
                    if "metadata" in workflow:
                        created = format_timestamp(workflow["metadata"].get("created_at", ""))
                        modified = format_timestamp(workflow["metadata"].get("modified_at", ""))
                        st.write(f"**Created:** {created}")
                        st.write(f"**Modified:** {modified}")
                
                if idx < len(workflows_list):
                    st.markdown("---")
        else:
            st.info("No workflows found.")
    else:
        st.json(result)

def display_envelope_result(result: Dict[str, Any]):
    """Display envelope data in a readable format."""
    if "envelopes" in result:
        envelopes = result["envelopes"]
        st.subheader(f"📧 Found {len(envelopes)} Envelope(s)")
        
        # Create a dataframe for table view
        if envelopes:
            df_data = []
            for env in envelopes:
                df_data.append({
                    "Envelope ID": env.get("envelopeId", "N/A")[:20] + "...",
                    "Subject": env.get("emailSubject", "N/A")[:40],
                    "Status": env.get("status", "N/A").upper(),
                    "Sent": format_timestamp(env.get("sentDateTime", "")),
                    "Recipients": len(env.get("recipients", {}).get("signers", []))
                })
            
            df = pd.DataFrame(df_data)
            st.dataframe(df, use_container_width=True)
            
            # Detailed view - show cards instead of nested expanders
            if len(envelopes) <= 5:  # Only show detailed view for small result sets
                st.markdown("---")
                st.subheader("Detailed View")
                for idx, env in enumerate(envelopes, 1):
                    st.markdown(f"### ✉️ {env.get('emailSubject', 'No Subject')}")
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.metric("Status", env.get("status", "N/A").upper())
                    with col2:
                        signers = len(env.get("recipients", {}).get("signers", []))
                        st.metric("Signers", signers)
                    with col3:
                        docs = len(env.get("documents", []))
                        st.metric("Documents", docs)
                    
                    st.write(f"**Envelope ID:** `{env.get('envelopeId', 'N/A')}`")
                    st.write(f"**Sent:** {format_timestamp(env.get('sentDateTime', 'N/A'))}")
                    
                    if idx < len(envelopes):
                        st.markdown("---")
        else:
            st.info("No envelopes found.")
    else:
        st.json(result)

def display_agreement_result(result: Dict[str, Any]):
    """Display agreement data in a readable format."""
    if "agreements" in result:
        agreements = result["agreements"]
        total_count = result.get("totalCount", len(agreements))
        
        st.subheader(f"📋 Found {total_count} Agreement(s)")
        
        if agreements:
            # Create table view
            df_data = []
            for agr in agreements:
                df_data.append({
                    "Agreement ID": agr.get("agreementId", "N/A")[:20] + "...",
                    "Name": agr.get("name", "N/A")[:40],
                    "Status": agr.get("status", "N/A").upper(),
                    "Created": format_timestamp(agr.get("createdDate", ""))
                })
            
            df = pd.DataFrame(df_data)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No agreements found in this account.")
            
        # Show metadata without nested expander
        if "metadata" in result:
            st.markdown("---")
            st.markdown("**📊 Metadata**")
            meta = result["metadata"]
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Strategy", meta.get("strategy", "N/A"))
            with col2:
                st.metric("Truncated", "Yes" if meta.get("truncated") else "No")
            with col3:
                st.write(f"**Fetched:** {format_timestamp(meta.get('fetchedAt', ''))}")
    else:
        st.json(result)

def display_user_result(result: Dict[str, Any]):
    """Display user data in a readable format."""
    if "users" in result:
        users = result["users"]
        st.subheader(f"👥 Found {len(users)} User(s)")
        
        if users:
            for idx, user in enumerate(users, 1):
                st.markdown(f"### 👤 {user.get('userName', 'Unknown User')}")
                col1, col2 = st.columns(2)
                
                with col1:
                    st.write(f"**Name:** {user.get('firstName', '')} {user.get('lastName', '')}")
                    st.write(f"**Email:** {user.get('email', 'N/A')}")
                    st.write(f"**User ID:** `{user.get('userId', 'N/A')}`")
                    
                with col2:
                    st.write(f"**Status:** {user.get('userStatus', 'N/A')}")
                    st.write(f"**Type:** {user.get('userType', 'N/A')}")
                    st.write(f"**Created:** {format_timestamp(user.get('createdDateTime', ''))}")
                
                if idx < len(users):
                    st.markdown("---")
        else:
            st.info("No users found.")
    elif "userName" in result:  # Single user
        st.subheader("👤 User Details")
        col1, col2 = st.columns(2)
        
        with col1:
            st.write(f"**Username:** {result.get('userName', 'N/A')}")
            st.write(f"**Name:** {result.get('firstName', '')} {result.get('lastName', '')}")
            st.write(f"**Email:** {result.get('email', 'N/A')}")
            
        with col2:
            st.write(f"**User ID:** `{result.get('userId', 'N/A')}`")
            st.write(f"**Status:** {result.get('userStatus', 'N/A')}")
            st.write(f"**Created:** {format_timestamp(result.get('createdDateTime', ''))}")
    else:
        st.json(result)

def display_rag_result(result: Dict[str, Any]):
    """Display RAG query results in a readable format."""
    if "results" in result:
        results = result["results"]
        st.subheader(f"🤖 AI Assistant Response ({len(results)} result(s))")
        
        for idx, res in enumerate(results, 1):
            st.markdown(f"### 📄 Result {idx}: {res.get('title', 'No Title')}")
            st.markdown(f"**Source:** [{res.get('title', 'N/A')}]({res.get('url', '#')})")
            st.markdown("---")
            st.markdown(res.get('content', 'No content available'))
            st.caption(f"Last updated: {format_timestamp(res.get('updatedAt', ''))}")
            
            if idx < len(results):
                st.markdown("---")
    else:
        st.json(result)

def display_workflow_instance_result(result: Dict[str, Any]):
    """Display workflow instance data in a readable format."""
    if "instances" in result:
        instances = result["instances"]
        st.subheader(f"🔄 Found {len(instances)} Workflow Instance(s)")
        
        if instances:
            for idx, inst in enumerate(instances, 1):
                status = inst.get("instanceState", "unknown")
                status_emoji = {
                    "in_progress": "🔄",
                    "completed": "✅",
                    "failed": "❌",
                    "paused": "⏸️"
                }.get(status.lower(), "❓")
                
                st.markdown(f"### {status_emoji} {inst.get('instanceName', 'Unnamed Instance')}")
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric("State", inst.get("instanceState", "N/A").upper())
                with col2:
                    st.metric("Started", format_timestamp(inst.get("startDate", "")))
                with col3:
                    if inst.get("lastUpdatedDate"):
                        st.metric("Last Updated", format_timestamp(inst.get("lastUpdatedDate", "")))
                
                st.write(f"**Instance ID:** `{inst.get('instanceId', 'N/A')}`")
                
                # Show trigger inputs if available
                if "triggerInputs" in inst:
                    st.markdown("**Trigger Inputs:**")
                    st.json(inst["triggerInputs"])
                
                if idx < len(instances):
                    st.markdown("---")
        else:
            st.info("No workflow instances found.")
    elif "instanceId" in result:  # Single instance
        status = result.get("instanceState", "unknown")
        status_emoji = {
            "in_progress": "🔄",
            "completed": "✅",
            "failed": "❌",
            "paused": "⏸️"
        }.get(status.lower(), "❓")
        
        st.subheader(f"{status_emoji} Workflow Instance Details")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("State", result.get("instanceState", "N/A").upper())
        with col2:
            st.metric("Started", format_timestamp(result.get("startDate", "")))
        with col3:
            if result.get("lastUpdatedDate"):
                st.metric("Last Updated", format_timestamp(result.get("lastUpdatedDate", "")))
        
        st.write(f"**Instance ID:** `{result.get('instanceId', 'N/A')}`")
        st.write(f"**Instance Name:** {result.get('instanceName', 'N/A')}")
        
        if "triggerInputs" in result:
            st.markdown("**Trigger Inputs:**")
            st.json(result["triggerInputs"])
    else:
        st.json(result)

def display_result(tool_name: str, result: Dict[str, Any]):
    """Display result in a formatted, user-friendly way based on tool type."""
    if not result:
        st.warning("No data returned from the API.")
        return
    
    st.success("✅ Tool executed successfully!")
    
    # Route to appropriate display function based on tool type
    if tool_name == "getWorkflowsList":
        display_workflow_result(result)
    elif tool_name in ["getEnvelopes", "getEnvelope"]:
        display_envelope_result(result)
    elif tool_name in ["getAllAgreements", "getAgreementDetails"]:
        display_agreement_result(result)
    elif tool_name in ["getUsers", "getUser"]:
        display_user_result(result)
    elif tool_name == "queryRAG":
        display_rag_result(result)
    elif tool_name in ["getWorkflowInstancesList", "getWorkflowInstance"]:
        display_workflow_instance_result(result)
    elif tool_name == "getAccount":
        st.subheader("🏢 Account Information")
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**Account Name:** {result.get('accountName', 'N/A')}")
            st.write(f"**Account ID:** `{result.get('accountId', 'N/A')}`")
            st.write(f"**Base URI:** {result.get('baseUri', 'N/A')}")
        with col2:
            st.write(f"**Plan:** {result.get('planName', 'N/A')}")
            st.write(f"**Created:** {format_timestamp(result.get('createdDate', ''))}")
        
        st.markdown("---")
        st.markdown("**🔍 Raw JSON Data**")
        st.json(result)
    else:
        # Generic display for other tools
        st.subheader("📊 Response Data")
        
        # Try to extract key metrics
        if isinstance(result, dict):
            # Look for common count fields
            for key in ["totalCount", "count", "resultSetSize"]:
                if key in result:
                    st.metric(key.replace("_", " ").title(), result[key])
        
        # Show formatted JSON
        st.markdown("**🔍 Full Response Data**")
        st.json(result)

def get_tool_params(tool_name: str, unique_suffix: str = "") -> Dict[str, Any]:
    """Get the parameter inputs for a specific tool based on the UI."""
    params = {}
    
    # If no suffix provided, use tool_name as suffix for backwards compatibility
    if not unique_suffix:
        unique_suffix = tool_name
    
    # Common parameters
    if tool_name in ["getAllAgreements", "getEnvelopes", "getWorkflowsList", "getWorkflowTriggerRequirements",
                     "getWorkflowInstancesList", "getAccount", "getUsers", "getBrands", "getTemplates",
                     "getTabGroups", "pauseNewWorkflowInstances", "resumeWorkflow", "getUser", "getBrand",
                     "getEnvelope", "listRecipients", "getAgreementDetails", "getWorkflowInstance", 
                     "cancelWorkflowInstance", "updateEnvelope"]:
        params["accountId"] = st.session_state.get("account_id", ACCOUNT_ID)
    
    # Workflow-specific parameters
    if tool_name in ["getWorkflowTriggerRequirements", "getWorkflowInstancesList", "pauseNewWorkflowInstances",
                     "resumeWorkflow", "triggerWorkflow"]:
        params["workflowId"] = st.session_state.get("workflow_id", WORKFLOW_ID)
    
    # Get values from session state (set by input widgets)
    # Instance-specific parameters
    if tool_name in ["getWorkflowInstance", "cancelWorkflowInstance"]:
        params["workflowId"] = st.session_state.get("workflow_id", WORKFLOW_ID)
        instance_id = st.session_state.get(f"instance_id_{unique_suffix}", "")
        if instance_id:
            params["instanceId"] = instance_id
    
    # User-specific parameters
    if tool_name == "getUser":
        user_id = st.session_state.get(f"user_id_{unique_suffix}", "")
        if user_id:
            params["userId"] = user_id
    
    # Brand-specific parameters
    if tool_name == "getBrand":
        brand_id = st.session_state.get(f"brand_id_{unique_suffix}", "")
        if brand_id:
            params["brandId"] = brand_id
    
    # Envelope-specific parameters
    if tool_name in ["getEnvelope", "listRecipients", "updateEnvelope"]:
        envelope_id = st.session_state.get(f"envelope_id_{unique_suffix}", "")
        if envelope_id:
            params["envelopeId"] = envelope_id
    
    # Agreement-specific parameters
    if tool_name == "getAgreementDetails":
        agreement_id = st.session_state.get(f"agreement_id_{unique_suffix}", "")
        if agreement_id:
            params["agreementId"] = agreement_id
    
    # Billing plan parameters
    if tool_name == "getBillingPlan":
        billing_plan_id = st.session_state.get(f"billing_plan_id_{unique_suffix}", "")
        if billing_plan_id:
            params["billingPlanId"] = billing_plan_id
    
    # RAG query parameters
    if tool_name == "queryRAG":
        prompt = st.session_state.get(f"rag_prompt_{unique_suffix}", "")
        if prompt:
            params["prompt"] = prompt
    
    # Trigger workflow parameters
    if tool_name == "triggerWorkflow":
        instance_name = st.session_state.get(f"instance_name_{unique_suffix}", "")
        start_date = st.session_state.get(f"start_date_{unique_suffix}", "")
        total_value = st.session_state.get(f"total_value_{unique_suffix}", "")
        expiry_date = st.session_state.get(f"expiry_date_{unique_suffix}", "")
        
        trigger_inputs = {}
        if start_date:
            trigger_inputs["startDate"] = start_date
            trigger_inputs["Start_Date"] = start_date
        if total_value:
            trigger_inputs["Total Value"] = total_value
        if expiry_date:
            trigger_inputs["Expiry date"] = expiry_date
        
        if trigger_inputs:
            params["trigger_inputs"] = trigger_inputs
        if instance_name:
            params["instance_name"] = instance_name
    
    return params

def render_tool_inputs(tool_name: str, unique_suffix: str):
    """Render input widgets for a specific tool."""
    # Instance-specific parameters
    if tool_name in ["getWorkflowInstance", "cancelWorkflowInstance"]:
        st.text_input("Instance ID", key=f"instance_id_{unique_suffix}", 
                     help="Enter the workflow instance ID")
    
    # User-specific parameters
    if tool_name == "getUser":
        st.text_input("User ID", key=f"user_id_{unique_suffix}",
                     help="Enter the user ID")
    
    # Brand-specific parameters
    if tool_name == "getBrand":
        st.text_input("Brand ID", key=f"brand_id_{unique_suffix}",
                     help="Enter the brand ID")
    
    # Envelope-specific parameters
    if tool_name in ["getEnvelope", "listRecipients", "updateEnvelope"]:
        st.text_input("Envelope ID", key=f"envelope_id_{unique_suffix}",
                     help="Enter the envelope ID (required)")
    
    # Agreement-specific parameters
    if tool_name == "getAgreementDetails":
        st.text_input("Agreement ID", key=f"agreement_id_{unique_suffix}",
                     help="Enter the agreement ID")
    
    # Billing plan parameters
    if tool_name == "getBillingPlan":
        st.text_input("Billing Plan ID", key=f"billing_plan_id_{unique_suffix}",
                     help="Enter the billing plan ID")
    
    # RAG query parameters
    if tool_name == "queryRAG":
        st.text_area("Ask a question", 
                    placeholder="How do I create an envelope with DocuSign?",
                    key=f"rag_prompt_{unique_suffix}",
                    help="Enter your question about DocuSign")
    
    # Trigger workflow parameters
    if tool_name == "triggerWorkflow":
        st.subheader("Workflow Trigger Inputs")
        st.info("For Price Adjustment workflow, provide the following inputs:")
        
        st.text_input("Instance Name (optional)", key=f"instance_name_{unique_suffix}")
        st.text_input("Start Date", key=f"start_date_{unique_suffix}", 
                     placeholder="YYYY-MM-DD")
        st.text_input("Total Value", key=f"total_value_{unique_suffix}", 
                     placeholder="e.g., 100000")
        st.text_input("Expiry Date", key=f"expiry_date_{unique_suffix}", 
                     placeholder="YYYY-MM-DD")

def get_tool_params_from_session(tool_name: str, unique_suffix: str) -> Dict[str, Any]:
    """Get current parameter values from session state for display."""
    return get_tool_params(tool_name, unique_suffix)

def main():
    """Main Streamlit application."""
    
    # Header
    st.title("📄 DocuSign MCP Tools")
    st.markdown("**User-friendly interface for DocuSign Model Context Protocol tools**")
    
    # Sidebar - Configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # Check token status
        token = load_token()
        if token:
            st.success("✅ OAuth token loaded")
        else:
            st.error("❌ No OAuth token found")
            st.info("Run the Flask app and complete OAuth flow first:\n```bash\npython app.py\n```")
        
        st.divider()
        
        # Account settings
        st.subheader("Account Settings")
        account_id = st.text_input(
            "Account ID",
            value=ACCOUNT_ID,
            key="account_id",
            help="DocuSign Account ID"
        )
        
        workflow_id = st.text_input(
            "Default Workflow ID",
            value=WORKFLOW_ID,
            key="workflow_id",
            help="Default workflow ID for Maestro operations"
        )
        
        st.divider()
        
        # Links
        st.subheader("📚 Resources")
        st.markdown("""
        - [MCP Tools Reference](DOCUSIGN_MCP_TOOLS.md)
        - [Maestro Workflows](MAESTRO_WORKFLOWS.md)
        - [GitHub Repository](https://github.com/Arturo-Quiroga-MSFT/docusign-price-adjustment-agent)
        """)
    
    # Main content - Tool selection
    st.header("🔧 Select a Tool")
    
    # Create tabs for different categories
    tabs = st.tabs(list(TOOL_CATEGORIES.keys()))
    
    for tab_idx, (tab, (category, tools)) in enumerate(zip(tabs, TOOL_CATEGORIES.items())):
        with tab:
            # Display tools in this category
            for tool_idx, (tool_name, description) in enumerate(tools):
                # Create unique key for this tool using tab and tool indices
                unique_key = f"tab{tab_idx}_tool{tool_idx}_{tool_name}"
                
                with st.expander(f"**{tool_name}** - {description}", expanded=False):
                    st.markdown(f"### {tool_name}")
                    st.caption(description)
                    
                    # Render input widgets for this specific tool
                    render_tool_inputs(tool_name, unique_key)
                    
                    # Display current parameters (after inputs are rendered)
                    params = get_tool_params_from_session(tool_name, unique_key)
                    
                    if params:
                        st.markdown("**Current Parameters:**")
                        st.json(params)
                    
                    # Call button
                    col1, col2 = st.columns([1, 4])
                    with col1:
                        if st.button("▶️ Run", key=f"run_{unique_key}"):
                            # Get fresh parameters from session state
                            params = get_tool_params(tool_name, unique_key)
                            
                            # Validate required parameters
                            required_params = {
                                "queryRAG": ["prompt"],
                                "getUser": ["userId"],
                                "getBrand": ["brandId"],
                                "getEnvelope": ["envelopeId"],
                                "listRecipients": ["envelopeId"],
                                "getAgreementDetails": ["agreementId"],
                                "getWorkflowInstance": ["instanceId"],
                                "cancelWorkflowInstance": ["instanceId"],
                                "updateEnvelope": ["envelopeId"],
                                "getBillingPlan": ["billingPlanId"],
                                "triggerWorkflow": ["trigger_inputs"]
                            }
                            
                            missing_params = []
                            if tool_name in required_params:
                                for param in required_params[tool_name]:
                                    if param not in params or not params[param]:
                                        missing_params.append(param)
                            
                            if missing_params:
                                st.error(f"Missing required parameters: {', '.join(missing_params)}")
                            else:
                                result = call_mcp_tool(tool_name, params)
                                if result:
                                    display_result(tool_name, result)
                                    
                                    # Save to session state for history
                                    if "history" not in st.session_state:
                                        st.session_state.history = []
                                    st.session_state.history.append({
                                        "tool": tool_name,
                                        "params": params,
                                        "result": result
                                    })
                    
                    with col2:
                        # Show Python code example
                        if st.button("📝 Show Code", key=f"code_{unique_key}"):
                            st.code(f"""
# Python code to call {tool_name}
python scripts/call_mcp_tool.py {tool_name}
                            """, language="bash")
    
    # History section
    if "history" in st.session_state and st.session_state.history:
        st.divider()
        st.header("📜 Recent Calls")
        
        for idx, call in enumerate(reversed(st.session_state.history[-5:])):
            with st.expander(f"{call['tool']} - Call #{len(st.session_state.history) - idx}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("Parameters")
                    st.json(call['params'])
                with col2:
                    st.subheader("Response")
                    st.json(call['result'])

if __name__ == "__main__":
    main()
