import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Application configuration class"""
    
    # Azure OpenAI Configuration (same as appcertify-aiagent)
    AZURE_OPENAI_API_KEY = os.getenv('AZURE_OPENAI_API_KEY')
    AZURE_OPENAI_API_VERSION = os.getenv('AZURE_OPENAI_API_VERSION', '2024-02-01')
    AZURE_OPENAI_ENDPOINT = os.getenv('AZURE_OPENAI_ENDPOINT')
    AZURE_OPENAI_DEPLOYMENT = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME')  # Match your .env file
    
    # DocuSign JWT Configuration
    INTEGRATION_KEY = os.getenv('INTEGRATION_KEY')
    USER_ID = os.getenv('USER_ID')
    ACCOUNT_ID = os.getenv('ACCOUNT_ID')
    BASE_URI = os.getenv('BASE_URI', 'https://demo.docusign.net')
    PRIVATE_KEY = os.getenv('PRIVATE_KEY')
    JWT_TOKEN_EXPIRY = int(os.getenv('JWT_TOKEN_EXPIRY', 3600))
    JWT_SCOPE = os.getenv('JWT_SCOPE', 'signature impersonation')
    SECRET_KEY = os.getenv('SECRET_KEY')
    
    # BLS (Bureau of Labor Statistics) API Configuration
    BLS_API_KEY = os.getenv('BLS_API_KEY')
    BLS_BASE_URL = os.getenv('BLS_BASE_URL', 'https://api.bls.gov/publicAPI/v2/timeseries/data')
    CPI_SERIES_ID = os.getenv('CPI_SERIES_ID', 'CUUR0000SA0')  # All items CPI for All Urban Consumers
    
    # Database Configuration
    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///price_adjustment.db')
    
    # Flask Configuration
    DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', 5001))  # Different port from appcertify-aiagent
    
    # Application Settings
    MAX_RECENT_REQUESTS = 10
    DOCUMENT_EXPIRY_DAYS = 30  # Look for documents expiring in next 30 days
    
    # Scheduler Configuration
    SCHEDULED_RUN_TIME = "19:00"  # 7 PM daily
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        # Check Azure OpenAI configuration
        if not cls.AZURE_OPENAI_API_KEY:
            raise ValueError("AZURE_OPENAI_API_KEY environment variable is required")
        
        if not cls.AZURE_OPENAI_ENDPOINT:
            raise ValueError("AZURE_OPENAI_ENDPOINT is required when using Azure OpenAI")
            
        if not cls.AZURE_OPENAI_DEPLOYMENT:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT is required when using Azure OpenAI")
        
        # Note: DocuSign and BLS API keys will be validated when we implement actual functionality
        # For skeleton phase, we'll use dummy implementations
        
        # DocuSign JWT validation (for when we move beyond skeleton)
        # For skeleton phase, these are optional
        docusign_required = [cls.INTEGRATION_KEY, cls.USER_ID, cls.PRIVATE_KEY]
        if any(docusign_required) and not all(docusign_required):
            raise ValueError("When using DocuSign, INTEGRATION_KEY, USER_ID, and PRIVATE_KEY are all required")
        
        return True
