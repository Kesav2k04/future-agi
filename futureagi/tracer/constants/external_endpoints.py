from enum import Enum


class ObservabilityRoutes(str, Enum):
    VAPI_CALL_URL = "https://api.vapi.ai/call"
    RETELL_LIST_CALLS_URL = "https://api.retellai.com/v2/list-calls"
    ELEVEN_LABS_CONVERSATIONS_URL = "https://api.elevenlabs.io/v1/convai/conversations"

    ## Assistant endpoints
    VAPI_ASSISTANT_URL = "https://api.vapi.ai/assistant"
    RETELL_GET_ASSISTANT_URL = "https://api.retellai.com/get-agent"
    RETELL_LIST_ASSISTANTS_URL = "https://api.retellai.com/list-agents"


# Vapi recording download endpoints (used by auth-powered rehost path)
VAPI_CALL_MONO_RECORDING_URL = "https://api.vapi.ai/call/{call_id}/mono-recording"
VAPI_CALL_STEREO_RECORDING_URL = "https://api.vapi.ai/call/{call_id}/stereo-recording"
VAPI_CALL_CUSTOMER_RECORDING_URL = "https://api.vapi.ai/call/{call_id}/customer-recording"
VAPI_CALL_ASSISTANT_RECORDING_URL = "https://api.vapi.ai/call/{call_id}/assistant-recording"
