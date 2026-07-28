#!/usr/bin/env python3
"""
Holy Guacamole! - AI-Powered Drive-Thru Order Agent
Web UI and SWML served on the same port
"""

import random
import os
import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP

_CENT = Decimal("0.01")   # money quantum for order math
import time
import logging
import threading
import warnings
from pathlib import Path
from dotenv import load_dotenv
from fastapi.responses import JSONResponse, Response
from signalwire import AgentBase, AgentServer
from signalwire.core.function_result import SwaigFunctionResult
from signalwire.rest import RestClient

# Load environment variables from .env file (for local development)
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Logging Configuration
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Global State
# ─────────────────────────────────────────────────────────────────────────────
# This dict stores the SWML handler info after registration on startup.
# It's used by the /get_token endpoint to provide the call address to clients.
swml_handler_info = {
    "id": None,           # Handler resource ID
    "address_id": None,   # Address resource ID (used to scope tokens)
    "address": None       # The SIP address clients dial to reach the agent
}

# Voice store file path (shared between workers)
VOICE_STORE_FILE = "/tmp/guacamole_voice.txt"

DEFAULT_VOICE = "elevenlabs.adam"

# Allowlist of selectable voices, loaded from the same JSON files the web UI
# offers. Used to reject a bogus/stale ?voice= before it reaches the SWML doc.
_VOICE_FILES = ("inworld_voices.json", "elevenlabs_voices.json",
                "smallest_voices.json", "fish_voices.json")
_known_voices = set()
for _vf in _VOICE_FILES:
    try:
        with open(Path(__file__).parent / "web" / _vf) as _fh:
            _known_voices.update(
                v["voiceId"] for v in json.load(_fh) if isinstance(v, dict) and v.get("voiceId"))
    except Exception as _e:      # a missing vendor file just shrinks the allowlist
        logging.getLogger(__name__).warning("Could not load %s: %s", _vf, _e)
_known_voices.add(DEFAULT_VOICE)


def is_known_voice(voice):
    """True if `voice` is one of the voices the UI actually offers."""
    return bool(voice) and voice in _known_voices


def singular_forms(s):
    """Candidate singular spellings of `s` (menu aliases are singular, callers
    speak plurals: "two waters", "three sodas", "remove the bottles")."""
    s = (s or "").lower().strip()
    forms = {s}
    if s.endswith("ies") and len(s) > 4:
        forms.add(s[:-3] + "y")
    if s.endswith("es") and len(s) > 3:
        forms.add(s[:-2])
    if s.endswith("s") and not s.endswith("ss"):
        forms.add(s[:-1])
    return forms


# Per-call voice selection: guest_id -> (voice, ts). /get_token records the
# caller's pick against the guest identity of the token it just minted
# (address_uri = "/guest/guest-<uuid>"), and the SWML request for that call
# arrives from "sip:guest-<uuid>@...". Keying on that makes the voice per-call
# instead of a single shared file that a second caller could overwrite
# mid-order.
_voice_by_guest = {}
_GUEST_VOICE_TTL = 2 * 3600
_GUEST_RE = re.compile(r"guest-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _guest_id_from_request(request_data):
    """Pull the guest id out of an SWML request (caller id / address / anywhere).

    The field name isn't guaranteed across call shapes, so match the well-formed
    guest-<uuid> pattern anywhere in the payload rather than guessing a key.
    """
    if not request_data:
        return None
    try:
        m = _GUEST_RE.search(json.dumps(request_data))
        return m.group(0).lower() if m else None
    except Exception:
        return None


def set_voice_for_guest(guest_id, voice):
    """Remember this caller's voice pick for the call they're about to place."""
    if not guest_id or not voice:
        return
    now = time.time()
    _voice_by_guest[guest_id.lower()] = (voice, now)
    for gid in [g for g, (_v, ts) in _voice_by_guest.items()
                if now - ts > _GUEST_VOICE_TTL]:
        _voice_by_guest.pop(gid, None)


def get_voice_for_guest(guest_id):
    entry = _voice_by_guest.get((guest_id or "").lower())
    return entry[0] if entry else None


def get_stored_voice():
    """Get voice from shared file store."""
    try:
        with open(VOICE_STORE_FILE, 'r') as f:
            return f.read().strip()
    except Exception as e:
        # Bare `except:` also swallowed permission errors silently.
        logging.getLogger(__name__).debug("No stored voice (%s)", e)
        return None

def set_stored_voice(voice):
    """Set voice in shared file store."""
    with open(VOICE_STORE_FILE, 'w') as f:
        f.write(voice)

# Why registration hasn't happened yet (surfaced by /get_token so a
# misconfiguration shows up in the browser, not just the server log)
swml_setup_error = None

# Guards the lazy setup retry from /get_token
swml_setup_lock = threading.Lock()

# Serializes order-mutating SWAIG handlers and de-dupes the model's rapid
# duplicate tool-calls (e.g. add_item fired twice in ~1s for one utterance),
# which otherwise processed concurrently and corrupted the webhook response
# (SignalWire "webhook_fail" / parse_error). Keyed by call_id+signature within
# a short window so a real "two tacos" (single qty=2 call) is never affected.
swaig_mutation_lock = threading.Lock()
_recent_swaig_calls = {}          # call_id -> {"sig":…, "ts":…, "response":…}
_SWAIG_DEDUP_WINDOW = 2.0         # seconds; shorter than any human re-order

# Authoritative per-call order state: call_id -> {"state": {...}, "ts": epoch}.
# The platform's echoed global_data is a per-TURN snapshot, so two mutating tool
# calls in the same turn both read it and the second's set_global_data clobbers
# the first (see get_order_state). Holding the order here makes them compose.
# Single worker (see Dockerfile) keeps this authoritative.
_call_order_states = {}
_ORDER_STATE_TTL = 4 * 3600       # drop states from calls that ended long ago


def _prune_order_states(max_entries=1000):
    """Drop stale per-call order states (calls that ended without cleanup)."""
    now = time.time()
    for cid in [c for c, v in _call_order_states.items()
                if now - v.get("ts", 0) > _ORDER_STATE_TTL]:
        _call_order_states.pop(cid, None)
    # Hard cap as a backstop: evict the oldest if something goes wrong.
    if len(_call_order_states) > max_entries:
        for cid, _ in sorted(_call_order_states.items(),
                             key=lambda kv: kv[1].get("ts", 0))[:len(_call_order_states) - max_entries]:
            _call_order_states.pop(cid, None)

# Import for TF-IDF vector matching
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    # Loud, not a passing note: without sklearn the app silently degrades to a
    # crude fuzzy scorer that mis-resolves items (it matched "big tacos" ->
    # "Large Drink" in production). scikit-learn is pinned in requirements.txt,
    # so reaching this branch means the image is built wrong.
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).error(
        "scikit-learn is MISSING - menu matching is falling back to the fuzzy "
        "scorer, which mis-resolves items. Rebuild the image with requirements.txt.")
    print("ERROR: scikit-learn not installed. Menu matching will be inaccurate.")

# Phase 1: Simple menu structure with descriptions
MENU = {
    "tacos": {
        "T001": {"name": "Beef Taco", "price": 3.49, "description": "Seasoned ground beef, lettuce, cheese, and salsa in a crispy shell"},
        "T002": {"name": "Chicken Taco", "price": 3.49, "description": "Grilled chicken, lettuce, cheese, and pico de gallo in a crispy shell"},
        "T003": {"name": "Bean Taco", "price": 2.99, "description": "Refried beans, lettuce, cheese, and salsa in a crispy shell"}
    },
    "burritos": {
        "B001": {"name": "Beef Burrito", "price": 8.99, "description": "Large flour tortilla with seasoned beef, rice, beans, cheese, and salsa"},
        "B002": {"name": "Chicken Burrito", "price": 8.99, "description": "Large flour tortilla with grilled chicken, rice, beans, cheese, and pico"},
        "B003": {"name": "Bean & Cheese Burrito", "price": 6.99, "description": "Large flour tortilla with refried beans and melted cheese"}
    },
    "quesadillas": {
        "Q001": {"name": "Cheese Quesadilla", "price": 5.99, "description": "Grilled flour tortilla with melted cheese blend"},
        "Q002": {"name": "Chicken Quesadilla", "price": 7.99, "description": "Grilled flour tortilla with seasoned chicken and melted cheese"}
    },
    "sides": {
        "S001": {"name": "Chips & Salsa", "price": 2.99, "description": "Fresh tortilla chips with our house-made salsa"},
        "S002": {"name": "Chips & Guacamole", "price": 4.99, "description": "Fresh tortilla chips with fresh-made guacamole"}
    },
    "drinks": {
        "D001": {"name": "Small Drink", "price": 1.99, "description": "16oz fountain drink of your choice"},
        "D002": {"name": "Large Drink", "price": 2.99, "description": "24oz fountain drink of your choice"},
        "D003": {"name": "Bottled Water", "price": 1.99, "description": "16oz bottled water"}
    },
    "combos": {
        "C001": {"name": "Taco Combo", "price": 9.99, "description": "2 tacos (your choice) + chips & salsa + small drink"},
        "C002": {"name": "Burrito Combo", "price": 12.99, "description": "Any burrito + chips & salsa + small drink"}
    }
}

# Alias dictionary for better menu item matching
MENU_ALIASES = {
    "D003": ["water", "bottled water", "water bottle", "aqua", "bottle of water", "h2o"],
    "D001": ["small soda", "small drink", "soda", "soft drink", "small fountain drink", "coke", "pepsi", "sprite"],
    "D002": ["large soda", "large drink", "big drink", "large fountain drink", "big soda"],
    "Q001": ["quesadilla", "cheese quesadilla", "plain quesadilla", "just cheese", "cheese only"],
    "Q002": ["chicken quesadilla", "chicken and cheese quesadilla"],
    "C001": ["taco meal", "taco combo", "taco deal", "taco special", "combo taco"],
    "C002": ["burrito meal", "burrito combo", "burrito deal", "burrito special", "combo burrito"],
    "S001": ["chips", "nachos", "chips and salsa", "chips with salsa", "salsa and chips", "just chips", "salsa", "chips n salsa"],
    "S002": ["guac", "chips and guac", "chips with guacamole", "guacamole and chips", "guac and chips", "guacamole", "chips n guacamole"],
    "T001": ["beef taco", "beef tacos", "ground beef taco", "regular taco", "taco beef"],
    "T002": ["chicken taco", "chicken tacos", "grilled chicken taco", "taco chicken"],
    "T003": ["bean taco", "bean tacos", "vegetarian taco", "veggie taco"],
    "B001": ["beef burrito", "beef burritos", "regular burrito", "burrito beef", "burrito with beef"],
    "B002": ["chicken burrito", "chicken burritos", "burrito chicken", "burrito with chicken"],
    "B003": ["bean burrito", "bean burritos", "bean and cheese burrito", "cheese and bean burrito"]
}


# ═══════════════════════════════════════════════════════════════════════════════
# SWML Handler Registration Functions
# ═══════════════════════════════════════════════════════════════════════════════
# These functions handle automatic registration of your agent with SignalWire
# so that incoming calls are routed to your SWML endpoint.

def get_signalwire_host():
    """
    Get the full SignalWire API host from the space name.

    The space name can be provided as either:
    - Just the space: "myspace" -> "myspace.signalwire.com"
    - Full domain: "myspace.signalwire.com" -> used as-is
    """
    space = os.getenv("SIGNALWIRE_SPACE_NAME", "")
    if not space:
        return None
    if "." in space:
        return space
    return f"{space}.signalwire.com"


def get_rest_client():
    """
    Build a SignalWire RestClient from environment configuration.

    Returns None when credentials are not configured.
    """
    sw_host = get_signalwire_host()
    project = os.getenv("SIGNALWIRE_PROJECT_ID", "")
    token = os.getenv("SIGNALWIRE_TOKEN", "")
    if not all([sw_host, project, token]):
        return None
    return RestClient(project=project, token=token, host=sw_host)


