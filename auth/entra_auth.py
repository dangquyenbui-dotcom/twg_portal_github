"""
Microsoft Entra ID Authentication Helper
"""
import msal
import logging
from config import Config

logger = logging.getLogger(__name__)

def _build_msal_app(cache=None, authority=None):
    """
    Creates a ConfidentialClientApplication instance.
    """
    return msal.ConfidentialClientApplication(
        Config.CLIENT_ID,
        authority=authority or Config.AUTHORITY,
        client_credential=Config.CLIENT_SECRET,
        token_cache=cache
    )

def get_token_from_code(auth_response, auth_code_flow=None):
    """
    Exchanges the 'code' for an Access Token using the flow object.
    
    CRITICAL FIX: We use 'acquire_token_by_auth_code_flow' instead of 
    'acquire_token_by_authorization_code'. This method automatically 
    extracts the code and the PKCE verifier from the flow object, 
    preventing AADSTS50148 errors.
    """
    logger.debug("Exchanging token using acquire_token_by_auth_code_flow...")
    
    return _build_msal_app().acquire_token_by_auth_code_flow(
        auth_code_flow=auth_code_flow,
        auth_response=auth_response
    )