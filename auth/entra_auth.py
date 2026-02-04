"""
Microsoft Entra ID Authentication Helper
"""
import msal
from config import Config

def _build_msal_app(cache=None, authority=None):
    """
    Creates a ConfidentialClientApplication instance.
    This is the secure client that holds your Secret.
    """
    return msal.ConfidentialClientApplication(
        Config.CLIENT_ID,
        authority=authority or Config.AUTHORITY,
        client_credential=Config.CLIENT_SECRET,
        token_cache=cache
    )

def build_auth_url(scopes=None, state=None):
    """
    Generates the Microsoft Login URL for the user to click.
    """
    return _build_msal_app().get_authorization_request_url(
        scopes or Config.SCOPE,
        state=state,
        redirect_uri='http://localhost:5000' + Config.REDIRECT_PATH
    )

def get_token_from_code(code, scopes=None, auth_code_flow=None):
    """
    Exchanges the 'code' returned by Microsoft for an actual Access Token.
    """
    cache = msal.SerializableTokenCache()
    
    result = _build_msal_app(cache=cache).acquire_token_by_authorization_code(
        code,
        scopes=scopes or Config.SCOPE,
        redirect_uri='http://localhost:5000' + Config.REDIRECT_PATH,
        auth_code_flow=auth_code_flow
    )
    return result