def find_resource_address(addresses, agent_name):
    """
    Find the resource address matching /public/{agent_name} from a list of addresses.

    When phone numbers are attached to a handler, multiple addresses exist.
    We want the resource address (e.g., /public/holyguacamole) not the phone number address.
    """
    expected_address = f"/public/{agent_name}"

    # First, try to find exact match for /public/{agent_name}
    for addr in addresses:
        audio_channel = addr.get("channels", {}).get("audio", "")
        if audio_channel == expected_address:
            return addr

    # Fallback: find any address that looks like a SIP address (not a phone number)
    for addr in addresses:
        audio_channel = addr.get("channels", {}).get("audio", "")
        # SIP addresses start with /public/ and don't contain phone number patterns
        if audio_channel.startswith("/public/") and not any(c.isdigit() for c in audio_channel.split("/")[-1][:3]):
            return addr

    # Last resort: return first address
    return addresses[0] if addresses else None


def find_existing_handler(client, agent_name):
    """
    Find an existing SWML handler by name.

    This prevents creating duplicate handlers on each deployment.
    We search by agent name rather than URL because the URL may change
    (e.g., different basic auth credentials).

    Args:
        client: signalwire.rest.RestClient
        agent_name: The name to search for

    Returns:
        Dict with handler info if found, None otherwise
    """
    try:
        # List all SWML webhook handlers in the project
        handlers = client.fabric.swml_webhooks.list().get("data", [])

        for handler in handlers:
            # The name is nested in the swml_webhook object
            swml_webhook = handler.get("swml_webhook", {})
            handler_name = swml_webhook.get("name") or handler.get("display_name")

            # Check if this handler matches our agent name
            if handler_name == agent_name:
                handler_id = handler.get("id")
                handler_url = swml_webhook.get("primary_request_url", "")

                # Get the address for this handler (needed for token scoping)
                addresses = client.fabric.swml_webhooks.list_addresses(handler_id).get("data", [])
                resource_addr = find_resource_address(addresses, agent_name)
                if resource_addr:
                    return {
                        "id": handler_id,
                        "name": handler_name,
                        "url": handler_url,
                        "address_id": resource_addr["id"],
                        "address": resource_addr["channels"]["audio"]
                    }
    except Exception as e:
        logger.error(f"Error finding existing handler: {e}")
    return None


def setup_swml_handler():
    """
    Set up the SWML handler resource on startup via the SignalWire SDK.

    This function:
    1. Checks if a handler with our agent name already exists
    2. If yes: Updates the URL (in case credentials changed)
    3. If no: Creates the resource and maps its dialable address
    4. Stores the handler info globally for use by /get_token

    The SWML URL includes basic auth credentials embedded in it so that
    SignalWire can authenticate when calling back to our endpoint.

    URL Priority:
    1. SWML_PROXY_URL_BASE (if set explicitly)
    2. APP_URL (auto-set by Dokku/Heroku)
    """
    global swml_setup_error

    # Get configuration from environment
    client = get_rest_client()
    agent_name = os.getenv("AGENT_NAME", "holyguacamole")

    # URL priority: SWML_PROXY_URL_BASE > APP_URL (auto-set by Dokku/Heroku)
    proxy_url = os.getenv("SWML_PROXY_URL_BASE", os.getenv("APP_URL", ""))
    auth_user = os.getenv("SWML_BASIC_AUTH_USER", "signalwire")
    auth_pass = os.getenv("SWML_BASIC_AUTH_PASSWORD", "")

    # Validate required configuration
    if client is None:
        swml_setup_error = ("SIGNALWIRE_SPACE_NAME / SIGNALWIRE_PROJECT_ID / "
                            "SIGNALWIRE_TOKEN not set")
        logger.warning(f"{swml_setup_error} - skipping SWML handler setup")
        return

    if not proxy_url:
        swml_setup_error = ("SWML_PROXY_URL_BASE (or APP_URL) not set - it must be "
                            "the public URL SignalWire can fetch SWML from "
                            "(e.g. your ngrok URL)")
        logger.warning(f"{swml_setup_error} - skipping SWML handler setup")
        return

    # Build SWML URL with basic auth credentials embedded
    # Format: https://user:pass@example.com/swml
    if auth_user and auth_pass and "://" in proxy_url:
        scheme, rest = proxy_url.split("://", 1)
        swml_url = f"{scheme}://{auth_user}:{auth_pass}@{rest}/swml"
    else:
        swml_url = f"{proxy_url}/swml"

    # Look for an existing handler by name
    existing = find_existing_handler(client, agent_name)

    if existing:
        # Handler exists - update the URL (credentials may have changed)
        swml_handler_info["id"] = existing["id"]
        swml_handler_info["address_id"] = existing["address_id"]
        swml_handler_info["address"] = existing["address"]
        swml_setup_error = None

        try:
            client.fabric.swml_webhooks.update(
                existing["id"],
                primary_request_url=swml_url,
                primary_request_method="POST"
            )
            logger.info(f"Updated SWML handler: {existing['name']}")
        except Exception as e:
            logger.error(f"Failed to update handler URL: {e}")

        logger.info(f"Call address: {existing['address']}")
    else:
        # Create the SWML handler resource and map its dialable address
        try:
            # A standalone dialable handler (not bound to a phone number) is
            # intentional here, so silence the SDK warning that steers
            # phone-number setups toward phone_numbers.set_swml_webhook
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                handler_resp = client.fabric.swml_webhooks.create(
                    name=agent_name,
                    used_for="calling",
                    primary_request_url=swml_url,
                    primary_request_method="POST"
                )
            handler_id = handler_resp.get("id")
            swml_handler_info["id"] = handler_id

            # Get the dialable address for this handler
            addresses = client.fabric.swml_webhooks.list_addresses(handler_id).get("data", [])
            resource_addr = find_resource_address(addresses, agent_name)
            if resource_addr:
                swml_handler_info["address_id"] = resource_addr["id"]
                swml_handler_info["address"] = resource_addr["channels"]["audio"]
                swml_setup_error = None
            else:
                swml_setup_error = f"handler '{agent_name}' created but no dialable address found"

            logger.info(f"Created SWML handler '{agent_name}' with address: {swml_handler_info.get('address')}")
        except Exception as e:
            logger.error(f"Failed to create SWML handler: {e}")
            # Retry finding existing handler (another worker may have just created it)
            time.sleep(0.5)
            existing = find_existing_handler(client, agent_name)
            if existing:
                swml_handler_info["id"] = existing["id"]
                swml_handler_info["address_id"] = existing["address_id"]
                swml_handler_info["address"] = existing["address"]
                swml_setup_error = None
                logger.info(f"Found existing SWML handler after retry: {existing['name']}")
                logger.info(f"Call address: {existing['address']}")
            else:
                swml_setup_error = f"failed to create handler '{agent_name}': {e}"


