import logging
import json
import requests
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Add the parent directory to the path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_database_instance

async def fetch_cpi_data(input_data: str) -> str:
    """
    Fetch CPI data for specific agreement effective date to current date
    REAL IMPLEMENTATION - Connects to actual BLS API for agreement-specific price adjustments
    """
    try:
        logger.info(f"📈 ========================================")
        logger.info(f"📈 BLS AGENT TOOL CALLED")
        logger.info(f"📈 ========================================")
        logger.info(f"📈 FUNCTION CALLED WITH INPUT: {input_data}")

        # Log agent start for UI broadcasting (extract request_id first)
        try:
            # Quick extraction of request_id for immediate broadcast
            if '"request_id"' in input_data:
                import re
                request_match = re.search(r'"request_id":\s*"([^"]+)"', input_data)
                if request_match:
                    temp_request_id = request_match.group(1)
                    db = get_database_instance()
                    db.log_agent_activity(temp_request_id, "bls_agent", "BLS_STARTED", {})
        except:
            pass

        # Initialize variables with defaults to prevent scope issues
        request_id = f"bls_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        user_id = 'system'
        agreement_id = 'unknown'
        effective_date = None
        expiration_date = None
        contract_value = 0.0
        currency = 'USD'

        # Parse input data - expecting JSON with agreement details
        import re
        
        # Try to parse as JSON first
        try:
            input_json = json.loads(input_data)
            request_id = input_json.get('request_id', request_id)
            user_id = input_json.get('user_id', 'system')
            agreement_id = input_json.get('agreement_id', 'unknown')
            effective_date = input_json.get('effective_date')
            expiration_date = input_json.get('expiration_date')
            contract_value = float(input_json.get('contract_value', 0))  # Convert to float
            currency = input_json.get('currency', 'USD')
        except json.JSONDecodeError:
            # Fallback to regex parsing for simple request_id
            request_id_match = re.search(r'Request ID:\s*(.+?)(?:\n|$)', input_data)
            if not request_id_match:
                if re.match(r'^[a-zA-Z0-9_]+$', input_data.strip()):
                    request_id = input_data.strip()
                else:
                    request_id = f"bls_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            else:
                request_id = request_id_match.group(1).strip()
            
            # Set defaults for fallback mode
            user_id = 'system'
            agreement_id = 'fallback'
            effective_date = None
            expiration_date = None
            contract_value = 0.0  # Ensure it's a float
            currency = 'USD'
        
        logger.info(f"📈 Processing Request ID: {request_id}")
        logger.info(f"📈 User ID: {user_id}")
        logger.info(f"📋 Agreement ID: {agreement_id}")
        logger.info(f"💰 Original Contract Value: ${contract_value:,.2f} {currency}")
        logger.info(f"📅 Contract Effective Date: {effective_date}")
        logger.info(f"📅 Contract Expiration Date: {expiration_date}")
        
        # Parse dates
        if effective_date:
            # Parse ISO format date
            effective_dt = datetime.fromisoformat(effective_date.replace('Z', '+00:00').replace('T00:00:00', ''))
            effective_year = effective_dt.year
            effective_month = effective_dt.month
            logger.info(f"📅 Parsed Effective Date: {effective_year}-{effective_month:02d}")
        else:
            # Fallback to previous year comparison
            effective_year = datetime.now().year - 1
            effective_month = datetime.now().month
            logger.warning(f"� ⚠️ No effective date provided, using fallback: {effective_year}-{effective_month:02d}")
        
        # Current date for comparison
        current_dt = datetime.now()
        current_year = current_dt.year
        current_month = current_dt.month
        
        logger.info(f"📅 Current Date for Comparison: {current_year}-{current_month:02d}")
        
        # Calculate date range for BLS API
        start_year = min(effective_year, current_year)
        end_year = max(effective_year, current_year)
        
        # Get BLS API key from environment
        bls_api_key = os.getenv('BLS_API_KEY')
        if not bls_api_key:
            logger.warning("📈 ⚠️ BLS_API_KEY not found in environment, using public API (limited)")
        
        logger.info(f"📈 Fetching CPI data for years: {start_year}-{end_year}")
        
        # BLS API endpoint
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
        
        # Payload for CPI All Urban Consumers (CUUR0000SA0)
        payload = {
            "seriesid": ["CUUR0000SA0"],  # All items CPI for All Urban Consumers
            "startyear": str(start_year),
            "endyear": str(end_year),
            "catalog": True,
            "calculations": True,
            "annualaverage": False
        }
        
        # Add registration key if available
        if bls_api_key:
            payload["registrationkey"] = bls_api_key
            logger.info("📈 Using registered API key for enhanced data access")
        
        headers = {'Content-Type': 'application/json'}
        
        logger.info(f"📈 Making API call to BLS: {url}")
        logger.info(f"📈 Payload: {json.dumps(payload, indent=2)}")
        
        # Make the API call
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        
        logger.info(f"📈 BLS API Response Status: {response.status_code}")
        
        if response.status_code == 200:
            bls_data = response.json()
            
            if bls_data.get('status') == 'REQUEST_SUCCEEDED':
                logger.info("📈 ✅ BLS API request succeeded")
                
                # Process the response
                series_data = bls_data['Results']['series'][0]
                series_id = series_data['seriesID']
                data_points = series_data['data']
                
                logger.info(f"📈 Retrieved {len(data_points)} data points for series {series_id}")
                
                # Find specific data points for effective date and current date
                effective_cpi = None
                current_cpi = None
                
                # Sort data by year and period
                sorted_data = sorted(data_points, 
                                   key=lambda x: (int(x['year']), x['period']))
                
                # Look for effective date CPI
                effective_period = f"M{effective_month:02d}"
                for data_point in sorted_data:
                    if data_point['year'] == str(effective_year) and data_point['period'] == effective_period:
                        effective_cpi = data_point
                        break
                
                # Look for current date CPI (use most recent available)
                current_period = f"M{current_month:02d}"
                for data_point in reversed(sorted_data):
                    if data_point['year'] == str(current_year):
                        if data_point['period'] == current_period:
                            current_cpi = data_point
                            break
                        elif not current_cpi:  # Use any available current year data if exact month not found
                            current_cpi = data_point
                
                # If no exact matches, use closest available data
                if not effective_cpi and sorted_data:
                    # Find closest to effective date
                    for data_point in sorted_data:
                        if int(data_point['year']) >= effective_year:
                            effective_cpi = data_point
                            break
                    if not effective_cpi:
                        effective_cpi = sorted_data[0]  # Use earliest available
                
                if not current_cpi and sorted_data:
                    current_cpi = sorted_data[-1]  # Use most recent available
                
                if effective_cpi and current_cpi:
                    effective_value = float(effective_cpi['value'])
                    current_value = float(current_cpi['value'])
                    
                    # Calculate cumulative inflation rate over contract period
                    inflation_rate = ((current_value - effective_value) / effective_value) * 100
                    adjustment_factor = 1 + (inflation_rate / 100)
                    
                    # Calculate new contract value
                    adjusted_contract_value = contract_value * adjustment_factor
                    
                    logger.info(f"📈 ========================================")
                    logger.info(f"📈 PRICE ADJUSTMENT CALCULATION")
                    logger.info(f"📈 ========================================")
                    logger.info(f"📋 Agreement ID: {agreement_id}")
                    logger.info(f"📅 Contract Period: {effective_cpi['year']}-{effective_cpi['period']} to {current_cpi['year']}-{current_cpi['period']}")
                    logger.info(f"📈 CPI at Contract Start: {effective_value}")
                    logger.info(f"📈 CPI at Current Date: {current_value}")
                    logger.info(f"📈 Cumulative Inflation Rate: {inflation_rate:.2f}%")
                    logger.info(f"📈 Adjustment Factor: {adjustment_factor:.4f}")
                    logger.info(f"💰 Original Contract Value: ${contract_value:,.2f}")
                    logger.info(f"💰 New Adjusted Value: ${adjusted_contract_value:,.2f}")
                    logger.info(f"💰 Value Increase: ${adjusted_contract_value - contract_value:,.2f}")
                    
                    # Save calculation results to database
                    logger.info(f"� 💾 Saving calculation results to database...")
                    
                    # Initialize database
                    db = get_database_instance()
                    
                    # Prepare adjustment data for database
                    adjustment_data = {
                        'original_contract_value': contract_value,
                        'adjusted_contract_value': adjusted_contract_value,
                        'inflation_rate_applied': inflation_rate,
                        'adjustment_date': datetime.now().strftime('%Y-%m-%d'),
                        'bls_data_used': {
                            'series_id': series_id,
                            'effective_date_cpi': effective_cpi,
                            'current_date_cpi': current_cpi,
                            'calculation_method': 'cumulative_inflation',
                            'data_points_used': len(data_points)
                        },
                        'effective_date': effective_date,
                        'expiration_date': expiration_date
                    }
                    
                    # Update database with calculation results
                    db_success = db.update_price_adjustment(agreement_id, adjustment_data)
                    
                    if db_success:
                        logger.info(f"📈 ✅ Successfully saved calculation results to database")
                        logger.info(f"📈 🎯 Agreement {agreement_id} marked as price_adjustment_completed = True")
                    else:
                        logger.error(f"📈 ❌ Failed to save calculation results to database")
                        # Don't fail the whole process, but log the issue
                    
                    # Log agent activity
                    db.log_agent_activity(
                        request_id=request_id,
                        agent_name="bls_agent",
                        agent_status="BLS_COMPLETE",
                        results={
                            "agreement_id": agreement_id,
                            "original_value": contract_value,
                            "adjusted_value": adjusted_contract_value,
                            "inflation_rate": inflation_rate,
                            "value_increase": adjusted_contract_value - contract_value,
                            "database_updated": db_success
                        }
                    )
                    
                    result = {
                        "status": "BLS_COMPLETE",
                        "request_id": request_id,
                        "user_id": user_id,
                        "agreement_details": {
                            "agreement_id": agreement_id,
                            "effective_date": effective_date,
                            "expiration_date": expiration_date,
                            "original_contract_value": contract_value,
                            "currency": currency
                        },
                        "bls_data": {
                            "series_id": series_id,
                            "series_title": series_data.get('catalog', {}).get('series_title', 'CPI All Urban Consumers'),
                            "effective_date_cpi": effective_cpi,
                            "current_date_cpi": current_cpi,
                            "all_data_points": data_points[:12]  # Include recent 12 months for context
                        },
                        "calculations": {
                            "effective_cpi": effective_value,
                            "current_cpi": current_value,
                            "cumulative_inflation_rate_percent": round(inflation_rate, 2),
                            "adjustment_factor": round(adjustment_factor, 4),
                            "adjusted_contract_value": round(adjusted_contract_value, 2),
                            "value_increase": round(adjusted_contract_value - contract_value, 2)
                        },
                        "api_info": {
                            "fetch_date": datetime.now().strftime("%Y-%m-%d"),
                            "response_time_ms": bls_data.get('responseTime', 0),
                            "used_api_key": bool(bls_api_key)
                        },
                        "timestamp": datetime.utcnow().isoformat(),
                        "agent": "bls_agent"
                    }
                    
                    logger.info(f"📈 ✅ BLS AGENT COMPLETED: Agreement {agreement_id}")
                    logger.info(f"📈 📊 STATUS: BLS_COMPLETE")
                    
                    return json.dumps(result)
                    
                else:
                    raise Exception("Could not find sufficient CPI data points for calculation")
                    
            else:
                error_msg = f"BLS API returned error status: {bls_data.get('status')} - {bls_data.get('message', [])}"
                logger.error(f"📈 ❌ {error_msg}")
                raise Exception(error_msg)
                
        else:
            error_msg = f"BLS API call failed with status {response.status_code}: {response.text}"
            logger.error(f"📈 ❌ {error_msg}")
            raise Exception(error_msg)
        
    except Exception as e:
        logger.error(f"📈 ❌ BLS AGENT ERROR: {str(e)}")
        
        # Return error response with proper JSON structure
        error_result = {
            "status": "BLS_ERROR",
            "request_id": request_id,
            "user_id": user_id,
            "agreement_id": agreement_id,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "bls_agent"
        }
        
        logger.error(f"📈 ❌ BLS AGENT FAILED: {str(e)}")
        logger.error(f"📈 📊 STATUS: BLS_ERROR")
        
        return json.dumps(error_result)
