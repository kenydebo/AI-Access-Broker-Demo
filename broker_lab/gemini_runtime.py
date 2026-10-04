"""Explicit hosted opt-in configuration. Does not read credentials at import."""
from .gemini_agent import GeminiAgent, GeminiClient, MODEL


def optional_model(environment):
    enabled = environment.get('BROKER_GEMINI_ENABLED', '0')
    if enabled == '0':
        return None  # Never inspects a key in disabled mode.
    if enabled != '1' or environment.get('BROKER_GEMINI_ACTIVATION_APPROVED') != 'synthetic-audience-reviewed':
        raise SystemExit('Gemini activation configuration incomplete; no model request enabled')
    # This acknowledgement is an operator activation switch, not geolocation or terms enforcement.
    try:
        client = GeminiClient(environment.get('GEMINI_API_KEY', ''), environment.get('GEMINI_MODEL', MODEL))
    except ValueError:
        raise SystemExit('Reviewed model and private environment key required; values not logged') from None
    return GeminiAgent(client).run_demo