class HolyGuacamoleAgent(AgentBase):
    """Sigmond - Your Holy Guacamole! Order Assistant"""
    
    def __init__(self):
        super().__init__(
            name="Sigmond",
            route="/swml",  # SWML endpoint path
            record_call=True
        )

        # Set AI model + barge behavior. transparent_barge defaults to true
        # in the engine (AI waits for the user to finish before responding
        # when they talk over the agent); set it explicitly so the intent is
        # documented in the rendered SWML rather than relying on the default.
        self.set_params({
            "ai_model": "gpt-4.1-mini",
            "transparent_barge": True,
            # Recording + AI disclosure, spoken VERBATIM by the platform rather
            # than by the LLM. A compliance notice must not depend on the model
            # choosing to say it (it demonstrably skips prompt instructions).
            # The call is recorded (record_call) and several US states require
            # all-party consent, so the caller hears this before saying anything
            # and can hang up. no_barge stops them talking over it.
            "static_greeting": (
                # Drive-thru-natural phrasing: the disclosure lands as a casual
                # aside rather than legalese, but still names the bot and says
                # "A I" plainly (the compliance bit - a caller must understand
                # they're talking to a machine, not a person).
                # "A I" stays spaced so TTS reads the letters, not "ay".
                "Welcome to Holy Guacamole! I'm Sigmond, the A I behind the mic "
                "- and yes, this call's recorded. Combo meals save you money, "
                "by the way. What can I get started for you?"
            ),
            "static_greeting_no_barge": True,
        })

        # Initialize TF-IDF vectorizer if available
        self.vectorizer = None
        self.menu_vectors = None
        self.sku_map = []
        
        if HAS_SKLEARN:
            self._initialize_tfidf()
        

        self.prompt_add_section(
            "Personality",
            "You are Sigmond, the friendly order-taker at Holy Guacamole! Mexican drive-thru. "
            "You're warm, enthusiastic about the food, and help customers order efficiently. "
            "The customer has a screen showing their order, so NEVER read back the full order - they can see it! "
            "Just acknowledge items briefly as they're added. Keep responses concise and friendly. "
            "CRITICAL: When a customer orders multiple items in one sentence (like 'two tacos and a drink'), "
            "you MUST call add_item separately for EACH item. Never skip items! "
            "IMPORTANT MENU RULE: NEVER list specific menu items or say what drinks/options we have. "
            "If asked about menu items or options, say 'Please check the menu on your screen' or 'Everything we have is shown on the menu.' "
            "You can ONLY confirm what we have by attempting to add_item - let the function tell you if we have it or not."
        )
        
        # Define conversation contexts with state machine
        contexts = self.define_contexts()
        
        default_context = contexts.add_context("default") \
            .add_section("Goal", "Take accurate food orders efficiently while providing excellent customer service.")
        
        # GREETING STATE - Entry point
        default_context.add_step("greeting") \
            .add_section("Current Task", "Welcome the customer and start their order") \
            .add_bullets("Process", [
                # The greeting (including the recording + AI disclosure) is
                # played automatically as a static_greeting - see set_params.
                # Do not restate it; just take the order.
                "The welcome and the recording/AI disclosure have ALREADY been "
                "played automatically. Do NOT greet again or repeat them.",
                "If the caller asks about recording, confirm plainly that the "
                "call is recorded and that they're speaking with Sigmond, an AI order taker.",
                "Ask what they'd like to order",
                # The combo-saves-money line is part of the scripted greeting now
                # (it used to be woven into the model's improvised welcome), so
                # don't repeat it straight away - just bring it up if it fits later.
                "The greeting already said combo meals save money - don't repeat "
                "it immediately, but you can mention combos again if it's relevant",
                "Listen for ALL items they mention",
                "If they order multiple items (e.g. 'two tacos and a drink'), call add_item for EACH item separately"
            ]) \
            .set_step_criteria("Customer has started ordering") \
            .set_functions(["add_item"]) \
            .set_valid_steps(["taking_order"])
        
        # TAKING ORDER STATE - Main ordering phase  
        default_context.add_step("taking_order") \
            .add_section("Current Task", "Build the customer's order") \
            .add_bullets("IMPORTANT RULES", [
                "Current order has ${global_data.order_state.item_count} items",
                "Current total: $${global_data.order_state.total}",
                "🔴 HIGHEST PRIORITY - Check for RESTART patterns FIRST:",
                "  - 'I only want X', 'never mind just X', 'actually just X' = cancel_order() THEN add_item(X)",
                "  - 'cancel', 'start over', 'never mind' = cancel_order()",
                "When customer orders multiple DIFFERENT items (e.g. 'a taco and a drink'): CALL add_item FOR EACH ITEM SEPARATELY",
                "CRITICAL: If customer says 'X and Y', you MUST call add_item twice - once for X and once for Y",
                "🔢 QUANTITY: If the customer orders MORE THAN ONE of the SAME item (e.g. 'two beef tacos', 'three waters', 'a couple burritos'), call add_item ONCE and PASS the quantity: add_item(item_name='beef taco', quantity=2). NEVER default the quantity to 1 when a number was said.",
                "  - 'two/three/four ...' or 'a couple/a few ...' = set quantity to that number (a couple = 2, a few = 3)",
                "⚠️ CRITICAL PATTERN - Customer wants to RESTART with only one item:",
                "  - TRIGGERS: 'never mind, I just want X', 'I only want X', 'forget everything, just X'",
                "  - Also: 'actually just give me X', 'you know what, just X', 'scratch that, only X'",
                "  - This means CLEAR ALL and keep ONLY the mentioned item",
                "  - ACTION REQUIRED: 1) FIRST call cancel_order(), 2) THEN call add_item(X)",
                "  - DO NOT use remove_item - MUST use cancel_order to clear everything",
                "When customer explicitly wants to cancel entire order:",
                "  - 'cancel my order', 'start over', 'never mind' (without mentioning another item)",
                "  - ACTION: CALL cancel_order()",
                "When customer wants to remove items:",
                "  - 'remove one water/bottle': CALL remove_item('water', quantity=1)",
                "  - 'remove 5 waters/bottles': CALL remove_item('water', quantity=5)",
                "  - 'remove all the water/bottles': CALL remove_item('water', quantity=-1)",
                "  - Default (no quantity specified): removes 1 item",
                "  - IMPORTANT: 'bottles' usually means 'water' - use 'water' as item_name",
                "When customer wants to change quantity: CALL modify_quantity function",
                "When customer wants to see order: CALL review_order function",
                "When customer is done: CALL finalize_order function",
                "Acknowledge items briefly (don't read back the entire order)",
                "💡 COMBO UPGRADES: If add_item response includes 'Great news!' about a combo:",
                "  - This means a money-saving combo is available",
                "  - If customer says 'yes', 'sure', 'okay', 'upgrade' or agrees: CALL upgrade_to_combo",
                "  - Determine combo type from the suggestion (taco, burrito, or both)",
                "  - If response mentions TWO combos, use combo_type='both'",
                "NEVER quote prices yourself - let the functions provide them"
            ]) \
            .set_step_criteria("Customer says they're done ordering") \
            .set_functions(["add_item", "remove_item", "modify_quantity", "review_order", "finalize_order", "upgrade_to_combo", "cancel_order"]) \
            .set_valid_steps(["confirming_order"])
        
        # CONFIRMING ORDER STATE
        default_context.add_step("confirming_order") \
            .add_section("Current Task", "Confirm the complete order") \
            .add_bullets("Instructions", [
                "The customer can see their order on the screen",
                "DO NOT read back the items - they can see them",
                "Just ask if the order on screen looks correct",
                "If they confirm, use process_payment",
                "If they want changes, use add_item or remove_item",
                "Only mention the total price, not individual items"
            ]) \
            .set_step_criteria("Order is confirmed as correct") \
            .set_functions(["process_payment", "add_item", "remove_item", "upgrade_to_combo", "cancel_order"]) \
            .set_valid_steps(["payment_processing", "taking_order"])
        
        # PAYMENT PROCESSING STATE
        default_context.add_step("payment_processing") \
            .add_section("Current Task", "Direct customer to payment") \
            .add_bullets("Instructions", [
                "Order number: ${global_data.order_state.order_number}",
                "Total: $${global_data.order_state.total}",
                "Tell them to pull forward to the first window",
                "Thank them for their order",
                "Call complete_order to finish"
            ]) \
            .set_step_criteria("Payment instructions given") \
            .set_functions(["complete_order"]) \
            .set_valid_steps(["order_complete"])
        
        # ORDER COMPLETE STATE
        default_context.add_step("order_complete") \
            .add_section("Current Task", "Order is complete") \
            .add_bullets("Final Steps", [
                "Thank the customer",
                "Wish them a great day",
                "If they want another order, use new_order"
            ]) \
            .set_functions(["new_order"]) \
            .set_valid_steps(["greeting"])
        
        # Helper functions
        def get_order_state(raw_data):
            """Get the authoritative order state for this call.

            The platform echoes `global_data` into every SWAIG request, but that
            snapshot is captured per TURN: if the model calls two mutating tools
            in one turn (which the prompt explicitly asks for - "a taco and a
            drink" -> two add_item calls), both requests carry the SAME snapshot
            and each returns its own set_global_data, so the second silently
            clobbers the first. The backend would end up with only the drink
            while the UI - driven by per-call events - showed both.

            So we keep the order in-process, keyed by call_id, and hand back the
            SAME dict object every time. Handlers mutate it in place, so
            successive tools in one turn compose instead of overwriting.
            global_data is still mirrored (see save_order_state) because the
            prompt interpolates ${global_data.order_state.*}.
            """
            # `or {}` (not a get-default): a present-but-null global_data /
            # order_state would otherwise blow up on .get() and the handler's
            # state change would be silently lost.
            raw_data = raw_data or {}
            global_data = raw_data.get('global_data') or {}
            call_id = raw_data.get('call_id')

            default = {
                "items": [],  # List of {sku, name, quantity, price, total}
                "total": 0.00,
                "subtotal": 0.00,
                "tax": 0.00,
                "order_number": None,
                "item_count": 0
            }

            # Live state for this call wins - it reflects every tool call so far,
            # including ones from the same turn whose set_global_data hasn't been
            # echoed back yet.
            if call_id:
                cached = _call_order_states.get(call_id)
                if cached is not None:
                    cached["ts"] = time.time()
                    return cached["state"], global_data

            # First touch of this call (or a restart mid-call): seed from the
            # platform snapshot, merged over the defaults so a partial/legacy
            # order_state heals instead of raising KeyError downstream.
            stored = global_data.get('order_state') or {}
            order_state = {**default, **stored}
            if not isinstance(order_state.get("items"), list):
                order_state["items"] = []

            # The wire form is compact (sku+quantity only - see
            # save_order_state), so rebuild name/price/description/total from
            # MENU. Items whose SKU no longer exists are dropped rather than
            # left half-formed.
            hydrated = []
            for it in order_state["items"]:
                if not isinstance(it, dict) or not it.get("sku"):
                    continue
                if it.get("name") and it.get("price") is not None:
                    hydrated.append(it)          # already full (in-process copy)
                    continue
                menu_item = next((d for _c, items in MENU.items()
                                  for s, d in items.items() if s == it["sku"]), None)
                if not menu_item:
                    continue
                qty = int(it.get("quantity", 1) or 1)
                hydrated.append({
                    "sku": it["sku"],
                    "name": menu_item["name"],
                    "description": menu_item.get("description", ""),
                    "price": menu_item["price"],
                    "quantity": qty,
                    "total": round(menu_item["price"] * qty, 2),
                })
            order_state["items"] = hydrated

            if call_id:
                _prune_order_states()
                _call_order_states[call_id] = {"state": order_state, "ts": time.time()}
            return order_state, global_data

        def save_order_state(result, order_state, global_data):
            """Mirror a COMPACT order state back to global_data.

            Size matters: SWAIG responses over ~1360 bytes come back as
            webhook_fail/parse_error (observed twice, both spliced at exactly
            byte 1360). Echoing the full item list - names, prices, per-item
            totals and 60-char descriptions - made the response grow with the
            order and blow that budget at 3-4 items.

            The prompt only interpolates ${global_data.order_state.item_count},
            .total and .order_number, and the authoritative copy now lives
            in-process (see get_order_state). So the wire carries just those
            scalars plus sku+quantity per item, which is enough to rebuild the
            whole order from MENU after a restart. ~90 bytes instead of ~590.
            """
            compact = {
                "items": [{"sku": i["sku"], "quantity": i["quantity"]}
                          for i in order_state.get("items", [])],
                "total": order_state.get("total", 0.0),
                "subtotal": order_state.get("subtotal", 0.0),
                "tax": order_state.get("tax", 0.0),
                "order_number": order_state.get("order_number"),
                "item_count": order_state.get("item_count", 0),
            }
            global_data['order_state'] = compact
            # Don't echo the platform's own caller id back (119 bytes of pure
            # overhead); it re-sends it on every request anyway.
            global_data.pop("caller_id_number", None)
            global_data.pop("caller_id_name", None)
            result.update_global_data(global_data)
            return result
        
        def calculate_totals(items):
            """Calculate order totals with tax"""
            # Decimal + ROUND_HALF_UP: float round() is round-half-even and
            # gave e.g. subtotal 9.95 -> tax 0.99 where a register says 1.00.
            _sub = Decimal(str(sum(item["total"] for item in items))).quantize(_CENT, rounding=ROUND_HALF_UP)
            _tax = (_sub * Decimal("0.10")).quantize(_CENT, rounding=ROUND_HALF_UP)  # 10% tax
            subtotal = float(_sub)
            tax = float(_tax)
            total = float((_sub + _tax).quantize(_CENT, rounding=ROUND_HALF_UP))
            return subtotal, tax, total
        
        def order_number_to_words(number):
            """Convert order number to individual spoken digits (e.g., 401 -> 'four zero one')"""
            digit_words = {
                '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
                '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
            }
            
            # Convert number to string and spell out each digit. Skip anything
            # that isn't a digit: str(None) -> "None" used to raise KeyError('N')
            # and take the whole handler down with it.
            digits = str(number if number is not None else "")
            spoken_digits = [digit_words[d] for d in digits if d in digit_words]
            return ' '.join(spoken_digits)
        
        def dollars_to_words(amount):
            """Convert dollar amount to spoken English"""
            # Handle zero / negative. A negative used to fall through and be
            # spoken as "zero dollars", so a value-destroying combo could be
            # announced as a saving.
            if amount is None or amount <= 0:
                return "zero dollars"
            
            # Split into dollars and cents
            dollars = int(amount)
            cents = round((amount - dollars) * 100)
            
            # Number words
            ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
            teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", 
                    "sixteen", "seventeen", "eighteen", "nineteen"]
            tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
            
            def number_to_words(n):
                """Convert number under 1000 to words"""
                if n == 0:
                    return ""
                elif n < 10:
                    return ones[n]
                elif n < 20:
                    return teens[n-10]
                elif n < 100:
                    return tens[n//10] + ("-" + ones[n%10] if n%10 > 0 else "")
                else:
                    hundred_part = ones[n//100] + " hundred"
                    remainder = n % 100
                    if remainder == 0:
                        return hundred_part
                    elif remainder < 10:
                        return hundred_part + " and " + ones[remainder]
                    elif remainder < 20:
                        return hundred_part + " and " + teens[remainder-10]
                    else:
                        return hundred_part + " and " + tens[remainder//10] + ("-" + ones[remainder%10] if remainder%10 > 0 else "")
            
            # Build the result
            result = []
            
            # Handle thousands
            if dollars >= 1000:
                thousands = dollars // 1000
                result.append(number_to_words(thousands) + " thousand")
                dollars = dollars % 1000
            
            # Handle hundreds and below
            if dollars > 0:
                result.append(number_to_words(dollars))
            
            # Add "dollar(s)"
            if result:
                dollar_amount = " ".join(result)
                if dollar_amount == "one":
                    result = ["one dollar"]
                else:
                    result = [" ".join(result) + " dollars"]
            else:
                result = []
            
            # Handle cents
            if cents > 0:
                if cents == 1:
                    cent_str = "one cent"
                else:
                    cent_str = number_to_words(cents) + " cents"
                
                if result:
                    result.append("and " + cent_str)
                else:
                    result = [cent_str]
            
            return " ".join(result) if result else "zero dollars"
        
        def check_combo_opportunity(items):
            """Check if current order qualifies for a combo upgrade"""
            if not items:
                return None
            
            # Count actual quantities of each item type
            taco_count = sum(item["quantity"] for item in items if "taco" in item["name"].lower() and "combo" not in item["name"].lower())
            burrito_count = sum(item["quantity"] for item in items if "burrito" in item["name"].lower() and "combo" not in item["name"].lower())
            chips_count = sum(item["quantity"] for item in items if "chips" in item["name"].lower() and "salsa" in item["name"].lower() and "combo" not in item["name"].lower())
            drink_count = sum(item["quantity"] for item in items if "small" in item["name"].lower() and "drink" in item["name"].lower() and "combo" not in item["name"].lower())
            
            # Check what combos we already have
            taco_combo_count = sum(item["quantity"] for item in items if "taco combo" in item["name"].lower())
            burrito_combo_count = sum(item["quantity"] for item in items if "burrito combo" in item["name"].lower())
            
            # Don't suggest upgrades for items already in combos
            # But allow suggesting different combo types
            
            # Check both combo opportunities and suggest the best one
            suggestions = []
            
            # Prices of the ACTUAL items in this order, cheapest-first, so the
            # quoted savings are real. Hardcoding 3.49/8.99 overstated it for
            # Bean Taco ($2.99) / Bean & Cheese Burrito ($6.99) - the burrito
            # case could even claim a saving on an upgrade that costs MORE.
            def _unit_prices(pred):
                prices = []
                for it in items:
                    n = it["name"].lower()
                    if pred(n) and "combo" not in n:
                        prices.extend([it["price"]] * int(it.get("quantity", 0)))
                return sorted(prices)

            taco_prices = _unit_prices(lambda n: "taco" in n)
            burrito_prices = _unit_prices(lambda n: "burrito" in n)
            chips_prices = _unit_prices(lambda n: "chips" in n and "salsa" in n)
            drink_prices = _unit_prices(lambda n: "small" in n and "drink" in n)
            taco_combo_price = MENU["combos"]["C001"]["price"]
            burrito_combo_price = MENU["combos"]["C002"]["price"]

            # Check for taco combo (2 tacos + 1 chips + 1 drink) - only if we don't already have taco combos
            if taco_combo_count == 0 and taco_count >= 2 and chips_count >= 1 and drink_count >= 1:
                current_total = sum(taco_prices[:2]) + chips_prices[0] + drink_prices[0]
                savings = round(current_total - taco_combo_price, 2)
                # Never pitch an "upgrade" that doesn't actually save money.
                if savings > 0:
                    suggestions.append(("taco", savings, f"💡 Great news! I can upgrade your 2 tacos, chips & salsa, and drink to a Taco Combo and save you {dollars_to_words(savings)}!"))
            
            # Check for burrito combo (1 burrito + 1 chips + 1 drink) - only if we don't already have burrito combos
            # Check if we have ADDITIONAL items for burrito combo beyond taco combo suggestion
            # If taco combo uses 1 chips and 1 drink, we need 2 total chips and 2 drinks for both combos
            min_chips_for_burrito = 2 if (taco_count >= 2 and len(suggestions) > 0) else 1
            min_drinks_for_burrito = 2 if (taco_count >= 2 and len(suggestions) > 0) else 1
            
            if burrito_combo_count == 0 and burrito_count >= 1 and chips_count >= min_chips_for_burrito and drink_count >= min_drinks_for_burrito:
                # Use the chips/drink not already claimed by the taco suggestion.
                _c = chips_prices[min_chips_for_burrito - 1]
                _d = drink_prices[min_drinks_for_burrito - 1]
                current_total = burrito_prices[0] + _c + _d
                savings = round(current_total - burrito_combo_price, 2)
                if savings > 0:
                    suggestions.append(("burrito", savings, f"💡 Great news! I can upgrade your burrito, chips & salsa, and drink to a Burrito Combo and save you {dollars_to_words(savings)}!"))
            
            # If we have multiple combo opportunities, suggest both!
            if len(suggestions) == 2:
                total_savings = suggestions[0][1] + suggestions[1][1]
                return f"💡 Amazing! You qualify for TWO combo upgrades! I can upgrade your tacos AND burrito meals to combos, saving you a total of {dollars_to_words(total_savings)}! Just say 'yes' or 'upgrade both' to save money."
            elif len(suggestions) == 1:
                return suggestions[0][2] + " Just say 'yes' to save money."
            
            return None
        
        def find_menu_item(item_name):
            """Find item in menu by name with TF-IDF vector matching or fuzzy matching"""
            item_lower = item_name.lower().strip()
            print(f"[DEBUG] Searching for: '{item_name}' (normalized: '{item_lower}')")
            
            # First check exact match with menu item names
            for category, items in MENU.items():
                for sku, item_data in items.items():
                    if item_lower == item_data["name"].lower():
                        print(f"[DEBUG] Exact match found: {item_data['name']} (SKU: {sku})")
                        return sku, item_data, category
            
            # Check aliases for exact match. Compare a de-pluralized form too:
            # aliases are singular ("water", "soda", "coke"), so "two waters" /
            # "make it three sodas" used to fall through to TF-IDF, score below
            # threshold and come back as "not on our menu".
            item_forms = singular_forms(item_lower)
            for sku, aliases in MENU_ALIASES.items():
                for alias in aliases:
                    if item_forms & singular_forms(alias.lower()):
                        print(f"[DEBUG] Alias match found: '{alias}' -> SKU: {sku}")
                        # Find the item data from the SKU
                        for category, items in MENU.items():
                            if sku in items:
                                print(f"[DEBUG] Resolved to: {items[sku]['name']}")
                                return sku, items[sku], category
            
            # Try TF-IDF matching if no exact match found
            if HAS_SKLEARN and self.vectorizer and self.menu_vectors is not None:
                try:
                    # Vectorize the user input
                    user_vector = self.vectorizer.transform([item_lower])
                    
                    # Calculate cosine similarities
                    similarities = cosine_similarity(user_vector, self.menu_vectors)[0]

                    # Combos share every word with their base items, so a bare
                    # "burrito" scored highest against "Burrito Combo" (0.422)
                    # and ordering "a burrito" silently added a $12.99 combo -
                    # while a bare "taco" lost to "Taco Combo" and matched
                    # NOTHING (0.372 < threshold). Unless the caller actually
                    # said "combo", only consider non-combo items; combos are
                    # reached explicitly or via upgrade_to_combo.
                    if "combo" not in item_lower:
                        similarities = similarities.copy()
                        for _i, (_sku, _data, _cat) in enumerate(self.sku_map):
                            if _cat == "combos" or "combo" in _data["name"].lower():
                                similarities[_i] = -1.0

                    # Get the best match
                    best_idx = np.argmax(similarities)
                    best_score = similarities[best_idx]
                    
                    print(f"[DEBUG] TF-IDF best match: {self.sku_map[best_idx][1]['name']} (score: {best_score:.3f})")
                    
                    # Return if similarity is high enough
                    # Threshold set to 0.42 for better balance between accuracy and flexibility
                    if best_score > 0.42:  # Threshold above 0.4 as requested
                        sku, item_data, category = self.sku_map[best_idx]
                        print(f"[DEBUG] TF-IDF match accepted: {item_data['name']} (SKU: {sku})")
                        return sku, item_data, category
                    else:
                        print(f"[DEBUG] TF-IDF score too low ({best_score:.3f} < 0.42), no match found")
                        # When TF-IDF is enabled, don't fall back to fuzzy matching
                        return None, None, None
                except Exception as e:
                    # If TF-IDF fails due to error, also don't fall back
                    print(f"[DEBUG] TF-IDF matching failed: {e}, no match found")
                    return None, None, None
            
            # Fallback to fuzzy matching (only if TF-IDF is not available)
            # Remove common words
            item_clean = item_lower.replace("the ", "").replace("a ", "").replace("an ", "").replace("just ", "").replace("plain ", "").strip()
            print(f"[DEBUG] Fuzzy matching with cleaned: '{item_clean}'")
            
            # Score-based matching
            best_match = None
            best_score = 0
            
            for category, items in MENU.items():
                for sku, item_data in items.items():
                    score = 0
                    item_name_lower = item_data["name"].lower()
                    
                    # Check if all words in input are in item name
                    input_words = item_clean.split()
                    if all(word in item_name_lower for word in input_words):
                        score += 80
                    
                    # Check aliases for partial matches
                    if sku in MENU_ALIASES:
                        for alias in MENU_ALIASES[sku]:
                            if item_clean in alias.lower():
                                score += 70
                                break
                            elif any(word in alias.lower() for word in input_words):
                                score += 40
                    
                    # Special cases for common requests
                    if "quesadilla" in item_clean and "quesadilla" in item_name_lower:
                        score += 50
                        if "cheese" not in item_clean and "cheese" in item_name_lower and "chicken" not in item_name_lower:
                            # Default to cheese quesadilla if just "quesadilla"
                            score += 30
                    
                    if "water" in item_clean and "water" in item_name_lower:
                        score += 90  # High score for water match
                    
                    if "combo" in item_clean and "combo" in item_name_lower:
                        score += 60
                        if "taco" in item_clean and "taco" in item_name_lower:
                            score += 40
                        elif "burrito" in item_clean and "burrito" in item_name_lower:
                            score += 40
                    
                    if "drink" in item_clean or "soda" in item_clean:
                        if "drink" in item_name_lower:
                            score += 50
                            if "small" in item_clean and "small" in item_name_lower:
                                score += 40
                            elif "large" in item_clean and "large" in item_name_lower:
                                score += 40
                            elif "small" not in item_clean and "large" not in item_clean:
                                # Default to small drink if size not specified
                                if "small" in item_name_lower:
                                    score += 20
                    
                    if "chips" in item_clean and "chips" in item_name_lower:
                        score += 50
                        if "guac" in item_clean and "guacamole" in item_name_lower:
                            score += 40
                        elif "salsa" in item_clean and "salsa" in item_name_lower:
                            score += 40
                        elif "salsa" not in item_clean and "guac" not in item_clean:
                            # Default to chips & salsa if not specified
                            if "salsa" in item_name_lower:
                                score += 20
                    
                    # Update best match if this score is higher
                    if score > best_score:
                        best_score = score
                        best_match = (sku, item_data, category)
            
            # Return best match if score is high enough
            if best_score >= 40:
                sku, item_data, category = best_match
                print(f"[DEBUG] Fuzzy match found: {item_data['name']} (SKU: {sku}, score: {best_score})")
                return best_match
            
            print(f"[DEBUG] No match found for '{item_name}'")
            return (None, None, None)
        
        # Core ordering functions
        @self.tool(
            name="add_item",
            wait_file="/keyspressing.mp3",
            description="Add an item to the order",
            parameters={
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "Name of the menu item"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "How many of this item to add. Use the exact number the customer said: 'two beef tacos' -> 2, 'a couple waters' -> 2, 'a few burritos' -> 3. If they name an item with no number, use 1.",
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": ["item_name", "quantity"]
            }
        )
        def add_item(args, raw_data):
            """Add item to order"""
            order_state, global_data = get_order_state(raw_data)
            print(f"[DEBUG] add_item args: {args}", flush=True)
            item_name = args["item_name"]
            quantity = args.get("quantity", 1)

            # Safety net: if the model dropped the count into the item name
            # ("two tacos", "a couple burritos") instead of the quantity arg,
            # recover it here so "two X" never silently becomes 1.
            _num_words = {
                "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "couple": 2, "few": 3, "several": 3, "dozen": 10,
            }
            try:
                _lead = re.match(r"^\s*(\d+|a couple of|a couple|a few|a dozen|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen)\s+(.*\S)\s*$", item_name.strip().lower())
                if quantity == 1 and _lead:
                    tok, rest = _lead.group(1), _lead.group(2)
                    # Strip a trailing "of" ("a couple of waters") before the
                    # lookup - taking the last word alone yielded "of" -> 1, so
                    # the parser missed its own headline case.
                    words = [w for w in tok.split() if w not in ("a", "an", "of")]
                    parsed = int(tok) if tok.isdigit() else _num_words.get(words[-1] if words else "", 1)
                    if parsed > 1:
                        quantity = parsed
                        item_name = rest
                        print(f"[DEBUG] add_item recovered quantity={quantity} from name -> '{item_name}'", flush=True)
            except Exception as _e:
                print(f"[DEBUG] quantity-parse skipped: {_e}", flush=True)

            # De-dupe the model's rapid duplicate tool-calls: of two identical
            # add_item calls within the window for the same call, only one
            # mutates. A real "two tacos" is a single quantity=2 call, so its
            # signature is unique and unaffected.
            # The slot is claimed AFTER validation (see _claim_dedup_slot below)
            # so a duplicate of a FAILED call re-runs and repeats the real error
            # instead of being acked - previously a duplicate of
            # add_item("cheeseburger") answered "Okay!" and the model could tell
            # the customer it was added.
            _call_id = (raw_data or {}).get("call_id") or "unknown"
            _sig = f"{item_name.strip().lower()}|{quantity}"

            def _claim_dedup_slot():
                with swaig_mutation_lock:
                    _now2 = time.time()
                    _recent_swaig_calls[_call_id] = {"sig": _sig, "ts": _now2, "response": None}
                    if len(_recent_swaig_calls) > 500:
                        for _k in [k for k, v in _recent_swaig_calls.items()
                                   if _now2 - v.get("ts", 0) > 300]:
                            _recent_swaig_calls.pop(_k, None)

            def _remember_response(text):
                """Replay the real answer if the duplicate lands after us."""
                with swaig_mutation_lock:
                    _e = _recent_swaig_calls.get(_call_id)
                    if _e and _e.get("sig") == _sig:
                        _e["response"] = text

            with swaig_mutation_lock:
                _prev = _recent_swaig_calls.get(_call_id)
                _now = time.time()
                if _prev and _prev.get("sig") == _sig and (_now - _prev.get("ts", 0)) < _SWAIG_DEDUP_WINDOW:
                    print(f"[DEBUG] add_item deduped rapid duplicate: {_sig} (call {_call_id})", flush=True)
                    # Replay what the first call actually said, so the model and
                    # the customer hear a consistent answer.
                    return SwaigFunctionResult(_prev.get("response") or "Okay!")

            # Enforce reasonable limits
            MAX_ITEMS_PER_TYPE = 20  # Max 20 of any single item
            MAX_TOTAL_ITEMS = 50     # Max 50 items total in order
            MAX_ORDER_VALUE = 500.00  # Max $500 order value
            
            # Validate quantity
            if quantity > 10:
                quantity = 10
                limited_message = f" (Limited to 10 per add)"
            else:
                limited_message = ""
            
            # Find the item in menu
            sku, item_data, category = find_menu_item(item_name)
            
            if not sku:
                # A bare category word ("a taco", "a burrito") is ambiguous, not
                # unavailable - several menu items match it. Saying "we don't
                # have that" was both wrong and confusing, so ask which one.
                # Per the prompt's rule we don't enumerate items; the customer
                # has the menu on screen.
                _matches = [d["name"] for _c, _items in MENU.items() if _c != "combos"
                            for _s, d in _items.items()
                            if singular_forms(item_name.strip().lower())
                            & {w for word in d["name"].lower().replace("&", " ").split()
                               for w in singular_forms(word)}]
                if len(_matches) > 1:
                    return SwaigFunctionResult(
                        f"We have a few {item_name.strip().lower()} options - "
                        "which one would you like? They're on the menu on your screen.")
                return SwaigFunctionResult(f"I couldn't find '{item_name}' on our menu. Please check the menu on your screen for available items.")
            
            # Check current total items
            current_total_items = sum(item["quantity"] for item in order_state["items"])
            if current_total_items >= MAX_TOTAL_ITEMS:
                return SwaigFunctionResult(f"Your order already has {current_total_items} items, which is our maximum. Please remove some items if you'd like to add more.")
            
            # Check if adding would exceed total limit
            if current_total_items + quantity > MAX_TOTAL_ITEMS:
                quantity = MAX_TOTAL_ITEMS - current_total_items
                limited_message = f" (Limited to {quantity} to stay within {MAX_TOTAL_ITEMS} item maximum)"
            
            # Check if item already in order
            existing_item = None
            for order_item in order_state["items"]:
                if order_item["sku"] == sku:
                    existing_item = order_item
                    break
            
            # Check per-item limit
            if existing_item:
                new_quantity = existing_item["quantity"] + quantity
                if new_quantity > MAX_ITEMS_PER_TYPE:
                    allowed_add = MAX_ITEMS_PER_TYPE - existing_item["quantity"]
                    if allowed_add <= 0:
                        return SwaigFunctionResult(f"You already have {existing_item['quantity']} {item_data['name']}s, which is the maximum of {MAX_ITEMS_PER_TYPE} per item type.")
                    quantity = allowed_add
                    new_quantity = MAX_ITEMS_PER_TYPE
                    limited_message = f" (Limited to {MAX_ITEMS_PER_TYPE} total per item type)"
            else:
                if quantity > MAX_ITEMS_PER_TYPE:
                    quantity = MAX_ITEMS_PER_TYPE
                    limited_message = f" (Limited to {MAX_ITEMS_PER_TYPE} per item type)"
            
            # Check if order would exceed max value
            potential_subtotal = sum(item["total"] for item in order_state["items"]) + (item_data["price"] * quantity)
            if potential_subtotal > MAX_ORDER_VALUE:
                # Calculate how many we can actually add
                remaining_value = MAX_ORDER_VALUE - sum(item["total"] for item in order_state["items"])
                max_quantity_by_value = int(remaining_value / item_data["price"])
                if max_quantity_by_value <= 0:
                    return SwaigFunctionResult(f"Adding this would exceed our {dollars_to_words(MAX_ORDER_VALUE)} order limit. Your current subtotal is {dollars_to_words(order_state['subtotal'])}.")
                quantity = min(quantity, max_quantity_by_value)
                limited_message = f" (Limited to {quantity} to stay within {dollars_to_words(MAX_ORDER_VALUE)} order limit)"
            
            # Validation passed and we're about to mutate: claim the dedup slot
            # now, so only genuine state changes suppress a following duplicate.
            _claim_dedup_slot()

            if existing_item:
                # Update quantity
                existing_item["quantity"] += quantity
                existing_item["total"] = round(existing_item["price"] * existing_item["quantity"], 2)
                response = f"Updated {item_data['name']} - now you have {existing_item['quantity']}{limited_message}."
            else:
                # Add new item
                new_item = {
                    "sku": sku,
                    "name": item_data["name"],
                    "description": item_data.get("description", ""),
                    "price": item_data["price"],
                    "quantity": quantity,
                    "total": round(item_data["price"] * quantity, 2)
                }
                order_state["items"].append(new_item)
                
                if quantity > 1:
                    response = f"Added {quantity} {item_data['name']}s to your order{limited_message}."
                else:
                    response = f"Added {item_data['name']} to your order{limited_message}."
            
            # Calculate new totals
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            # Check for combo opportunities after adding item
            combo_suggestion = check_combo_opportunity(order_state["items"])

            response += f" Your total is now {dollars_to_words(order_state['total'])}."

            # Add combo suggestion if found - but don't re-pitch the SAME offer on
            # every subsequent add. It used to nag once the order qualified; now a
            # given pitch is spoken once and only returns if the offer changes.
            if combo_suggestion:
                # Store a short fingerprint, NOT the pitch text: order_state is
                # echoed back into every later request/response, so keeping the
                # full sentence bloated each payload (and duplicated its emoji).
                _pitch_key = hashlib.sha1(combo_suggestion.encode()).hexdigest()[:12]
                if order_state.get("last_combo_pitch") != _pitch_key:
                    response += f"\n\n{combo_suggestion}"
                    order_state["last_combo_pitch"] = _pitch_key
            else:
                order_state["last_combo_pitch"] = None
            
            _remember_response(response)
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)

            # Send event to UI with all calculated values
            # Get the actual item from order_state to have correct quantity
            final_item = None
            for order_item in order_state["items"]:
                if order_item["sku"] == sku:
                    final_item = order_item
                    break
            
            event_data = {
                "type": "item_added",
                "item": {
                    "sku": sku,
                    "name": item_data["name"],
                    "description": item_data.get("description", ""),
                    "quantity": final_item["quantity"] if final_item else quantity,
                    "price": item_data["price"],
                    "total": final_item["total"] if final_item else round(item_data["price"] * quantity, 2)
                },
                "order_total": order_state["total"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "item_count": order_state["item_count"]
            }
            result.swml_user_event(event_data)
            
            # Change to taking_order if we're in greeting
            result.swml_change_step("taking_order")
            
            return result
        
        @self.tool(
            name="remove_item",
            wait_file="/keyspressing.mp3",
            description="Remove an item from the order",
            parameters={
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "Name of item to remove"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "How many to remove (default: 1, use -1 for all)",
                        "minimum": -1
                    }
                },
                "required": ["item_name"]
            }
        )
        def remove_item(args, raw_data):
            """Remove item from order"""
            order_state, global_data = get_order_state(raw_data)
            item_name = args["item_name"]
            quantity_to_remove = args.get("quantity", 1)  # Default to removing 1
            
            # First try to find the item in menu using fuzzy matching to get the proper name
            sku, item_data, category = find_menu_item(item_name)
            
            # Find item in order using either the fuzzy matched name or direct search
            target_item = None
            item_index = None
            
            if sku:
                # Use SKU for exact match if we found it in menu
                for i, order_item in enumerate(order_state["items"]):
                    if order_item["sku"] == sku:
                        target_item = order_item
                        item_index = i
                        break
            
            # If not found by SKU, try fuzzy match on the name in the order
            if not target_item:
                item_lower = item_name.lower()
                # Try exact substring match first. Collect ALL hits and prefer a
                # plain item over a combo: "remove the burrito" hit the
                # "Burrito Combo" line first purely because of list order and
                # deleted the combo the customer had just upgraded to.
                _subs = [(i, oi) for i, oi in enumerate(order_state["items"])
                         if item_lower in oi["name"].lower()]
                if _subs:
                    _plain = [c for c in _subs if "combo" not in c[1]["name"].lower()]
                    # Only prefer a plain item when the caller didn't say "combo".
                    _pick = (_plain or _subs)[0] if "combo" not in item_lower else _subs[0]
                    item_index, target_item = _pick
                
                # If still not found, try word-level matching (e.g. "bottles"
                # matches "Bottled Water"). This used to accept a substring hit
                # in EITHER direction on ANY word pair, so short words matched
                # almost anything and "remove the burrito" could delete a
                # Burrito Combo. Now: whole-word matches on de-pluralized words
                # of >= 4 chars, and plain items win over combos.
                if not target_item:
                    MIN_WORD = 4
                    search_forms = set()
                    for w in item_lower.split():
                        if len(w) >= MIN_WORD:
                            search_forms |= singular_forms(w)

                    candidates = []
                    for i, order_item in enumerate(order_state["items"]):
                        order_name_lower = order_item["name"].lower()
                        item_forms = set()
                        for w in order_name_lower.replace("&", " ").split():
                            if len(w) >= MIN_WORD:
                                item_forms |= singular_forms(w)
                        if search_forms & item_forms:
                            candidates.append((i, order_item))

                    if candidates:
                        # Prefer a non-combo line so "remove the burrito" takes
                        # the Beef Burrito, not the Burrito Combo the customer
                        # just paid to upgrade to.
                        plain = [c for c in candidates
                                 if "combo" not in c[1]["name"].lower()]
                        item_index, target_item = (plain or candidates)[0]
            
            if not target_item:
                return SwaigFunctionResult(f"You don't have {item_name} in your order.")
            
            # Handle quantity removal
            item_completely_removed = False
            
            if quantity_to_remove == -1 or quantity_to_remove >= target_item["quantity"]:
                # Remove all
                removed_item = order_state["items"].pop(item_index)
                quantity_removed = removed_item["quantity"]
                item_completely_removed = True
                response = f"Removed all {quantity_removed} {removed_item['name']}{'s' if quantity_removed > 1 else ''} from your order."
            else:
                # Remove partial quantity
                if quantity_to_remove <= 0:
                    quantity_to_remove = 1
                    
                quantity_removed = min(quantity_to_remove, target_item["quantity"])
                target_item["quantity"] -= quantity_removed
                target_item["total"] = round(target_item["price"] * target_item["quantity"], 2)
                
                if target_item["quantity"] == 0:
                    # If we removed all, remove the item
                    removed_item = order_state["items"].pop(item_index)
                    item_completely_removed = True
                else:
                    removed_item = target_item  # Keep reference for event
                    item_completely_removed = False
                
                response = f"Removed {quantity_removed} {target_item['name']}{'s' if quantity_removed > 1 else ''} from your order."
                if not item_completely_removed:
                    response += f" You still have {target_item['quantity']} remaining."
            
            # Recalculate totals
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            if order_state["total"] > 0:
                response += f" Your new total is {dollars_to_words(order_state['total'])}."
            else:
                response += " Your order is now empty."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send event to UI based on whether item was completely removed
            if item_completely_removed:
                # Item completely removed
                result.swml_user_event({
                    "type": "item_removed",
                    "sku": removed_item["sku"],
                    "order_total": order_state["total"],
                    "subtotal": order_state["subtotal"],
                    "tax": order_state["tax"],
                    "item_count": order_state["item_count"]
                })
            else:
                # Item still exists with reduced quantity
                result.swml_user_event({
                    "type": "quantity_modified",
                    "sku": removed_item["sku"],
                    "new_quantity": removed_item["quantity"],
                    "new_total": removed_item["total"],
                    "order_total": order_state["total"],
                    "subtotal": order_state["subtotal"],
                    "tax": order_state["tax"],
                    "item_count": order_state["item_count"]
                })
            
            return result
        
        # (Continuing with the rest of the tools as in the original...)
        # I'll just add the skeletons to keep the file complete
        
        @self.tool(
            name="modify_quantity",
            wait_file="/keyspressing.mp3",
            description="Change the quantity of an item already in the order",
            parameters={
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "Name of the item to modify"
                    },
                    "new_quantity": {
                        "type": "integer",
                        "description": "New quantity (0 to remove)",
                        "minimum": 0,
                        "maximum": 10
                    }
                },
                "required": ["item_name", "new_quantity"]
            }
        )
        def modify_quantity(args, raw_data):
            """Modify quantity of an existing item"""
            order_state, global_data = get_order_state(raw_data)
            item_name = args["item_name"]
            new_quantity = args["new_quantity"]
            
            # Same limits as add_item
            MAX_ITEMS_PER_TYPE = 20
            MAX_TOTAL_ITEMS = 50
            MAX_ORDER_VALUE = 500.00
            
            # Find item. Resolve through the same alias/TF-IDF matcher add_item
            # and remove_item use, so "make it two cokes" / "three sodas" /
            # "a couple of guac" work instead of "you don't have that". Falls
            # back to the old substring scan when the matcher can't resolve.
            item_lower = item_name.lower()
            modified_item = None
            target_sku, _target_data, _target_cat = find_menu_item(item_name)
            if target_sku:
                for order_item in order_state["items"]:
                    if order_item["sku"] == target_sku:
                        # Match by SKU so "taco" can't hit "Taco Combo" first.
                        item_lower = order_item["name"].lower()
                        break

            for order_item in order_state["items"]:
                if (target_sku and order_item["sku"] == target_sku) or \
                   (not target_sku and item_lower in order_item["name"].lower()):
                    if new_quantity == 0:
                        # Remove item
                        order_state["items"].remove(order_item)
                        response = f"Removed {order_item['name']} from your order."
                    else:
                        # Validate new quantity
                        if new_quantity > MAX_ITEMS_PER_TYPE:
                            new_quantity = MAX_ITEMS_PER_TYPE
                            response = f"Changed {order_item['name']} quantity to {new_quantity} (maximum per item type)."
                        else:
                            response = f"Changed {order_item['name']} quantity to {new_quantity}."
                        
                        # Check total items limit
                        current_total = sum(item["quantity"] for item in order_state["items"]) - order_item["quantity"]
                        if current_total + new_quantity > MAX_TOTAL_ITEMS:
                            new_quantity = MAX_TOTAL_ITEMS - current_total
                            response = f"Changed {order_item['name']} quantity to {new_quantity} (to stay within {MAX_TOTAL_ITEMS} item limit)."
                        
                        # Check value limit
                        potential_subtotal = sum(item["total"] for item in order_state["items"]) - order_item["total"] + (order_item["price"] * new_quantity)
                        if potential_subtotal > MAX_ORDER_VALUE:
                            max_quantity_by_value = int((MAX_ORDER_VALUE - (sum(item["total"] for item in order_state["items"]) - order_item["total"])) / order_item["price"])
                            # A clamp of <= 0 would leave a quantity-0 line item in
                            # the order (items vs item_count drift, "quantity to 0"
                            # spoken). Refuse the change instead. add_item already
                            # guards this; modify_quantity didn't.
                            if max_quantity_by_value <= 0:
                                return SwaigFunctionResult(
                                    f"That would put the order over our {dollars_to_words(MAX_ORDER_VALUE)} limit, "
                                    f"so I left {order_item['name']} as it was.")
                            new_quantity = max_quantity_by_value
                            response = f"Changed {order_item['name']} quantity to {new_quantity} (to stay within {dollars_to_words(MAX_ORDER_VALUE)} order limit)."
                        
                        # Update quantity
                        order_item["quantity"] = new_quantity
                        order_item["total"] = round(order_item["price"] * new_quantity, 2)
                    modified_item = order_item
                    break
            
            if not modified_item:
                return SwaigFunctionResult(f"You don't have {item_name} in your order.")
            
            # Recalculate totals
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            if order_state["total"] > 0:
                response += f" Your new total is {dollars_to_words(order_state['total'])}."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send event to UI
            result.swml_user_event({
                "type": "quantity_modified",
                "sku": modified_item.get("sku"),
                "new_quantity": new_quantity if new_quantity > 0 else 0,
                "new_total": modified_item["total"] if modified_item and new_quantity > 0 else 0,
                "order_total": order_state["total"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "item_count": order_state["item_count"]
            })
            
            return result
        
        @self.tool(
            name="review_order",
            wait_file="/keyspressing.mp3",
            description="Review the current order",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def review_order(args, raw_data):
            """Display current order with totals"""
            order_state, global_data = get_order_state(raw_data)
            
            if not order_state["items"]:
                return SwaigFunctionResult("Your order is empty. What would you like to order?")
            
            # Just give the total - they can see the details on screen
            response = f"Your current total is {dollars_to_words(order_state['total'])}. You can see your order on the screen."
            
            result = SwaigFunctionResult(response)
            
            # Send complete order to UI
            result.swml_user_event({
                "type": "order_reviewed",
                "items": order_state["items"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "total": order_state["total"]
            })
            
            return result
        
        @self.tool(
            name="finalize_order",
            wait_file="/keyspressing.mp3",
            description="Finalize order and move to confirmation",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def finalize_order(args, raw_data):
            """Move to order confirmation"""
            order_state, global_data = get_order_state(raw_data)
            
            if not order_state["items"]:
                return SwaigFunctionResult("Your order is empty. Please add some items first!")
            
            # Simple confirmation - they can see the order on screen
            response = f"Alright, your total comes to {dollars_to_words(order_state['total'])}. Does everything on the screen look correct?"
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Move to confirming state
            result.swml_change_step("confirming_order")
            
            # Send event to UI with complete order details
            result.swml_user_event({
                "type": "order_finalized",
                "items": order_state["items"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "total": order_state["total"],
                "item_count": order_state["item_count"]
            })
            
            return result
        
        @self.tool(
            name="process_payment",
            wait_file="/keyspressing.mp3",
            description="Process the payment",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def process_payment(args, raw_data):
            """Process payment and generate order number"""
            order_state, global_data = get_order_state(raw_data)

            # Don't take payment on an empty order. Reachable because remove_item
            # is available during confirmation: dropping the last item and then
            # saying "yes, that's right" would otherwise assign an order number
            # and announce a zero-dollar total.
            if not order_state["items"]:
                result = SwaigFunctionResult(
                    "It looks like your order is empty. Let's add something first - "
                    "what would you like?")
                result.swml_change_step("taking_order")
                return result

            # Generate order number
            order_state["order_number"] = random.randint(100, 999)
            
            response = f"Perfect! Your order number is {order_number_to_words(order_state['order_number'])}.\n"
            response += f"Your total is {dollars_to_words(order_state['total'])}.\n\n"
            response += "Please pull forward to the first window to pay."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Move to payment processing
            result.swml_change_step("payment_processing")
            
            # Send event to UI
            result.swml_user_event({
                "type": "payment_started",
                "order_number": order_state["order_number"],
                "total": order_state["total"]
            })
            
            return result
        
        @self.tool(
            name="complete_order",
            wait_file="/keyspressing.mp3",
            description="Complete the order",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def complete_order(args, raw_data):
            """Mark order as complete"""
            order_state, global_data = get_order_state(raw_data)

            order_number = order_state.get('order_number')

            # The model re-fires complete_order (observed twice, 0.5s apart).
            # Once the order is finished, just re-acknowledge instead of
            # re-emitting the completion event / re-clearing state.
            if order_state.get("completed") and order_number:
                return SwaigFunctionResult(
                    f"Order number {order_number_to_words(order_number)} is all set. Have a great day!")

            # The model sometimes jumps straight here and skips process_payment
            # (observed in production), leaving order_number None. That used to
            # raise KeyError('N') inside order_number_to_words, so the order was
            # never cleared, the step never advanced and the UI never got
            # 'order_completed' - the model then retried and crashed again.
            # Assign a number here so the call still finishes cleanly.
            if not order_number:
                order_number = random.randint(100, 999)
                order_state['order_number'] = order_number
                logger.warning("complete_order called without a prior process_payment; "
                               "assigned order number %s", order_number)

            # The goodbye goes out as a `say` ACTION, not as the response text.
            # Response text is spoken by the LLM asynchronously, so the hangup
            # action fired while it was still talking and the call cut off
            # mid-sentence. Actions run in order, so say-then-hangup guarantees
            # the caller hears the whole thing (same pattern jmac uses before
            # its transfer).
            goodbye = (
                f"Thank you for your order! Order number "
                f"{order_number_to_words(order_number)} is complete. "
                "We'll see you at the window - thank you for choosing Holy Guacamole!"
            )
            # Kept short and internal: this is what the model sees, not the caller.
            response = "Order complete. The goodbye is being played and the call will end."
            
            # Clear the order but keep the order number
            order_state["completed"] = True   # so a re-fired complete_order is a no-op
            order_state["items"] = []
            order_state["total"] = 0.00
            order_state["subtotal"] = 0.00
            order_state["tax"] = 0.00
            order_state["item_count"] = 0
            # Keep order_number to display it
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Move to complete state
            result.swml_change_step("order_complete")
            
            # Send event to UI
            result.swml_user_event({
                "type": "order_completed",
                "order_number": order_number
            })

            # Speak the goodbye, THEN end the call. Actions execute in order, so
            # the hangup waits for the say to finish.
            result.say(goodbye)
            result.hangup()

            return result
        
        @self.tool(
            name="cancel_order",
            wait_file="/keyspressing.mp3",
            description="Clear/cancel the entire order. Use when customer says 'cancel', 'start over', 'never mind', or before adding a single item when they say 'I only want X'",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def cancel_order(args, raw_data):
            """Cancel and reset order"""
            order_state, global_data = get_order_state(raw_data)
            
            # Reset order
            order_state["items"] = []
            order_state["total"] = 0.00
            order_state["subtotal"] = 0.00
            order_state["tax"] = 0.00
            order_state["order_number"] = None
            order_state["item_count"] = 0
            order_state["completed"] = False       # a fresh order can complete again
            order_state["last_combo_pitch"] = None # allow the combo pitch again
            
            # Check current state to determine response
            current_step = global_data.get("current_step", "greeting")
            if current_step == "taking_order":
                # Stay in taking_order for "never mind, I just want X" scenarios
                # (no step change needed)
                response = "Alright, I've cleared everything. What would you like?"
                result = SwaigFunctionResult(response)
                save_order_state(result, order_state, global_data)
            else:
                # From confirming_order, go back to taking a fresh order
                # (greeting is not a valid transition from confirming_order)
                response = "Order cancelled. How can I help you today?"
                result = SwaigFunctionResult(response)
                save_order_state(result, order_state, global_data)
                result.swml_change_step("taking_order")
            
            # Send event to UI
            result.swml_user_event({
                "type": "order_cancelled",
                "items": [],
                "subtotal": 0,
                "tax": 0,
                "total": 0
            })
            
            return result
        
        @self.tool(
            name="new_order",
            wait_file="/keyspressing.mp3",
            description="Start a new order",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def new_order(args, raw_data):
            """Start fresh order"""
            order_state, global_data = get_order_state(raw_data)
            
            # Reset order
            order_state["items"] = []
            order_state["total"] = 0.00
            order_state["subtotal"] = 0.00
            order_state["tax"] = 0.00
            order_state["order_number"] = None
            order_state["item_count"] = 0
            order_state["completed"] = False       # a fresh order can complete again
            order_state["last_combo_pitch"] = None # allow the combo pitch again
            
            response = "Welcome back to Holy Guacamole! What can I get started for you?"
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Go to greeting
            result.swml_change_step("greeting")
            
            # Send event to UI
            result.swml_user_event({
                "type": "new_order"
            })
            
            return result
        
        @self.tool(
            name="upgrade_to_combo",
            wait_file="/keyspressing.mp3",
            description="Upgrade individual items to a combo meal",
            parameters={
                "type": "object",
                "properties": {
                    "combo_type": {
                        "type": "string",
                        "description": "Type of combo: 'taco', 'burrito', or 'both'"
                    }
                },
                "required": ["combo_type"]
            }
        )
        def upgrade_to_combo(args, raw_data):
            """Replace individual items with a combo meal to save money"""
            order_state, global_data = get_order_state(raw_data)
            combo_type = args["combo_type"].lower()
            
            # Handle "both" by upgrading both combos
            if combo_type == "both":
                # We'll process both taco and burrito combos
                combos_to_add = []
                removed_items = []
                items_to_keep = []
                
                # First pass: count what we have
                taco_count = sum(item["quantity"] for item in order_state["items"] if "taco" in item["name"].lower() and "combo" not in item["name"].lower())
                burrito_count = sum(item["quantity"] for item in order_state["items"] if "burrito" in item["name"].lower() and "combo" not in item["name"].lower())
                chips_count = sum(item["quantity"] for item in order_state["items"] if "chips" in item["name"].lower() and "salsa" in item["name"].lower() and "combo" not in item["name"].lower())
                drink_count = sum(item["quantity"] for item in order_state["items"] if "small" in item["name"].lower() and "drink" in item["name"].lower() and "combo" not in item["name"].lower())
                
                # Calculate how many of each combo we can make
                max_taco_combos = min(taco_count // 2, chips_count, drink_count)
                # After taco combos, recalculate remaining items for burrito combos
                remaining_chips = chips_count - max_taco_combos
                remaining_drinks = drink_count - max_taco_combos
                max_burrito_combos = min(burrito_count, remaining_chips, remaining_drinks)

                # Nothing actually qualifies: the single-combo paths guard this,
                # the "both" path didn't, and it produced the nonsense response
                # "I've upgraded your order to , saving you zero dollars!".
                if max_taco_combos == 0 and max_burrito_combos == 0:
                    return SwaigFunctionResult(
                        "You don't have the right items for a combo yet. A Taco Combo needs "
                        "2 tacos, chips & salsa and a small drink; a Burrito Combo needs a "
                        "burrito, chips & salsa and a small drink. Want me to add what's missing?")

                # Track what we need to remove
                tacos_to_remove = max_taco_combos * 2
                burritos_to_remove = max_burrito_combos
                chips_to_remove = max_taco_combos + max_burrito_combos
                drinks_to_remove = max_taco_combos + max_burrito_combos
                
                # Process each item
                for item in order_state["items"]:
                    item_lower = item["name"].lower()
                    item_to_keep = item.copy()
                    
                    if "taco" in item_lower and "combo" not in item_lower and tacos_to_remove > 0:
                        if item["quantity"] <= tacos_to_remove:
                            removed_items.append(item)
                            tacos_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = tacos_to_remove
                            removed_item["total"] = round(removed_item["price"] * tacos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= tacos_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            tacos_to_remove = 0
                    
                    elif "burrito" in item_lower and "combo" not in item_lower and burritos_to_remove > 0:
                        if item["quantity"] <= burritos_to_remove:
                            removed_items.append(item)
                            burritos_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = burritos_to_remove
                            removed_item["total"] = round(removed_item["price"] * burritos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= burritos_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            burritos_to_remove = 0
                    
                    elif "chips" in item_lower and "salsa" in item_lower and "combo" not in item_lower and chips_to_remove > 0:
                        if item["quantity"] <= chips_to_remove:
                            removed_items.append(item)
                            chips_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = chips_to_remove
                            removed_item["total"] = round(removed_item["price"] * chips_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= chips_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            chips_to_remove = 0
                    
                    elif "small" in item_lower and "drink" in item_lower and "combo" not in item_lower and drinks_to_remove > 0:
                        if item["quantity"] <= drinks_to_remove:
                            removed_items.append(item)
                            drinks_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = drinks_to_remove
                            removed_item["total"] = round(removed_item["price"] * drinks_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= drinks_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            drinks_to_remove = 0
                    
                    if item_to_keep["quantity"] > 0:
                        items_to_keep.append(item_to_keep)
                
                # Add both combos with proper quantities
                if max_taco_combos > 0:
                    combos_to_add.append({
                        "sku": "C001",
                        "name": "Taco Combo",
                        "description": "2 tacos (your choice) + chips & salsa + small drink",
                        "price": MENU["combos"]["C001"]["price"],
                        "quantity": max_taco_combos,
                        "total": round(MENU["combos"]["C001"]["price"] * max_taco_combos, 2)
                    })
                
                if max_burrito_combos > 0:
                    combos_to_add.append({
                        "sku": "C002",
                        "name": "Burrito Combo",
                        "description": "Any burrito + chips & salsa + small drink",
                        "price": MENU["combos"]["C002"]["price"],
                        "quantity": max_burrito_combos,
                        "total": round(MENU["combos"]["C002"]["price"] * max_burrito_combos, 2)
                    })
                
                # Calculate total savings
                removed_total = sum(item["total"] for item in removed_items)
                combo_total = sum(combo["total"] for combo in combos_to_add)
                savings = round(removed_total - combo_total, 2)
                
                # Update order
                items_to_keep.extend(combos_to_add)
                order_state["items"] = items_to_keep
                order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
                order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
                
                # Build response
                combo_descriptions = []
                for c in combos_to_add:
                    if c["quantity"] > 1:
                        combo_descriptions.append(f"{c['quantity']} {c['name']}s")
                    else:
                        combo_descriptions.append(f"a {c['name']}")
                combo_names = " and ".join(combo_descriptions)
                response = f"Awesome! I've upgraded your order to {combo_names}, saving you {dollars_to_words(savings)}!"
                response += f" Your new total is {dollars_to_words(order_state['total'])}."
                
                result = SwaigFunctionResult(response)
                save_order_state(result, order_state, global_data)
                
                # Send event
                result.swml_user_event({
                    "type": "combo_upgraded",
                    "items": order_state["items"],
                    "removed_items": [{"name": item["name"], "quantity": item["quantity"]} for item in removed_items],
                    "added_combos": combos_to_add,
                    "subtotal": order_state["subtotal"],
                    "tax": order_state["tax"],
                    "total": order_state["total"],
                    "savings": savings,
                    "item_count": order_state["item_count"]
                })
                
                return result
            
            # Original single combo upgrade logic - now handles multiple combos
            removed_items = []
            items_to_keep = []
            
            if combo_type == "taco":
                # Calculate how many taco combos we can make
                # Need: 2 tacos, 1 chips & salsa, 1 small drink per combo
                taco_count = sum(item["quantity"] for item in order_state["items"] if "taco" in item["name"].lower() and "combo" not in item["name"].lower())
                chips_count = sum(item["quantity"] for item in order_state["items"] if "chips" in item["name"].lower() and "salsa" in item["name"].lower() and "combo" not in item["name"].lower())
                drink_count = sum(item["quantity"] for item in order_state["items"] if "small" in item["name"].lower() and "drink" in item["name"].lower() and "combo" not in item["name"].lower())
                
                # Maximum combos we can make
                max_combos = min(taco_count // 2, chips_count, drink_count)
                
                if max_combos <= 0:
                    return SwaigFunctionResult("You don't have enough items for a Taco Combo. You need 2 tacos, chips & salsa, and a small drink.")
                
                # Track how many of each item we need to remove
                tacos_to_remove = max_combos * 2
                chips_to_remove = max_combos
                drinks_to_remove = max_combos
                
                # Process each item
                for item in order_state["items"]:
                    item_lower = item["name"].lower()
                    
                    # Remove tacos
                    if "taco" in item_lower and "combo" not in item_lower and tacos_to_remove > 0:
                        if item["quantity"] <= tacos_to_remove:
                            removed_items.append(item)
                            tacos_to_remove -= item["quantity"]
                        else:
                            # Remove only what we need, keep the rest
                            removed_item = item.copy()
                            removed_item["quantity"] = tacos_to_remove
                            removed_item["total"] = round(removed_item["price"] * tacos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            # Keep the remaining tacos
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - tacos_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            tacos_to_remove = 0
                    # Remove chips & salsa
                    elif "chips" in item_lower and "salsa" in item_lower and "combo" not in item_lower and chips_to_remove > 0:
                        if item["quantity"] <= chips_to_remove:
                            removed_items.append(item)
                            chips_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = chips_to_remove
                            removed_item["total"] = round(removed_item["price"] * chips_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - chips_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            chips_to_remove = 0
                    # Remove small drinks
                    elif "small" in item_lower and "drink" in item_lower and "combo" not in item_lower and drinks_to_remove > 0:
                        if item["quantity"] <= drinks_to_remove:
                            removed_items.append(item)
                            drinks_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = drinks_to_remove
                            removed_item["total"] = round(removed_item["price"] * drinks_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - drinks_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            drinks_to_remove = 0
                    else:
                        items_to_keep.append(item)
                
                # Add Taco Combo(s)
                combo = {
                    "sku": "C001",
                    "name": "Taco Combo",
                    "description": "2 tacos (your choice) + chips & salsa + small drink",
                    "price": MENU["combos"]["C001"]["price"],
                    "quantity": max_combos,
                    "total": round(MENU["combos"]["C001"]["price"] * max_combos, 2)
                }
                
            elif combo_type == "burrito":
                # Calculate how many burrito combos we can make
                # Need: 1 burrito, 1 chips & salsa, 1 small drink per combo
                burrito_count = sum(item["quantity"] for item in order_state["items"] if "burrito" in item["name"].lower() and "combo" not in item["name"].lower())
                chips_count = sum(item["quantity"] for item in order_state["items"] if "chips" in item["name"].lower() and "salsa" in item["name"].lower() and "combo" not in item["name"].lower())
                drink_count = sum(item["quantity"] for item in order_state["items"] if "small" in item["name"].lower() and "drink" in item["name"].lower() and "combo" not in item["name"].lower())
                
                # Maximum combos we can make
                max_combos = min(burrito_count, chips_count, drink_count)
                
                if max_combos <= 0:
                    return SwaigFunctionResult("You don't have enough items for a Burrito Combo. You need a burrito, chips & salsa, and a small drink.")
                
                # Track how many of each item we need to remove
                burritos_to_remove = max_combos
                chips_to_remove = max_combos
                drinks_to_remove = max_combos
                
                # Process each item
                for item in order_state["items"]:
                    item_lower = item["name"].lower()
                    
                    # Remove burritos
                    if "burrito" in item_lower and "combo" not in item_lower and burritos_to_remove > 0:
                        if item["quantity"] <= burritos_to_remove:
                            removed_items.append(item)
                            burritos_to_remove -= item["quantity"]
                        else:
                            # Remove only what we need, keep the rest
                            removed_item = item.copy()
                            removed_item["quantity"] = burritos_to_remove
                            removed_item["total"] = round(removed_item["price"] * burritos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            # Keep the remaining burritos
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - burritos_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            burritos_to_remove = 0
                    # Remove chips & salsa
                    elif "chips" in item_lower and "salsa" in item_lower and "combo" not in item_lower and chips_to_remove > 0:
                        if item["quantity"] <= chips_to_remove:
                            removed_items.append(item)
                            chips_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = chips_to_remove
                            removed_item["total"] = round(removed_item["price"] * chips_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - chips_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            chips_to_remove = 0
                    # Remove small drinks
                    elif "small" in item_lower and "drink" in item_lower and "combo" not in item_lower and drinks_to_remove > 0:
                        if item["quantity"] <= drinks_to_remove:
                            removed_items.append(item)
                            drinks_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = drinks_to_remove
                            removed_item["total"] = round(removed_item["price"] * drinks_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - drinks_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            drinks_to_remove = 0
                    else:
                        items_to_keep.append(item)
                
                # Add Burrito Combo(s)
                combo = {
                    "sku": "C002",
                    "name": "Burrito Combo",
                    "description": "Any burrito + chips & salsa + small drink",
                    "price": MENU["combos"]["C002"]["price"],
                    "quantity": max_combos,
                    "total": round(MENU["combos"]["C002"]["price"] * max_combos, 2)
                }
            else:
                return SwaigFunctionResult("I can only upgrade to taco or burrito combos.")
            
            # Calculate savings
            removed_total = sum(item["total"] for item in removed_items)
            savings = round(removed_total - combo["total"], 2)
            
            # Update order
            items_to_keep.append(combo)
            order_state["items"] = items_to_keep
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            # Build response
            if combo["quantity"] > 1:
                combo_text = f"{combo['quantity']} {combo['name']}s"
            else:
                combo_text = f"the {combo['name']}"
            
            if savings > 0:
                response = f"Great choice! I've upgraded your order to {combo_text} and saved you {dollars_to_words(savings)}!"
            else:
                response = f"I've upgraded your order to {combo_text}."
            response += f" Your new total is {dollars_to_words(order_state['total'])}."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send comprehensive event to UI
            result.swml_user_event({
                "type": "combo_upgraded",
                "items": order_state["items"],
                "removed_items": [{"name": item["name"], "quantity": item["quantity"]} for item in removed_items],
                "added_combo": combo,
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "total": order_state["total"],
                "savings": savings,
                "item_count": order_state["item_count"]
            })
            
            return result
        
        # Voice is configured dynamically in on_swml_request based on user selection
        
        # Add speech hints
        self.add_hints([
            "taco", "burrito", "quesadilla",
            "beef", "chicken", "bean", "cheese",
            "chips", "salsa", "guacamole",
            "drink", "water", "combo",
            "small", "large",
            "yes", "no", "done", "finished",
            "add", "remove", "cancel"
        ])
        
        # Set conversation parameters (video URLs will be set dynamically)
#        self.set_param("turn_detection_timeout", "300")
#        self.set_param("end_of_speech_timeout", "2000")

        self.set_prompt_llm_params(
            temperature=0.1,
            top_p=0.1
        )

        # Optional post-prompt URL from environment
        post_prompt_url = os.environ.get("POST_PROMPT_URL")
        if post_prompt_url:
            self.set_post_prompt("Summarize the conversation, including all the details about the food order and any special requests.")
            self.set_post_prompt_url(post_prompt_url)

        # Initialize global data
        self.set_global_data({
            "restaurant": "Holy Guacamole!",
            "order_state": {
                "items": [],
                "total": 0.00,
                "subtotal": 0.00,
                "tax": 0.00,
                "order_number": None,
                "item_count": 0
            }
        })
    
    def on_swml_request(self, request_data=None, callback_path=None, request=None):
        """Override to dynamically set video URLs based on request origin"""
        # Get the host from the request object if available
        host = None
        
        if request:
            # Try to get host from the Starlette request headers
            headers = dict(request.headers)
            host = headers.get('host') or headers.get('x-forwarded-host')
            
            # Check if we're behind a proxy with x-forwarded-proto
            protocol = headers.get('x-forwarded-proto', 'https')
            
            # Override protocol for local development
            if host and ('localhost' in host or '127.0.0.1' in host):
                protocol = 'http'
        
        # If we found a host, update the video URLs
        if host:
            base_url = f"{protocol}://{host}"
            # Use set_param to set individual params instead of set_params to avoid clobbering
            self.set_param("video_idle_file", f"{base_url}/sigmond_cc_idle.mp4")
            self.set_param("video_talking_file", f"{base_url}/sigmond_cc_talking.mp4")
            print(f"Set video URLs to use host: {base_url}")
        else:
            # No Host header — fall back to the configured public base URL
            # (same precedence as the SWML handler setup above) instead of a
            # hardcoded personal dev tunnel.
            base_url = os.getenv("SWML_PROXY_URL_BASE", os.getenv("APP_URL", "")).rstrip("/")
            if base_url:
                self.set_param("video_idle_file", f"{base_url}/sigmond_cc_idle.mp4")
                self.set_param("video_talking_file", f"{base_url}/sigmond_cc_talking.mp4")
                print(f"No host header found, using configured base URL: {base_url}")
            else:
                print("No host header and no SWML_PROXY_URL_BASE/APP_URL — leaving video URLs unset")

        # Resolve the voice for THIS call. /get_token records the caller's pick
        # against the guest id of the token it minted, and the call arrives from
        # sip:guest-<uuid>@... - so we can match them up instead of reading a
        # single process-wide file that any other caller could overwrite
        # mid-order. Falls back to the shared file (inbound PSTN has no guest
        # id) and then to the default.
        selected_voice = None
        guest_id = _guest_id_from_request(request_data)
        if guest_id:
            selected_voice = get_voice_for_guest(guest_id)
            if selected_voice:
                print(f"Using voice for guest {guest_id[:18]}: {selected_voice}", flush=True)
        if not selected_voice:
            selected_voice = get_stored_voice() or DEFAULT_VOICE
            print(f"Using voice from store (no per-call match): {selected_voice}", flush=True)
        default_voice = DEFAULT_VOICE

        # Clear any existing languages to prevent accumulation across calls
        if hasattr(self, '_languages'):
            self._languages = []

        # Configure voice dynamically
        self.add_language(
            name="English",
            code="en-US",
            voice=selected_voice
        )
        self._languages[-1]["params"] = {"streaming": True}

        # Call parent implementation
        return super().on_swml_request(request_data, callback_path, request)
    
    def _initialize_tfidf(self):
        """Initialize TF-IDF vectorizer with menu items"""
        corpus = []
        self.sku_map = []
        
        # Build corpus from menu items
        for category, items in MENU.items():
            for sku, item in items.items():
                # Combine name, description, and aliases for better matching
                text_parts = [item['name']]
                
                # Add description if available
                if 'description' in item:
                    text_parts.append(item['description'])
                
                # Add aliases if available
                if sku in MENU_ALIASES:
                    text_parts.extend(MENU_ALIASES[sku])
                
                # Add category name for context
                text_parts.append(category)
                
                # Create combined text
                text = " ".join(text_parts).lower()
                corpus.append(text)
                self.sku_map.append((sku, item, category))
        
        # Create and fit vectorizer
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),  # Use unigrams and bigrams
            stop_words=None,  # Keep all words for menu matching
            max_features=200,
            sublinear_tf=True
        )
        self.menu_vectors = self.vectorizer.fit_transform(corpus)
    


def create_server():
    """Create AgentServer with static file mounting."""
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))

    server = AgentServer(host=host, port=port)
    server.register(HolyGuacamoleAgent(), "/swml")

    # Health check endpoints for deployment verification
    @server.app.get("/health")
    def health_check():
        """Health check endpoint for deployment verification."""
        return {"status": "healthy", "agent": "holyguacamole"}

    @server.app.get("/ready")
    def ready_check():
        """Readiness check - verifies SWML handler is configured."""
        if swml_handler_info.get("address"):
            return {"status": "ready", "address": swml_handler_info["address"]}
        return {"status": "initializing"}

    # ─────────────────────────────────────────────────────────────────────────
    # Token Generation Endpoint
    # This is how web clients get authentication tokens for WebRTC calls
    # ─────────────────────────────────────────────────────────────────────────
    @server.app.get("/get_token")
    def get_token(voice: str = DEFAULT_VOICE):
        """
        Generate a guest token for the web client.

        This endpoint:
        1. Validates SignalWire credentials are configured
        2. Verifies SWML handler is registered
        3. Creates a scoped guest token via SignalWire API
        4. Returns token and destination address

        The frontend uses this to initialize the SignalWire client and dial.
        """
        # Validate against the shipped voice lists before storing. `voice` is a
        # free-form query param that lands in the SWML `voice` field, so a bogus
        # value (or a stale id from a client's localStorage) made the call fail
        # at answer. Unknown values fall back to the default instead.
        if not is_known_voice(voice):
            logger.warning("Rejected unknown voice %r from /get_token; using default", voice)
            voice = DEFAULT_VOICE

        # Store selected voice in shared file (works across gunicorn workers)
        set_stored_voice(voice)
        print(f"Stored voice selection: {voice}", flush=True)

        client = get_rest_client()

        # Validate configuration
        if client is None:
            return JSONResponse(status_code=500, content={"error": "SignalWire credentials not configured (SIGNALWIRE_SPACE_NAME / SIGNALWIRE_PROJECT_ID / SIGNALWIRE_TOKEN)"})

        # Registration happens at startup, but retry lazily here so a
        # transient failure (or an extra worker) heals itself
        if not swml_handler_info.get("address_id"):
            with swml_setup_lock:
                if not swml_handler_info.get("address_id"):
                    setup_swml_handler()

        if not swml_handler_info.get("address_id"):
            reason = swml_setup_error or "unknown error - check server logs"
            return JSONResponse(status_code=500, content={"error": f"SWML handler not registered: {reason}"})

        try:
            # Create guest token with 24-hour expiry
            # Token is scoped to only allow calling our specific address
            expire_at = int(time.time()) + 3600 * 24  # 24 hours

            guest = client.fabric.tokens.create_guest_token(
                allowed_addresses=[swml_handler_info["address_id"]],
                expire_at=expire_at
            )
            guest_token = guest.get("token", "")

            # Bind the caller's voice pick to THIS token's guest identity, so the
            # SWML render for their call picks it up (see _guest_id_from_request)
            # rather than reading a file another caller may have overwritten.
            _gid = _guest_id_from_request({"address_uri": guest.get("address_uri", "")})
            if _gid:
                set_voice_for_guest(_gid, voice)
                logger.info("Bound voice %s to guest %s", voice, _gid[:18])

            # Return token and the address to dial
            return {
                "token": guest_token,
                "address": swml_handler_info["address"]
            }
        except Exception as e:
            logger.error(f"Token request failed: {e}")
            return JSONResponse(status_code=500, content={"error": str(e)})

    # ─────────────────────────────────────────────────────────────────────────
    # Debug Endpoint (optional - remove in production if desired)
    # ─────────────────────────────────────────────────────────────────────────
    @server.app.get("/get_resource_info")
    def get_resource_info():
        """Return SWML handler info for debugging."""
        return swml_handler_info

    # Add custom API routes for the web UI
    @server.app.get("/api/menu")
    async def get_menu():
        """Serve the menu data from backend"""
        return {"menu": MENU}

    # Serve static files using SDK's built-in method
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        server.serve_static_files(str(web_dir))

    # The SDK's static handler sends no Cache-Control at all, so browsers cache
    # the HTML shell heuristically. That shell carries the /app.js?v=N reference
    # AND the whole inline <style> theme block, so a stale copy kept serving old
    # JS/CSS no matter how many times the version was bumped (this bit us three
    # times). Make the shell always revalidate; let real assets stay cacheable.
    # Starlette's JSONResponse renders with ensure_ascii=False, so any non-ASCII
    # character (emoji, accents, curly quotes) makes the body's BYTE length
    # exceed its CHARACTER length. The SWAIG consumer reads by character count,
    # so the surplus bytes bleed into the next read and the whole turn fails with
    # webhook_fail/parse_error. Re-encoding with ensure_ascii=True sends the same
    # data as \uXXXX escapes - pure ASCII, byte length == char length - and the
    # receiving JSON parser decodes it back to the original characters.
    @server.app.middleware("http")
    async def _ascii_safe_json(request, call_next):
        response = await call_next(request)
        ctype = response.headers.get("content-type", "")
        if not ctype.startswith("application/json"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            escaped = json.dumps(json.loads(body), ensure_ascii=True,
                                 allow_nan=False, separators=(",", ":")).encode("ascii")
        except Exception:
            escaped = body        # not re-encodable: pass through untouched
        headers = dict(response.headers)
        headers.pop("content-length", None)   # let Starlette recompute it
        return Response(content=escaped, status_code=response.status_code,
                        headers=headers, media_type=ctype)

    @server.app.middleware("http")
    async def _cache_headers(request, call_next):
        response = await call_next(request)
        path = request.url.path
        is_shell = path in ("/", "/index.html") or path.endswith(".html")
        ctype = response.headers.get("content-type", "")
        if is_shell or ctype.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        elif path.endswith((".js", ".css")):
            # no-cache = keep the copy but ALWAYS revalidate against the ETag.
            # Cheap (304s when unchanged) and a forgotten ?v= bump can no longer
            # leave a browser running last week's script.
            response.headers["Cache-Control"] = "no-cache"
        elif path.endswith((".png", ".jpg", ".jpeg", ".mp4", ".woff2", ".json")):
            # Content-addressed by name here; a day of caching is plenty and the
            # ETag still forces a revalidate after that.
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        return response

    # ─────────────────────────────────────────────────────────────────────────
    # Startup: Register SWML handler
    # ─────────────────────────────────────────────────────────────────────────
    setup_swml_handler()

    return server


# Create server and expose app for WSGI/ASGI servers (Gunicorn, Uvicorn, etc.)
server = create_server()
app = server.app

if __name__ == "__main__":
    server.run()
