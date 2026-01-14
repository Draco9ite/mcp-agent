#!/usr/bin/env python3
"""
DocuSign Authentication Test Script
Tests the JWT authentication flow with DocuSign
"""

import logging
import sys
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_docusign_auth():
    """Test DocuSign JWT authentication"""
    try:
        # Load environment variables
        load_dotenv()
        
        # Import the auth manager
        from docusign_auth_manager import create_docusign_auth_manager
        
        logger.info("🔧 Testing DocuSign JWT Authentication...")
        
        # Create auth manager
        auth_manager = create_docusign_auth_manager()
        
        # Test authentication
        success = auth_manager.test_authentication()
        
        if success:
            logger.info("🎉 DocuSign authentication test PASSED!")
            return True
        else:
            logger.error("❌ DocuSign authentication test FAILED!")
            return False
            
    except Exception as e:
        logger.error(f"❌ DocuSign authentication test error: {e}")
        return False

def main():
    """Main test function"""
    print("=" * 60)
    print("🔐 DOCUSIGN JWT AUTHENTICATION TEST")
    print("=" * 60)
    
    # Check environment variables
    required_vars = ['INTEGRATION_KEY', 'USER_ID', 'PRIVATE_KEY']
    missing_vars = []
    
    load_dotenv()
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("💡 Please set these in your .env file")
        return False
    
    # Run the test
    success = test_docusign_auth()
    
    print("=" * 60)
    if success:
        print("✅ ALL TESTS PASSED! DocuSign authentication is working.")
    else:
        print("❌ TESTS FAILED! Check your DocuSign configuration.")
    print("=" * 60)
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
